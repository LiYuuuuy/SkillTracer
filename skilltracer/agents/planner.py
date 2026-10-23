"""Create symbolic plans from grounded task context."""

from .base import Agent


class Planner(Agent):
    def __init__(self, llm, type_set, action_set):
        super().__init__(llm, "planner", type_set=type_set, action_set=action_set)

    def plan(
        self,
        *,
        query,
        subgoal,
        axtree,
        skill=None,
        past_plan=None,
        plan_status=True,
        executed_plan=None,
        history_plan=None,
        history_actions=None,
        active_memory=None,
        task_description="",
    ):
        has_skill = skill is not None and (
            not isinstance(skill, str) or skill.strip().lower() not in {"", "none", "null"}
        )
        return self.ask(
            "planner",
            query=query,
            subgoal=subgoal,
            axtree=axtree,
            reuse_instructions=self.build_prompt("planner_skill", skill=skill) if has_skill else "",
            reuse_fields='"use_skill":false,"skill_start_node_id":null,' if has_skill else "",
            past_plan=past_plan,
            plan_status=plan_status,
            executed_plan=executed_plan,
            history_plan=history_plan,
            history_actions=history_actions,
            active_memory=active_memory,
            task_description=task_description,
        )
