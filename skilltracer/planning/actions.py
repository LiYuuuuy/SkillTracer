import ast
import re
from typing import List, Tuple


class ActionRegistry:
    """
    Central registry for primitive actions.
    """

    @staticmethod
    def get_action_set() -> str:
        """Return the supported primitive signatures and grounding rules."""
        return """click(bid, button='left', modifiers=[]): click a visible element.
hover(bid): reveal hover controls.
fill(bid, value): replace a textbox, combobox or editable field value.
press(bid, key): press a key such as Enter or Control+a.
select_option(bid, options): choose a combobox option by its text or value.
goto(url), go_back(), go_forward(): navigate using a grounded URL or history.
new_tab(), tab_focus(index), tab_close(): manage browser tabs.
memorize(bid): retain the visible subtree; use 'Null' when none is relevant.
send_msg_to_user(text): return an answer grounded in observed evidence.
report_infeasible(reason): explain why the task cannot be completed.
Element IDs are strings copied from the current AXTree, not labels or selectors.
String arguments must use valid Python quoting and escaping."""

    @staticmethod
    def primitive_name(action):
        """Validate one supported primitive call without evaluating its arguments."""
        try:
            expression = ast.parse(action, mode="eval").body
        except (SyntaxError, TypeError) as exc:
            raise ValueError("An action must be one valid primitive call.") from exc
        supported = set(re.findall(r"\b([a-z_]+)\(", ActionRegistry.get_action_set()))
        if (
            not isinstance(expression, ast.Call)
            or not isinstance(expression.func, ast.Name)
            or expression.func.id not in supported
        ):
            raise ValueError("An action must use a supported primitive.")
        return expression.func.id

    @staticmethod
    def _extract_plan_component_entries(executor_text: str) -> List[str]:
        """
        Extract triple-quoted entries inside <plan components>: ... block.
        """
        m = re.search(
            r"<plan components>:\s*(.*?)(?:\n<context>:|\Z)",
            executor_text,
            flags=re.DOTALL,
        )
        if not m:
            return []
        block = m.group(1)
        return re.findall(r"'''(.*?)'''", block, flags=re.DOTALL)

    @staticmethod
    def _is_condition_payload(payload: str) -> bool:
        """
        Condition examples:
          - c: exists_content('Search') = true
          - exists_component(...) = true
          - exists_content(...) = false
          - exists(...) = true
        """
        p = payload.strip()

        # explicit c: line
        if p.startswith("c:"):
            return True

        # "exists_*(...)=true/false" or "exists(...)=true/false"
        if re.match(r"^exists(_component|_content)?\s*\(.*\)\s*=\s*(true|false)$", p, flags=re.I):
            return True

        return False

    @staticmethod
    def _parse_node_and_payload(line: str) -> Tuple[str, str]:
        """
        Parse "n12: click('147')" -> ("n12", "click('147')")
        If no node prefix, return ("", original_line).
        """
        s = line.strip()
        m = re.match(r"^(n\d+)\s*:\s*(.*)$", s)
        if m:
            return m.group(1), m.group(2).strip()
        return "", s

    @staticmethod
    def executor_output_to_actions(
        executor_text: str,
    ) -> Tuple[List[str], List[str], List[str], List[str]]:
        """
        Return:
          - action_list, action_node_ids         (exclude memorize)
          - memory_action_list, memory_node_ids  (memorize only)

        Notes:
          - Only parse entries inside <plan components>: block
          - Ignore condition lines (c:..., exists_*(...)=true/false)
        """
        entries = ActionRegistry._extract_plan_component_entries(executor_text)
        if not entries:
            return [], [], [], []

        action_list: List[str] = []
        action_node_ids: List[str] = []
        memory_action_list: List[str] = []
        memory_node_ids: List[str] = []

        for raw in entries:
            raw = raw.strip()
            if not raw:
                continue

            node_id, payload = ActionRegistry._parse_node_and_payload(raw)

            # ignore condition checks
            if ActionRegistry._is_condition_payload(payload):
                continue

            # keep only code-like "foo(...)" lines
            if "(" not in payload or not payload.endswith(")"):
                continue

            if payload.startswith("memorize("):
                memory_action_list.append(payload)
                memory_node_ids.append(node_id)
            else:
                action_list.append(payload)
                action_node_ids.append(node_id)

        return action_list, action_node_ids, memory_action_list, memory_node_ids

    @staticmethod
    def executor_output_to_full_actions(executor_text: str) -> Tuple[List[str], List[str]]:
        """
        Return:
          - action_list_full: all executable actions including memorize(...)
          - node_id_list_full: aligned node ids

        Notes:
          - Only parse entries inside <plan components>: block
          - Ignore condition lines (c:..., exists_*(...)=true/false)
        """
        entries = ActionRegistry._extract_plan_component_entries(executor_text)
        if not entries:
            return [], []

        action_list_full: List[str] = []
        node_id_list_full: List[str] = []

        for raw in entries:
            raw = raw.strip()
            if not raw:
                continue

            node_id, payload = ActionRegistry._parse_node_and_payload(raw)

            if ActionRegistry._is_condition_payload(payload):
                continue

            if "(" not in payload or not payload.endswith(")"):
                continue

            action_list_full.append(payload)
            node_id_list_full.append(node_id)

        return action_list_full, node_id_list_full

    @staticmethod
    def parse_memorize_bids(memory_action_list: List[str]) -> List[str]:
        """
        Clean and extract bid from memorize(...) actions.

        Input examples:
            ["memorize('157')", "memorize('a32')", "memorize('Null')"]
        Output:
            ["157", "a32"]
        """

        bids: List[str] = []

        for s in memory_action_list:
            s = s.strip()

            # memorize('157') / memorize("157") / memorize(157) / memorize(Null)
            m = re.match(r"^memorize\(\s*(['\"])?([^'\")\s]+)\1?\s*\)\s*$", s)
            if not m:
                continue

            bid = m.group(2).strip()

            # ---- NEW: explicitly ignore Null ----
            if bid.lower() == "null":
                continue

            bids.append(bid)

        return bids
