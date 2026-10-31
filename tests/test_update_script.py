"""deploy/update.sh against a throwaway origin, with sudo/chown/systemctl/pip
stubbed: a step that fails after the pull must be retried the next day, and
every run leaves data/update_status.json for `sportsbot doctor` to read."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "deploy" / "update.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="needs bash and git")


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def box(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "app.txt").write_text("v1\n")
    _git(seed, "add", "app.txt")
    _git(seed, "commit", "-q", "-m", "v1")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    app = tmp_path / "app"
    _git(tmp_path, "clone", "-q", str(origin), str(app))
    log = tmp_path / "calls.log"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _stub(bin_ / "chown", "exit 0\n")
    _stub(bin_ / "sudo", 'if [ "$1" = -u ]; then shift 2; fi\nexec "$@"\n')
    _stub(bin_ / "systemctl", f'echo "systemctl $*" >> {log}\n')
    venv = app / ".venv" / "bin"
    venv.mkdir(parents=True)
    fail = tmp_path / "pip_fails"
    _stub(venv / "pip", f'[ -f {fail} ] && exit 1\necho "pip $*" >> {log}\n')
    _stub(venv / "sportsbot", "exit 0\n")
    (app / ".git" / "info" / "exclude").write_text(".venv/\ndata/\n")

    def run():
        env = dict(os.environ, SPORTSBOT_APP_DIR=str(app), PATH=f"{bin_}:{os.environ['PATH']}")
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)

    def push(text):
        (seed / "app.txt").write_text(text)
        _git(seed, "commit", "-qam", text.strip())
        _git(seed, "push", "-q", str(origin), "main")

    def status():
        return json.loads((app / "data" / "update_status.json").read_text())

    def head():
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=app, capture_output=True,
                               text=True, check=True).stdout.strip()

    return dict(app=app, log=log, fail=fail, run=run, push=push, status=status, head=head)


def test_a_step_that_fails_after_the_pull_is_retried_next_day(box):
    """The old script compared HEAD before and after the pull: when pip failed
    after a successful pull, the next run saw no new commits, printed "units
    left running" and never installed or restarted — stale services forever,
    with the timer looking healthy."""
    box["push"]("v2\n")
    box["fail"].touch()
    first = box["run"]()
    assert first.returncode != 0
    st = box["status"]()
    assert st["status"] == "failed" and st["step"] == "pip"
    assert st["head"] == box["head"]()                     # the pull itself went through
    box["fail"].unlink()
    second = box["run"]()
    assert second.returncode == 0, second.stderr
    calls = box["log"].read_text()
    assert "pip install" in calls
    assert "systemctl restart sportsbot-sim" in calls
    st = box["status"]()
    assert st["status"] == "ok" and st["deployed"] == box["head"]()


def test_a_failed_pull_is_reported_and_nothing_restarts(box):
    box["push"]("v2\n")
    (box["app"] / "app.txt").write_text("edited on the box\n")   # local edit main also changes
    before = box["head"]()
    res = box["run"]()
    assert res.returncode != 0
    st = box["status"]()
    assert st["status"] == "failed" and st["step"] == "pull"
    assert box["head"]() == before
    assert not box["log"].exists() or "restart" not in box["log"].read_text()


def test_nothing_new_leaves_the_units_running(box):
    assert box["run"]().returncode == 0                    # first run deploys once
    box["log"].unlink()
    res = box["run"]()
    assert res.returncode == 0
    assert "no new commits" in res.stdout
    assert not box["log"].exists() or "restart" not in box["log"].read_text()
    assert box["status"]()["status"] == "ok"
