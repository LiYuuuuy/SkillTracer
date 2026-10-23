#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv --without-pip .venv
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  mkdir -p .local
  python3 - <<'PY'
import urllib.request
urllib.request.urlretrieve("https://bootstrap.pypa.io/pip/pip.pyz", ".local/pip.pyz")
PY
  .venv/bin/python .local/pip.pyz install pip
fi
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m playwright install chromium
if [ ! -f .env ]; then
  (umask 077; cp .env.example .env)
fi
printf 'Setup complete. Set OPENROUTER_API_KEY in .env, then run .venv/bin/python scripts/check_local.py --llm\n'
