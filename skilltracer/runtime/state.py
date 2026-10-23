"""Explicit task, subgoal and plan state shared by runtime services."""

from dataclasses import dataclass, field

from skilltracer.memory.active import MemoryWrite
from skilltracer.planning.plan import Plan


@dataclass
class TaskState:
    query: str
    maximum_steps: int
    skills: list
    candidate_skills: list
    long_term: list
    subgoal_entries: list
    subgoal_context: str
    long_term_context: str = ""
    steps: int = 0
    plan: Plan = field(default_factory=Plan.init_empty)
    actions: list[str] = field(default_factory=list)
    subgoals: list[str] = field(default_factory=list)
    guidances: list = field(default_factory=list)
    finished: bool = False

    @property
    def budget_available(self):
        return self.steps < self.maximum_steps


@dataclass
class SubgoalState:
    goal: str
    evidence: str
    terminal: bool
    selected_skill: str = "None"
    plan: Plan = field(default_factory=Plan.init_empty)
    actions: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    failures: list = field(default_factory=list)
    guidances: list = field(default_factory=list)
    past_plan: Plan = field(default_factory=Plan.init_empty)
    last_plan_succeeded: bool = True
    planning_request: str = "Continue from the current page without repeating committed nodes."
    planning_rounds: int = 0
    complete: bool = False


@dataclass
class PlanState:
    plan: Plan
    evidence: str
    filled: str | None = None
    rounds: int = 0
    complete: bool = False


@dataclass
class ExecutionBatch:
    filled: str
    context: str
    actions: list[str]
    node_ids: list[str]
    browser_actions: list[str]
    browser_node_ids: list[str]
    memory_pairs: list[tuple[str, str]]
    observations: list[dict]
    browser_observations: list[dict]
    errors: list[str]
    memory_changes: list[MemoryWrite | tuple[str, str, str | None]] | None = None
    edge_results: list[dict] = field(default_factory=list)
