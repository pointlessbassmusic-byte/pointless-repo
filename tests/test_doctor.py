"""`sportsbot doctor` preflight checks (offline paths)."""

from pathlib import Path

from typer.testing import CliRunner

from sportsbot.bot.doctor import (
    FAIL,
    PASS,
    WARN,
    check_mode,
    check_params,
    check_ratings,
    check_secrets,
    check_storage,
    run_checks,
    worst_level,
)
from sportsbot.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]


def by_name(checks):
    return {c.name: c for c in checks}


def test_live_gate_semantics(monkeypatch):
    monkeypatch.delenv("SPORTSBOT_LIVE", raising=False)
    c = by_name(check_mode({"mode": "live"}))["config.live_gate"]
    assert c.level == WARN and "force paper" in c.detail
    monkeypatch.setenv("SPORTSBOT_LIVE", "1")
    c = by_name(check_mode({"mode": "live"}))["config.live_gate"]
    assert c.level == PASS and "armed" in c.detail
    assert by_name(check_mode({}))["config.live_gate"].level == PASS


def test_param_sanity_catches_bad_knobs():
    bad = {"bankroll": {"kelly_multiplier": 1.5},
           "positions": {"exit_edge": 0.05, "stop_fraction": 1.2}}
    named = by_name(check_params(bad))
    assert named["params.kelly"].level == FAIL
    assert named["params.positions"].level == FAIL
    assert worst_level(check_params({})) == PASS  # defaults are sane


def test_storage_and_ratings(tmp_path):
    cfg = {"storage": {"sqlite_path": str(tmp_path / "s.sqlite"),
                       "ratings_dir": str(tmp_path / "ratings")},
           "sports": {"tennis": {"enabled": True}}}
    named = by_name(check_storage(cfg))
    assert named["storage.schema"].level == PASS
    assert named["storage.kv"].level == PASS
    assert named["storage.kill_switch"].level == PASS
    rat = by_name(check_ratings(cfg))
    assert rat["ratings.tennis"].level == WARN  # missing -> cold model
    (tmp_path / "ratings").mkdir()
    (tmp_path / "ratings" / "tennis.json").write_text("{}")
    assert by_name(check_ratings(cfg))["ratings.tennis"].level == PASS


def test_secrets_presence_only(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    named = by_name(check_secrets({"mode": "paper", "exchange": "kalshi"}))
    assert named["secrets.KALSHI_API_KEY_ID"].level == WARN   # paper: soft
    named = by_name(check_secrets({"mode": "live", "exchange": "kalshi"}))
    assert named["secrets.KALSHI_API_KEY_ID"].level == FAIL   # live: hard
    # values never leak into details
    monkeypatch.setenv("KALSHI_API_KEY_ID", "super-secret-id")
    named = by_name(check_secrets({"mode": "live", "exchange": "kalshi"}))
    assert "super-secret-id" not in named["secrets.KALSHI_API_KEY_ID"].detail


def test_cli_doctor_offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPORTSBOT_LIVE", raising=False)
    result = CliRunner().invoke(
        app, ["doctor", "--offline",
              "--config", str(REPO_ROOT / "config" / "default.yaml")])
    assert result.exit_code == 0, result.output   # WARNs only, no FAILs
    assert "sportsbot doctor" in result.output
    assert "overall" in result.output


def test_run_checks_offline_skips_network(tmp_path):
    cfg = {"storage": {"sqlite_path": str(tmp_path / "s.sqlite"),
                       "ratings_dir": str(tmp_path / "r")}}
    names = {c.name for c in run_checks(cfg, offline=True)}
    assert not any(n.startswith("net.") for n in names)
