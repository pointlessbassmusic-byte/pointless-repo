#!/bin/bash
# SessionStart hook: make tests runnable immediately in Claude Code on the web.
# Installs the shared Python deps for all three modules (mirrors CI's package
# list in .github/workflows/tests.yml). py-clob-client is NOT installed: it is
# lazily imported for live trading only and tests never need it. cffi is pinned
# in because the preinstalled system `cryptography` wheel is missing its
# backend without it.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

pip install --quiet requests PyYAML python-dotenv cryptography cffi pytest ruff

# sportsbot itself (typer, pydantic, httpx, rich, pandas ...). A rebuilt
# container has none of these and every `sportsbot` command and test fails
# to import; measured 2026-10-06 when the daily check-in found an empty
# site-packages after an overnight rebuild.
pip install --quiet -e "${CLAUDE_PROJECT_DIR:-.}"

echo "session-start: python test dependencies installed"
