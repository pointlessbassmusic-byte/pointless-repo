"""CI coverage for the substrate dashboard (milestone 4): run its self-test
(synthetic ingest CSV + real arv_cli session lifecycle -> HTML build with
wealth curves, fusion weights, trial counts) as a subprocess so the
substrate/ sibling-import style stays untouched."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_self_test():
    proc = subprocess.run(
        [sys.executable, "dashboard.py", "--self-test"],
        cwd=REPO_ROOT / "substrate",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "All self-tests passed." in proc.stdout
