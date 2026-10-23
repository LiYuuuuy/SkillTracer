"""Bind a symbolic plan to visible browser elements."""

import json

from .base import Agent


class Executor(Agent):
    def __init__(self, llm, action_set):
        super().__init__(llm, "executor", action_set=action_set)

    def execute(
        self,
        *,
        query,
        subgoal,
        axtree,
        memory,
        account_information,
        plan_to_process,
        filled_plan=None,
    ):
        try:
            graph = json.loads(plan_to_process)
            if "edges" in graph:
                graph.pop("edges")
                plan_to_process = json.dumps(graph, ensure_ascii=False, indent=2)
            example_node_id = next(
                (
                    node["node_id"]
                    for node in graph["nodes"]
                    if node["node_id"] not in graph.get("committed_node_ids", [])
                ),
                "<node_id>",
            )
        except (ValueError, TypeError, KeyError):
            example_node_id = "<node_id>"
        return self.ask(
            "executor",
            query=query,
            subgoal=subgoal,
            axtree=axtree,
            memory=memory,
            account_information=account_information,
            plan_to_process=plan_to_process,
            filled_plan=filled_plan,
            example_node_id=example_node_id,
        )
