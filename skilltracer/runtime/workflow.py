"""Coordinate subgoals, planning, execution, repair, learning and evaluation."""

import json
import logging

from skilltracer.agents.executor import Executor
from skilltracer.agents.parsing import split_plan_and_evidence
from skilltracer.agents.planner import Planner
from skilltracer.agents.skills import SkillInducer, SkillReuser
from skilltracer.agents.subgoal import SubgoalGenerator
from skilltracer.agents.summarizer import InvalidAssessment, Summarizer
from skilltracer.browser.executor import BrowsergymExecutor
from skilltracer.browser.login import ACCOUNTS, login_site_with_env
from skilltracer.config import DEFAULT_MODEL, ROOT
from skilltracer.evaluation.evaluator import Evaluator
from skilltracer.memory.active import ActiveMemory
from skilltracer.memory.experience import ShortTermMemory
from skilltracer.planning.actions import ActionRegistry
from skilltracer.planning.checkpoints import CheckPointManager, RollBackPointer
from skilltracer.planning.plan import InvalidPlan, concat_plans
from skilltracer.planning.reuse import manage_skill_plan

from .assets import TaskAssets
from .execution import ExecutionService, InvalidExecution
from .learning import LearningService
from .repair import RepairService
from .repositories import Repositories
from .results import ArtifactStore, extract_response
from .state import PlanState, SubgoalState

logger = logging.getLogger(__name__)


class Workflow:
    def __init__(
        self,
        llms,
        task_id,
        domain,
        start_url,
        *,
        headless=True,
        results_dir=None,
        state_dir=None,
        evaluator_model=None,
        browser=None,
        evaluator=None,
        debug=False,
    ):
        self.task_id = str(task_id)
        self.site = domain
        self.start_url = start_url
        self.model = evaluator_model or DEFAULT_MODEL
        self.repositories = Repositories.open(state_dir or ROOT / ".local/state", self.site)
        actions = ActionRegistry.get_action_set()
        self.subgoal_generator = SubgoalGenerator(llms["subgoal_generator"])
        self.planner = Planner(
            llms["planner"], self.repositories.components.get_type_set(), actions
        )
        self.summarizer = Summarizer(llms["summarizer"])
        self.memory = ActiveMemory()
        self.artifacts = ArtifactStore(
            results_dir or ROOT / ".local/results",
            task_id,
            self.site,
            debug=debug,
        )
        self.browser = browser
        self.headless = headless
        self.evaluator = evaluator
        self.learning = LearningService(
            self.repositories,
            SkillReuser(llms["skill_reuser"]),
            SkillInducer(llms["skill_inducer"]),
            artifacts=self.artifacts,
        )
        self.executor = Executor(llms["executor"], actions)

    def run(self, task, maximum_steps=15):
        if maximum_steps <= 0:
            raise ValueError("maximum_steps must be positive.")
        state = self.repositories.load_task(task, maximum_steps)
        self.assets = TaskAssets(
            self.artifacts,
            state,
            self.memory,
            self.repositories,
            self.site,
            self.model,
            agents=(
                self.subgoal_generator,
                self.planner,
                self.executor,
                self.summarizer,
                self.learning.reuser,
                self.learning.inducer,
            ),
        )
        with self.assets:
            try:
                self._start_browser()
                for _ in range(11):
                    if state.finished or not state.budget_available:
                        break
                    subgoal = self._next_subgoal(state)
                    if subgoal is None:
                        break
                    self._run_subgoal(state, subgoal)
                    self.learning.finish_subgoal(state, subgoal)
                    state.finished = subgoal.complete and subgoal.terminal
                    self.artifacts.record("subgoal_finished", complete=subgoal.complete)
                return self._evaluate(state)
            finally:
                if self.browser is not None:
                    self.browser.close()

    def _start_browser(self):
        if self.browser is None:
            self.browser = BrowsergymExecutor(self.start_url, headless=self.headless)
        if self.site in ACCOUNTS and not login_site_with_env(self.browser, self.site):
            raise RuntimeError(f"Benchmark login failed for {self.site}.")
        succeeded, _, _ = self.browser.execute_browsergym_action(f"goto({self.start_url!r})")
        if not succeeded:
            raise RuntimeError(f"Could not open task URL: {self.start_url}")
        self.execution = ExecutionService(
            self.browser,
            self.executor,
            self.memory,
            ACCOUNTS.get(self.site, {"username": "", "password": ""}),
            self.artifacts,
        )
        self.rollback = RollBackPointer(self.start_url)
        self.repairs = RepairService(self.browser, self.memory, self.rollback, self.artifacts)

    def _next_subgoal(self, task):
        page = self.browser.get_page_state()
        goal, evidence, terminal = self.subgoal_generator.generate(
            task=task.query,
            history_subgoals=task.subgoals,
            history_plan=task.plan.to_symbolic(),
            history_actions=task.actions,
            subgoal_memory_text=task.subgoal_context,
            current_axtree=page["axtree_object"],
        )
        if not goal or goal.strip().lower() in {"none", "null"}:
            if terminal:
                task.finished = True
                return None
            raise ValueError("Subgoal generator returned no objective before task completion.")
        self.rollback.snapshot_subgoal_start(page["url"])
        logger.info("Subgoal: %s", goal)
        subgoal = SubgoalState(
            goal,
            evidence or page["axtree_object"],
            bool(terminal),
            observations=[page["axtree_object"]],
        )
        self.assets.add_subgoal(subgoal)
        subgoal.selected_skill = self.learning.select_skill(task, goal)
        selected_id = None
        if subgoal.selected_skill != "None":
            selected_id = json.loads(subgoal.selected_skill).get("id")
        self.artifacts.record("skill_selected", skill_id=selected_id)
        return subgoal

    def _run_subgoal(self, task, subgoal):
        for _ in range(21):
            if subgoal.complete or not task.budget_available:
                return
            try:
                current = self._plan(task, subgoal)
            except InvalidPlan as exc:
                self.artifacts.record("plan_rejected", error=str(exc))
                subgoal.last_plan_succeeded = False
                subgoal.planning_request = (
                    f"Return a valid plan with unique node IDs and boolean completion fields: {exc}"
                )
                continue
            self.assets.set_plan(current)
            if not current.plan.get("nodes", []):
                subgoal.plan = concat_plans(subgoal.plan, current.plan)
            self._execute_plan(task, subgoal, current)
            if current.complete:
                subgoal.complete = not current.plan.get("next_step_decision", {}).get(
                    "should_plan_next", True
                )

    def _plan(self, task, subgoal):
        page = self.browser.get_page_state()
        # The original workflow retained guidance from the final planning round only.
        subgoal.guidances = []
        first_round = subgoal.planning_rounds == 0
        subgoal.planning_rounds += 1
        response = self.planner.plan(
            query=task.query,
            subgoal=subgoal.goal,
            axtree=subgoal.evidence if first_round else page["axtree_object"],
            skill=subgoal.selected_skill,
            past_plan=subgoal.past_plan.to_symbolic(),
            plan_status=subgoal.last_plan_succeeded,
            executed_plan=subgoal.plan.to_symbolic(),
            history_plan=task.plan.to_symbolic(),
            history_actions=task.actions,
            active_memory=self.memory.to_prompt(),
            task_description=subgoal.planning_request,
        )
        text, evidence = split_plan_and_evidence(response)
        decision = manage_skill_plan(
            text or response,
            concat_plans(task.plan, subgoal.plan),
            subgoal.selected_skill,
        )
        if decision.planner_node_mapping or decision.skipped_prefix_node_ids:
            self.artifacts.record(
                "plan_node_ids_normalized",
                mapping=decision.planner_node_mapping,
                skipped_prefix_node_ids=decision.skipped_prefix_node_ids,
            )
        self.artifacts.record(
            "skill_reuse_checked",
            requested=decision.requested,
            skill_id=decision.skill_id,
            replaced=decision.replaced,
            reason=decision.reason,
            node_mapping=decision.node_mapping,
            skill_start_node_id=decision.skill_start_node_id,
        )
        plan = decision.plan
        return PlanState(
            plan, evidence or page["axtree_object"], complete=not plan.get("nodes", [])
        )

    def _execute_plan(self, task, subgoal, current):
        checkpoints = CheckPointManager(current.plan.to_dict())
        while not current.complete and task.budget_available:
            try:
                batch = self.execution.execute(task, subgoal, current)
            except InvalidExecution as exc:
                self.artifacts.record("execution_rejected", error=str(exc))
                subgoal.last_plan_succeeded = False
                subgoal.past_plan = current.plan
                subgoal.planning_request = (
                    f"Replan without skipping pending nodes; previous binding was invalid: {exc}"
                )
                return
            self.artifacts.record(
                "execution_batch", filled=batch.filled, node_ids=batch.node_ids, errors=batch.errors
            )
            last_id = (
                batch.node_ids[-1]
                if batch.node_ids
                else next(
                    (
                        node["node_id"]
                        for node in reversed(subgoal.plan.get("nodes", []))
                        if node["node_id"] in checkpoints.node_ids
                    ),
                    None,
                )
            )
            checkpoint = checkpoints.checkpoints[
                checkpoints.node_ids.index(last_id) + 1 if last_id else 0
            ]
            try:
                assessment = self.summarizer.summarize(
                    query=task.query,
                    subgoal=subgoal.goal,
                    plan=current.plan.to_symbolic(),
                    delta_axtree_traj=self.execution.describe(batch, checkpoints),
                    executed_actions=batch.filled,
                    executed_actions_list=subgoal.actions,
                    executed_nodes_list=subgoal.plan.to_symbolic(),
                    active_memory=self.memory.to_prompt(),
                    short_term_memory=ShortTermMemory.to_prompt(subgoal.failures),
                    long_term_memory=task.long_term_context,
                    checkpoints=checkpoints,
                    last_checkpoint=checkpoint,
                    action_errors=batch.errors,
                )
                resolution = getattr(self.summarizer, "last_node_resolution", None)
                if isinstance(resolution, dict):
                    self.artifacts.record("repair_node_resolved", **resolution)
                self.artifacts.record("assessment", assessment=assessment.to_dict())
                subgoal.guidances.append(assessment.guidance)
                if assessment.plan_success:
                    current.complete = assessment.checkpoint == checkpoints.checkpoints[-1]
                    self.repairs.commit(subgoal, current, batch)
                    subgoal.past_plan = current.plan
                    subgoal.last_plan_succeeded = True
                    subgoal.planning_request = (
                        "Continue after the committed nodes if this subgoal needs more actions."
                    )
                    self.artifacts.record("progress_committed")
                else:
                    continue_plan = self.repairs.apply(task, subgoal, current, batch, assessment)
                    self.artifacts.record("repair_applied", continue_plan=continue_plan)
                    if not continue_plan:
                        return
            except InvalidAssessment as exc:
                logger.warning("Rejected model assessment: %s", exc)
                self.artifacts.record("assessment_rejected", error=str(exc))
                subgoal.last_plan_succeeded = False
                subgoal.past_plan = current.plan
                subgoal.planning_request = (
                    f"Replan using observed page state; previous assessment was invalid: {exc}"
                )
                return

    def _evaluate(self, task):
        if self.evaluator is None:
            self.evaluator = Evaluator(self.model)
        captions = self.browser.get_page_state()["axtree_object"]
        if self.memory.to_prompt():
            captions += "\nGrounded memory:\n" + self.memory.to_prompt()
        evaluation, prompt = self.evaluator(
            {
                "captions": captions,
                "intent": task.query,
                "response": extract_response(task.actions),
                "actions": task.actions,
                "task_id": self.task_id,
                "site": self.site,
            }
        )
        evaluation["model"] = self.model
        self.assets.evaluation = evaluation
        self.artifacts.record("task_evaluated", evaluation=evaluation)
        outcome = self.artifacts.save(task, self.site, evaluation, prompt)
        if evaluation["status"] == "success":
            self.repositories.persist_success(task, on_commit=self.artifacts.committed)
            self.assets.learning_committed = True
        self.artifacts.save_trajectory(outcome)
        self.assets.status = evaluation["status"]
        logger.info("Task %s: %s", self.task_id, evaluation["status"])
        return outcome
