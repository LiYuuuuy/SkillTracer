"""Choose existing skills and abstract successful procedures."""

from .base import Agent


class SkillInducer(Agent):
    def __init__(self, llm):
        super().__init__(llm, "skill_inducer")

    def induce_skill(self, *, query, subgoal, executed_plan, reused_skill=None):
        return self.ask(
            "skill",
            query=query,
            subgoal=subgoal,
            executed_plan=executed_plan,
            reused_skill=reused_skill,
        )


class SkillReuser(Agent):
    def __init__(self, llm):
        super().__init__(llm, "skill_reuser")

    def reuse(self, task_goal, subtask_goal, skills):
        output = self.ask("reuse", task_goal=task_goal, subtask_goal=subtask_goal, skills=skills)
        matched, skill_id = None, None
        for raw in output.splitlines():
            line = raw.strip()
            if line.lower().startswith("match:"):
                value = line.split(":", 1)[1].strip().lower()
                matched = value == "true" if value in {"true", "false"} else None
            elif line.lower().startswith("id:"):
                value = line.split(":", 1)[1].strip()
                skill_id = None if value.lower() == "null" else value
        if matched is None:
            matched = bool(skill_id)
        return matched, skill_id if matched else None
