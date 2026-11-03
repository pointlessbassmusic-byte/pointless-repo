"""deploy/export_reports.sh runs over ssh as root from /root; the configs'
paths (logs/, data/...) are relative to the app directory, so every
`sportsbot` call must run from there."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


def test_every_report_runs_from_the_app_directory(tmp_path):
    """Telemetry 2026-11-03: status_sim.txt and allocation_sim.txt were
    `PermissionError: 'logs'` tracebacks — the script ran sportsbot from
    root's home, where the config's relative log dir cannot be created (and a
    relative sqlite path would have opened the wrong book)."""
    app = tmp_path / "app"
    (app / "data").mkdir(parents=True)
    for db in ("prematch", "book_lines", "fresh_watch", "pilot"):
        (app / "data" / f"{db}.sqlite").write_text("")
    calls = tmp_path / "cwd.log"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _stub(bin_ / "sudo", f'echo "$PWD" >> {calls}\n')
    for tool in ("systemctl", "journalctl", "chown"):
        _stub(bin_ / tool, "exit 0\n")
    elsewhere = tmp_path / "root_home"
    elsewhere.mkdir()
    env = dict(os.environ, SPORTSBOT_APP_DIR=str(app), PATH=f"{bin_}:{os.environ['PATH']}")
    res = subprocess.run(["bash", str(REPO / "deploy" / "export_reports.sh")], cwd=elsewhere,
                         env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    dirs = calls.read_text().split()
    assert len(dirs) >= 5
    assert set(dirs) == {str(app)}
