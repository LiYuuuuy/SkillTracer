"""Persist task results, trajectories and optional AXTree diagnostics."""

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

from skilltracer.storage import domain_directory


def extract_response(actions):
    responses = []
    for action in actions:
        if not isinstance(action, str):
            continue
        try:
            call = ast.parse(action.strip(), mode="eval").body
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                parameters = {"send_msg_to_user": "text", "report_infeasible": "reason"}
                if call.func.id not in parameters:
                    continue
                if len(call.args) == 1 and not call.keywords:
                    argument = call.args[0]
                elif (
                    not call.args
                    and len(call.keywords) == 1
                    and call.keywords[0].arg == parameters[call.func.id]
                ):
                    argument = call.keywords[0].value
                else:
                    continue
                message = ast.literal_eval(argument)
                if isinstance(message, str) and message.strip():
                    responses.append(message.strip())
        except (SyntaxError, ValueError, TypeError):
            continue
    return "\n".join(responses)


class ArtifactStore:
    def __init__(self, directory, task_id, domain, debug=False):
        self.root = Path(directory).expanduser().resolve()
        self.directory = domain_directory(self.root, domain)
        self.domain = domain
        self.task_id = str(task_id)
        self.debug = debug
        self.assets = None

    def record(self, event, **details):
        if self.assets is not None:
            self.assets.checkpoint(event, **details)

    def committed(self, bank):
        if self.assets is not None:
            self.assets.bank_commits[bank] = True
            self.assets.record_bank_commit(bank)
            self.record("bank_committed", bank=bank)

    def snapshot(self, axtree, tag):
        if not self.debug:
            return
        path = self.directory / "debug" / f"{self.task_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "tag": tag,
                        "axtree": axtree,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def save(self, task, site, evaluation, prompt):
        self.directory.mkdir(parents=True, exist_ok=True)
        result_path = self.directory / f"{self.task_id}.json"
        payload = {
            "task_id": int(self.task_id),
            "domain": site,
            "raw": [evaluation, prompt],
            "assets_dir": str(self.assets.directory) if self.assets is not None else None,
        }
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        outcome = {
            "task": task.query,
            "evaluation": evaluation,
            "result_path": str(result_path),
            "assets_dir": payload["assets_dir"],
            "subgoals": task.subgoals,
            "executed_plan": task.plan.to_symbolic(),
            "executed_actions": task.actions,
            "execution_steps": task.steps,
            "finished": task.finished,
        }
        return outcome

    def save_trajectory(self, outcome):
        trajectory_dir = self.directory / "trajectories"
        trajectory_dir.mkdir(exist_ok=True)
        (trajectory_dir / f"{self.task_id}.json").write_text(
            json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
