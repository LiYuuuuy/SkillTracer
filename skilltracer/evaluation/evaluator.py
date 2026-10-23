"""Evaluate a completed task with the configured text model."""

import re

from skilltracer.config import completion_options, create_client

SYSTEM_PROMPT = """Evaluate a browser agent using the task, actual actions, final
page and grounded memory. For information requests, the agent response must
contain the requested information or explicitly state that it is unavailable.
Page evidence alone is insufficient; check that all necessary actions were taken.
For navigation
and modification requests, assess the requested final page or state; a separate
response is not required. Do not infer success from an intended action alone.
Return exactly two lines:
Thoughts: <evidence-based reasoning>
Status: success|failure"""


class Evaluator:
    def __init__(self, model, client=None):
        self.model = model
        self.client = client or create_client()

    def __call__(self, info):
        actions = "\n".join(
            f"{index + 1}: {action}" for index, action in enumerate(info["actions"])
        )
        prompt = (
            f"Task: {info['intent']}\nActions:\n{actions}\n"
            f"Final page and memory:\n{info['captions']}\n"
            f"Agent response: {info['response'] or 'None'}"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            **completion_options(self.model, 0.0, 4096),
        )
        text = response.choices[0].message.content or ""
        status = re.search(r'(?im)^Status:\s*["\x27]?(success|failure)\b', text)
        if not status:
            raise ValueError("Evaluator returned no valid success/failure status.")
        thoughts = re.search(r"(?ims)^Thoughts:\s*(.*?)\nStatus:", text)
        return {
            "thoughts": thoughts.group(1).strip() if thoughts else "",
            "status": status.group(1).lower(),
        }, prompt
