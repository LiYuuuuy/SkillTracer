"""Shared configuration for local runs; environment variables override .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(os.getenv("SKILLTRACER_HOME", str(Path.cwd()))).resolve()
load_dotenv(ROOT / ".env", override=False)

# A user-local fallback for minimal WSL installations without sudo access.
_browser_libs = ROOT / ".local/browser-deps/usr/lib/x86_64-linux-gnu"
if _browser_libs.is_dir():
    os.environ["LD_LIBRARY_PATH"] = str(_browser_libs) + (
        ":" + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else ""
    )

DATA_DIR = Path(__file__).resolve().parent / "data"

DEFAULT_MODEL = os.getenv("LLM_MODEL", "openai/gpt-6-luna-pro")
BROWSER_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "60000"))
SITE_URLS = {
    "shopping": os.getenv("WA_SHOPPING", "http://localhost:7770").rstrip("/"),
    "shopping_admin": os.getenv("WA_SHOPPING_ADMIN", "http://localhost:7780").rstrip("/"),
    "reddit": os.getenv("WA_REDDIT", "http://localhost:9999").rstrip("/"),
    "gitlab": os.getenv("WA_GITLAB", "http://localhost:8023").rstrip("/"),
    "map": os.getenv("WA_MAP", "http://localhost:3000").rstrip("/"),
    "wikipedia": os.getenv("WA_WIKIPEDIA", "http://localhost:8888").rstrip("/"),
    "homepage": os.getenv("WA_HOMEPAGE", "http://localhost:4399").rstrip("/"),
}


def create_client():
    from openai import OpenAI

    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Set OPENROUTER_API_KEY in .env or the environment.")
    base_url = os.getenv("OPENROUTER_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if not base_url:
        base_url = (
            "https://openrouter.ai/api/v1"
            if os.getenv("OPENROUTER_API_KEY")
            else "https://api.openai.com/v1"
        )
    return OpenAI(api_key=key, base_url=base_url, timeout=120.0, max_retries=2)


def completion_options(model, temperature, max_tokens):
    options = {"max_tokens": max_tokens}
    # Reasoning models do not accept temperature. OpenRouter forwards reasoning
    # settings through extra_body; the returned content remains plain text.
    name = model.rsplit("/", 1)[-1]
    if name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        options["extra_body"] = {"reasoning": {"effort": os.getenv("LLM_REASONING_EFFORT", "low")}}
    else:
        options["temperature"] = temperature
    return options
