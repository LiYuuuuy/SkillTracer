"""Preserve subgoal learning gates and the distinct bank commit policies."""

import json

from skilltracer.memory.skills import SkillRepository
from skilltracer.planning.plan import concat_plans


class LearningService:
    def __init__(self, repositories, reuser, inducer, top_k=3, artifacts=None):
        self.repositories = repositories
        self.reuser = reuser
        self.inducer = inducer
        self.top_k = top_k
        self.artifacts = artifacts

    def select_skill(self, task, goal):
        if not task.skills:
            return "None"
        matches = SkillRepository.match_by_subgoal(task.skills, goal, self.top_k)
        if not matches:
            return "None"
        selected, skill_id = self.reuser.reuse(
            task.query,
            goal,
            matches,
        )
        if selected:
            found, skill = self.repositories.skills.get_skill_by_id(matches, skill_id)
            if found:
                return json.dumps(skill, ensure_ascii=False, indent=2)
        return "None"

    def finish_subgoal(self, task, subgoal):
        # Keep verified progress even when the execution budget is exhausted.
        task.plan = concat_plans(task.plan, subgoal.plan)
        task.actions.extend(subgoal.actions)
        if subgoal.complete:
            task.subgoals.append(subgoal.goal)
            task.guidances.append(list(subgoal.guidances))

        node_count = len(subgoal.plan.get("nodes", []))
        blocked = []
        if not task.budget_available:
            blocked.append("step_budget_exhausted")
        if subgoal.planning_rounds > 20:
            blocked.append("planning_retry_limit_exceeded")
        induce_skill = not blocked and node_count >= 2
        if self.artifacts is not None:
            self.artifacts.record(
                "learning_decision",
                goal=subgoal.goal,
                complete=subgoal.complete,
                verified_nodes=node_count,
                execution_steps=task.steps,
                maximum_steps=task.maximum_steps,
                planning_rounds=subgoal.planning_rounds,
                induce_skill=induce_skill,
                skill_skip_reasons=blocked
                + (["verified_plan_too_short"] if node_count < 2 else []),
                update_components=not blocked,
                component_skip_reasons=list(blocked),
            )
        if blocked:
            return
        if induce_skill:
            response = self.inducer.induce_skill(
                query=task.query,
                subgoal=subgoal.goal,
                executed_plan=subgoal.plan.to_symbolic(),
                reused_skill=subgoal.selected_skill,
            )
            skill = SkillRepository.parse_skill_json_from_triple_skill(response)
            before = {item["id"]: item for item in task.candidate_skills}
            task.candidate_skills = SkillRepository.upsert_skill(task.candidate_skills, skill)
            if self.artifacts is not None:
                self.artifacts.record(
                    "skill_induced",
                    goal=subgoal.goal,
                    source_plan=subgoal.plan.to_dict(),
                    skills=[
                        item for item in task.candidate_skills if before.get(item["id"]) != item
                    ],
                )
        learned_types = []
        for node in subgoal.plan.get("nodes", []):
            purpose, component_type = node.get("purpose"), node.get("component_type")
            if (
                purpose
                and component_type
                and self.repositories.components.update(purpose, component_type)
            ):
                learned_types.append({"purpose": purpose, "component_type": component_type})
        # Component discoveries survive a later task failure, as in the original workflow.
        if self.artifacts is not None:
            self.artifacts.record("component_types_updated", learned=learned_types)
        self.repositories.components.write_back()
        if self.artifacts is not None:
            self.artifacts.committed("components")
