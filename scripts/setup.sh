#!/usr/bin/env bash
# BSEBot setup. Idempotent: safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Checking Python 3.11+"
if ! command -v python3.11 >/dev/null 2>&1; then
  echo "ERROR: python3.11 not found on PATH." >&2
  exit 1
fi
PY_VER=$(python3.11 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "    found python $PY_VER"

echo "==> Creating venv at .venv"
if [ ! -d ".venv" ]; then
  python3.11 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies (with dev extras)"
pip install --upgrade pip
pip install -e ".[dev]"

echo "==> Running migrations"
mkdir -p data logs
python3.11 -c "from pathlib import Path; from bsebot import db; \
db.run_migrations(Path('data/bsebot.db'), Path('migrations'))"

echo "==> Verifying .env"
if [ ! -f ".env" ]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill in keys." >&2
  exit 1
fi
MISSING=0
for key in GEMINI_API_KEY CEREBRAS_API_KEY GROQ_API_KEY GITHUB_MODELS_TOKEN; do
  if ! grep -E "^${key}=.+" .env >/dev/null; then
    echo "    missing: $key"
    MISSING=1
  fi
done
if [ "$MISSING" -ne 0 ]; then
  echo "ERROR: one or more required keys missing in .env" >&2
  exit 1
fi

echo "==> Pinging every configured provider (bsebot llm test)"
bsebot --config config.yaml --env .env llm test

echo "==> Setup complete."
