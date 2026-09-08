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

pip install --quiet requests PyYAML python-dotenv cryptography cffi pytest

echo "session-start: python test dependencies installed"
