"""`sportsbot dashboard` end-to-end in a temp cwd: empty store -> event
export -> substrate/dashboard.py build -> self-contained HTML on disk.
Offline by construction (no --resolve, no weather DB)."""

from pathlib import Path

from typer.testing import CliRunner

from sportsbot.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_command_builds_html(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app, ["dashboard", "--config", str(REPO_ROOT / "config" / "default.yaml"),
              "--out", "data/dashboard.html"])
    assert result.exit_code == 0, result.output
    doc = (tmp_path / "data" / "dashboard.html").read_text()
    assert "Substrate dashboard" in doc
    assert "Bot operations" in doc      # ops panel renders even for empty store
    assert "clear" in doc               # kill switch not tripped
    assert (tmp_path / "data" / "substrate_events.csv").exists()
    assert (tmp_path / "data" / "ops.json").exists()
