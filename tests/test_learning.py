"""Compare learning transitions with fixtures executed from pre-refactor code."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skilltracer.memory.experience import LongTermMemory, ShortTermMemory
from skilltracer.memory.subgoals import SubgoalMemory
from skilltracer.planning.plan import Plan
from skilltracer.runtime.learning import LearningService
from skilltracer.runtime.repositories import Repositories
from skilltracer.runtime.state import SubgoalState

REFERENCE = json.loads((Path(__file__).parent / "fixtures/learning_reference.json").read_text())


class LearningReferenceTests(unittest.TestCase):
    def test_skill_and_type_gates_match_36_original_code_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, case in enumerate(REFERENCE["learning_gates"]):
                with self.subTest(**case):
                    repositories = Repositories.open(Path(directory) / str(index), "map")
                    task = repositories.load_task("Reference task", case["maximum_steps"])
                    task.steps = case["steps"]
                    nodes = [
                        {
                            "node_id": f"n{node + 1}",
                            "purpose": "navigation",
                            "component_type": "reference_panel",
                            "action": "click(target_id)",
                            "description": "Use the observed target.",
                        }
                        for node in range(case["nodes"])
                    ]
                    subgoal = SubgoalState(
                        "Reference subgoal",
                        "",
                        True,
                        plan=Plan(
                            {
                                "nodes": nodes,
                                "edges": [],
                                "next_step_decision": {"should_plan_next": False},
                            }
                        ),
                        planning_rounds=case["planning_rounds"],
                        complete=True,
                    )
                    inducer = Mock()
                    inducer.induce_skill.return_value = (
                        "'''skill\n"
                        + json.dumps(
                            {
                                "id": "reference_skill",
                                "name": "Reference skill",
                                "description": "Reuse the target interaction",
                                "plan": subgoal.plan.to_dict(),
                            }
                        )
                        + "\n'''"
                    )
                    initial_skills = len(task.candidate_skills)
                    with patch.object(
                        repositories.components,
                        "write_back",
                        wraps=repositories.components.write_back,
                    ) as write:
                        LearningService(repositories, Mock(), inducer).finish_subgoal(task, subgoal)
                    self.assertEqual(inducer.induce_skill.called, case["induce_skill"])
                    self.assertEqual(write.called, case["write_components"])
                    self.assertEqual(
                        len(task.candidate_skills), initial_skills + int(case["induce_skill"])
                    )

    def test_short_and_long_term_transitions_match_original_objects(self):
        short_term, long_term = [], []
        for case in REFERENCE["memory_transitions"]:
            with self.subTest(transition=case["label"]):
                if case["freeze_previous"]:
                    short_term[-1].set_editable(False)
                if case["is_error"]:
                    LongTermMemory.update_long_term_memory_on_error_transition(
                        long_term,
                        task="Reference task",
                        subgoal="Reference subgoal",
                        short_term_memories=short_term,
                        progressed_nodes=case["progressed_nodes"],
                        success_repair_tool=case["repair_tool"],
                        success_repair_insight=case["insight"],
                    )
                    ShortTermMemory.update_short_term_memories_on_error(
                        short_term,
                        progressed_nodes=case["progressed_nodes"],
                        failed_node=case["failed_node"],
                        repair_tool=case["repair_tool"],
                        insight=case["insight"],
                        error_evidence=case["error_evidence"],
                    )
                self.assertEqual(
                    [item.to_dict() for item in short_term], case["expected_short_term"]
                )
                self.assertEqual([item.to_dict() for item in long_term], case["expected_long_term"])
                prompt = ShortTermMemory.to_prompt(short_term)
                for index, item in enumerate(short_term):
                    self.assertEqual(f"[SHORT_TERM_MEMORY_{index}]" in prompt, item.usable)

    def test_subgoal_guidance_filtering_and_merge_match_original_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubgoalMemory(str(Path(directory) / "subgoals.txt"))
            entries = []
            for case in REFERENCE["subgoal_memory_updates"]:
                entries = repository.write_back(
                    entries, case["query"], case["subgoals"], case["guidances"]
                )
                self.assertEqual(entries, case["expected"])

    def test_new_long_term_experience_is_visible_to_the_next_task(self):
        with tempfile.TemporaryDirectory() as directory:
            repositories = Repositories.open(directory, "map")
            task = repositories.load_task("Current task", 4)
            context_at_start = task.long_term_context
            task.long_term.append(
                LongTermMemory(
                    "New experience",
                    "Find target",
                    {},
                    "Missing target",
                    [],
                    {"insight": "Locate first"},
                )
            )
            self.assertEqual(task.long_term_context, context_at_start)
            self.assertNotIn("New experience", task.long_term_context)
            repositories.persist_success(task)
            next_task = repositories.load_task("Next task", 4)
            self.assertIn("New experience", next_task.long_term_context)


if __name__ == "__main__":
    unittest.main()
