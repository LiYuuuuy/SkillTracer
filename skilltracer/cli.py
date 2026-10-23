"""One command-line interface for single tasks, ranges and site batches."""

import argparse
import json
import logging
import random
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_MODEL, ROOT, SITE_URLS
from .tasks import TaskRepository

logger = logging.getLogger(__name__)


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Value must be positive.")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description="Run SkillTracer browser experiments.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task-id", nargs="+", type=int, help="One or more task IDs.")
    selection.add_argument("--task-range", nargs=2, type=int, metavar=("FIRST", "LAST"))
    selection.add_argument("--site", choices=SITE_URLS, help="Run single-site tasks for this site.")
    parser.add_argument("--task-dir", type=Path, help="Override the bundled task definitions.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-steps", type=positive_int, default=15)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=ROOT / ".local/state",
        help="State root; banks are isolated in one subdirectory per domain.",
    )
    parser.add_argument("--max-output-tokens", type=positive_int, default=4096)
    parser.add_argument("--limit", type=positive_int)
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument(
        "--debug", action="store_true", help="Save AXTree snapshots and full agent prompts."
    )
    return parser


def select_tasks(args, repository):
    if args.task_id is not None:
        ids = list(dict.fromkeys(args.task_id))
    elif args.task_range is not None:
        first, last = args.task_range
        if first > last:
            raise ValueError("Task range must be in ascending order.")
        ids = list(range(first, last + 1))
    else:
        ids = repository.for_site(args.site)
    unknown = sorted(set(ids) - set(repository.ids()))
    if unknown:
        raise ValueError(f"Unknown task IDs: {unknown}")
    if args.shuffle:
        random.Random(args.seed).shuffle(ids)
    return ids[: args.limit] if args.limit else ids


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    repository = TaskRepository(args.task_dir)
    try:
        ids = select_tasks(args, repository)
    except ValueError as exc:
        parser.error(str(exc))
    if not ids:
        parser.error("No tasks match the selection.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("skilltracer").setLevel(logging.DEBUG if args.debug else logging.INFO)
    from .llm import build_multi_llms
    from .runtime.workflow import Workflow

    results = args.results_dir or ROOT / ".local/runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    results.mkdir(parents=True, exist_ok=True)
    logger.info("Results directory: %s", results.resolve())
    logger.info("Reusable learning state: %s", args.state_dir.resolve())
    llms = None
    summary = {"completed": 0, "success": 0, "failure": 0, "errors": 0, "skipped": 0}
    for task_id in ids:
        workflow = None
        domain = None
        try:
            task = repository.load(task_id)
            domain = task.site
            if args.skip_existing and (results / domain / f"{task_id}.json").exists():
                summary["skipped"] += 1
                continue
            if llms is None:
                llms = build_multi_llms(
                    {
                        "default_model": args.model,
                        "max_output_tokens": args.max_output_tokens,
                    }
                )
            workflow = Workflow(
                llms,
                task_id,
                task.site,
                task.start_url,
                headless=not args.headful,
                results_dir=results,
                state_dir=args.state_dir,
                evaluator_model=args.model,
                debug=args.debug,
            )
            outcome = workflow.run(task.intent, maximum_steps=args.max_steps)
            summary["completed"] += 1
            summary[outcome["evaluation"]["status"]] += 1
        except Exception as exc:
            logger.exception("Task %s failed", task_id)
            summary["errors"] += 1
            errors = (
                results / domain / "errors" if domain is not None else results / "unresolved_errors"
            )
            errors.mkdir(parents=True, exist_ok=True)
            assets = getattr(workflow, "assets", None)
            (errors / f"{task_id}.json").write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "error": str(exc),
                        "assets_dir": str(assets.directory) if assets is not None else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    print(
        json.dumps(
            {
                "results_dir": str(results.resolve()),
                "state_dir": str(args.state_dir.resolve()),
                **summary,
            },
            indent=2,
        )
    )
    return 1 if summary["errors"] else 0
