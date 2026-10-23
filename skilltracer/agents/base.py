"""Shared prompt rendering and model invocation."""

from importlib.resources import files
from string import Template


class Agent:
    def __init__(self, llm, role, **constants):
        self.llm = llm
        self.role = role
        self.constants = constants
        self.recorder = None

    def build_prompt(self, template, **context):
        text = (
            files("skilltracer.agents")
            .joinpath("prompts", template + ".md")
            .read_text(encoding="utf-8")
        )
        values = {**self.constants, **context}
        return Template(text).substitute(
            {key: "" if value is None else str(value) for key, value in values.items()}
        )

    def ask(self, template, **context):
        prompt = self.build_prompt(template, **context)
        try:
            response = self.llm(prompt)
        except Exception as exc:
            if self.recorder is not None:
                self.recorder(
                    self.role,
                    template,
                    prompt,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            raise
        if self.recorder is not None:
            self.recorder(self.role, template, prompt, response=response)
        return response
