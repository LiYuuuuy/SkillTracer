"""Record assets at their point of production without copying loaded banks."""

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from skilltracer.storage import fingerprint, utc_now, write_json

logger = logging.getLogger(__name__)


class TaskAssets:
    """Keep one task's produced assets and their domain-specific provenance."""

    FILES = (
        "progress.json",
        "skills.json",
        "active_memory.json",
        "short_term_memory.json",
        "long_term_memory.json",
        "subgoal_memory.json",
        "components.json",
        "events.jsonl",
        "model_calls.jsonl",
    )
    BANK_ASSETS = {
        "skills": "skills.json",
        "long_term_memory": "long_term_memory.json",
        "subgoal_memory": "subgoal_memory.json",
        "components": "components.json",
    }

    def __init__(self, store, task, memory, repositories, site, model, agents):
        self.store, self.task, self.memory = store, task, memory
        self.repositories, self.site, self.model, self.agents = repositories, site, model, agents
        attempt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex[:8]
        self.directory = store.directory / "assets" / store.task_id / attempt
        self.started_at = utc_now()
        self.status, self.evaluation, self.error = "running", None, None
        self.learning_committed = False
        self.bank_commits = {bank: False for bank in self.BANK_ASSETS}
        self.learning_decisions = []
        self.subgoals, self.current_plan, self.sequence = [], None, 0
        self.produced_skills = {}
        self.produced_long_term = []
        self.produced_components = {}
        self.journaled_entities = {bank: set() for bank in self.BANK_ASSETS}

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        for name in ("events.jsonl", "model_calls.jsonl"):
            (self.directory / name).touch()
        self.store.assets = self
        for agent in self.agents:
            agent.recorder = self.record_model_call
        self.checkpoint("task_started")
        logger.info("Task %s assets: %s", self.store.task_id, self.directory)
        logger.info("Domain learning state: %s", self.repositories.directory)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc is not None:
            self.status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
            self.error = {"type": exc_type.__name__, "message": str(exc)}
        try:
            self.checkpoint("task_finished", status=self.status, error=self.error)
        except Exception:
            if exc is None:
                raise
            logger.exception("Could not save final task assets to %s", self.directory)
        finally:
            self.store.assets = None
            for agent in self.agents:
                agent.recorder = None
        return False

    def add_subgoal(self, subgoal):
        self.subgoals.append(subgoal)
        self.current_plan = None
        self.checkpoint("subgoal_created", goal=subgoal.goal)

    def set_plan(self, current):
        self.current_plan = current
        self.checkpoint("plan_created", plan=current.plan.to_dict(), evidence=current.evidence)

    def record_model_call(self, role, template, prompt, response=None, error=None):
        data = {"role": role, "template": template, "response": response, "error": error}
        if self.store.debug:
            data["prompt"] = prompt
        self._append(
            "model_calls.jsonl",
            subgoal_index=len(self.subgoals) + 1
            if role == "subgoal_generator"
            else len(self.subgoals) or None,
            **data,
        )

    def checkpoint(self, event, **details):
        if event == "learning_decision":
            self.learning_decisions.append({"subgoal_index": len(self.subgoals), **details})
        elif event == "skill_induced":
            for skill in details["skills"]:
                self.produced_skills[skill["id"]] = skill
        elif event == "long_term_memory_created":
            self.produced_long_term.extend(details["entries"])
        elif event == "component_types_updated":
            for component in details["learned"]:
                self.produced_components[(component["purpose"], component["component_type"])] = (
                    component
                )
        self._append("events.jsonl", event=event, data=details)
        self.flush()

    def _append(self, filename, **payload):
        self.sequence += 1
        record = {
            "schema_version": 2,
            "domain": self.site,
            "sequence": self.sequence,
            "timestamp": utc_now(),
            "step": self.task.steps,
            "subgoal_index": len(self.subgoals) or None,
            **payload,
        }
        with (self.directory / filename).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _subgoal_experience(self):
        entries = []
        for goal, guidance in zip(self.task.subgoals, self.task.guidances):
            cleaned = [
                str(item).strip()
                for item in guidance
                if item is not None and str(item).strip().lower() not in {"", "null"}
            ]
            if cleaned:
                entries.append(
                    {"task": self.task.query, "subgoal": goal, "guidance": " ".join(cleaned)}
                )
        return entries

    def produced(self, bank):
        return {
            "skills": lambda: list(self.produced_skills.values()),
            "long_term_memory": lambda: list(self.produced_long_term),
            "subgoal_memory": self._subgoal_experience,
            "components": lambda: list(self.produced_components.values()),
        }[bank]()

    def record_bank_commit(self, bank):
        pending = [
            item
            for item in self.produced(bank)
            if fingerprint(item) not in self.journaled_entities[bank]
        ]
        self.repositories.record_commit(
            bank, self.store.task_id, self.directory / self.BANK_ASSETS[bank], pending
        )
        self.journaled_entities[bank].update(fingerprint(item) for item in pending)

    def flush(self):
        task = self.task
        write_json(
            self.directory / "skills.json",
            {"produced": self.produced("skills"), "induction_decisions": self.learning_decisions},
        )
        write_json(self.directory / "active_memory.json", self.memory.to_dict())
        write_json(
            self.directory / "short_term_memory.json",
            [
                {
                    "subgoal_index": index,
                    "goal": subgoal.goal,
                    "entries": [item.to_dict() for item in subgoal.failures],
                }
                for index, subgoal in enumerate(self.subgoals, 1)
            ],
        )
        for bank in ("long_term_memory", "subgoal_memory", "components"):
            write_json(self.directory / self.BANK_ASSETS[bank], {"produced": self.produced(bank)})
        write_json(
            self.directory / "progress.json",
            {
                "query": task.query,
                "maximum_steps": task.maximum_steps,
                "execution_steps": task.steps,
                "finished": task.finished,
                "committed_plan": task.plan.to_dict(),
                "committed_actions": task.actions,
                "completed_subgoals": task.subgoals,
                "subgoals": [
                    {
                        "goal": subgoal.goal,
                        "evidence": subgoal.evidence,
                        "terminal": subgoal.terminal,
                        "complete": subgoal.complete,
                        "plan": subgoal.plan.to_dict(),
                        "actions": subgoal.actions,
                        "guidances": subgoal.guidances,
                        "planning_rounds": subgoal.planning_rounds,
                        "planning_request": subgoal.planning_request,
                    }
                    for subgoal in self.subgoals
                ],
                "current_plan": None
                if self.current_plan is None
                else {
                    "plan": self.current_plan.plan.to_dict(),
                    "filled": self.current_plan.filled,
                    "rounds": self.current_plan.rounds,
                    "complete": self.current_plan.complete,
                },
            },
        )
        write_json(
            self.directory / "manifest.json",
            {
                "schema_version": 2,
                "site": self.site,
                "domain": self.site,
                "task_id": self.store.task_id,
                "model": self.model,
                "status": self.status,
                "started_at": self.started_at,
                "updated_at": utc_now(),
                "state_root": str(self.repositories.root),
                "state_directory": str(self.repositories.directory),
                "asset_policy": "produced_only",
                "prompts_recorded": self.store.debug,
                "learning_committed": self.learning_committed,
                "bank_commits": self.bank_commits,
                "evaluation": self.evaluation,
                "error": self.error,
                "files": {name: name for name in self.FILES},
            },
        )
