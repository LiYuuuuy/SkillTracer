"""Configure shared or role-specific OpenRouter model callables."""

from dataclasses import dataclass

from .config import DEFAULT_MODEL, completion_options, create_client

ROLES = ("subgoal_generator", "planner", "executor", "summarizer", "skill_inducer", "skill_reuser")


@dataclass
class LLMConfig:
    model: str = DEFAULT_MODEL
    temperature: float = 0.0
    max_output_tokens: int = 4096
    system_prompt: str | None = None


def build_llm(config):
    client = create_client()

    def complete(prompt):
        messages = []
        if config.system_prompt:
            messages.append({"role": "system", "content": config.system_prompt})
        messages.append({"role": "user", "content": prompt or ""})
        response = client.chat.completions.create(
            model=config.model,
            messages=messages,
            **completion_options(config.model, config.temperature, config.max_output_tokens),
        )
        content = response.choices[0].message.content
        if not content:
            reason = response.choices[0].finish_reason
            raise RuntimeError(
                f"Model returned no text (finish_reason={reason}). Check the output token budget."
            )
        return content

    return complete


def build_multi_llms(base):
    """Build the six active agent roles, or validate supplied model callables."""
    if base and all(callable(value) for value in base.values()):
        missing = set(ROLES) - set(base)
        if missing:
            raise ValueError(f"Missing model roles: {sorted(missing)}")
        return base
    default = base.get("default_model", DEFAULT_MODEL)
    models = base.get("models", {})
    temperatures = base.get("temperature", {})
    return {
        role: build_llm(
            LLMConfig(
                model=models.get(role, default),
                temperature=temperatures.get(role, 0.0),
                max_output_tokens=int(base.get("max_output_tokens", 4096)),
            )
        )
        for role in ROLES
    }
