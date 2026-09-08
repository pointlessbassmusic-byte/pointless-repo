"""CI coverage for the substrate ARV session runner: run its own self-test
(full open -> transcribe -> judge -> resolve lifecycle against a temp pool
and SQLite ledger) as a subprocess so the substrate/ sibling-import style
stays untouched."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_arv_cli_self_test():
    proc = subprocess.run(
        [sys.executable, "arv_cli.py", "--self-test"],
        cwd=REPO_ROOT / "substrate",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "All self-tests passed." in proc.stdout
