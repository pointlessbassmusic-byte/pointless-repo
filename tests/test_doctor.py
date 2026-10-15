"""`sportsbot doctor` preflight checks (offline paths)."""

import pytest

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
    # a null YAML value is a FAIL row, never a traceback
    broken = check_params({"bankroll": {"kelly_multiplier": None}})
    assert broken[0].level == FAIL and "unparseable" in broken[0].detail


def test_missing_db_is_reported_not_created(tmp_path):
    path = tmp_path / "nope" / "s.sqlite"
    checks = check_storage({"storage": {"sqlite_path": str(path)}})
    assert checks[0].level == WARN and "does not exist" in checks[0].detail
    assert not path.exists()  # the doctor must not create it


def test_storage_and_ratings(tmp_path):
    from sportsbot.data.store import Store

    cfg = {"storage": {"sqlite_path": str(tmp_path / "s.sqlite"),
                       "ratings_dir": str(tmp_path / "ratings")},
           "sports": {"tennis": {"enabled": True}}}
    store = Store(cfg["storage"]["sqlite_path"])  # bot has run: DB exists
    named = by_name(check_storage(cfg))
    assert named["storage.schema"].level == PASS
    assert named["storage.kv"].level == PASS
    assert named["storage.kill_switch"].level == PASS
    row = store.conn.execute(
        "SELECT COUNT(*) FROM kv WHERE key LIKE 'doctor:%'").fetchone()
    assert row[0] == 0  # probe cleaned up
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


def test_fee_floor_rejects_an_edge_bar_the_venue_eats():
    """An entry bar at or under the taker fee at mid plus slippage admits
    trades that lose to the venue on expectation; the doctor must FAIL it
    for the base bar and for every per-sport override, on both venues."""
    from sportsbot.bot.doctor import FAIL, PASS, check_params, fee_floor

    assert fee_floor({"exchange": "polymarket"}) == pytest.approx(0.0125 + 0.005)
    assert fee_floor({"exchange": "kalshi"}) == pytest.approx(0.0175 + 0.005)
    ok = {"exchange": "polymarket", "bankroll": {"min_edge": 0.03},
          "sports": {"table_tennis": {"min_edge_override": 0.05}}}
    assert all(c.level == PASS for c in check_params(ok) if "fee_floor" in c.name)
    bad = {"exchange": "kalshi", "bankroll": {"min_edge": 0.02},
           "sports": {"tennis": {"min_edge_override": 0.015}}}
    levels = {c.name: c.level for c in check_params(bad) if "fee_floor" in c.name}
    assert levels["params.fee_floor[bankroll.min_edge]"] == FAIL
    assert levels["params.fee_floor[sports.tennis.min_edge_override]"] == FAIL
    assert all(c.level == PASS for c in check_params({}) if "fee_floor" in c.name)


# ----------------------------------------------- Kalshi key validity (2026-10-13)
def _write_key(tmp_path, kind):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

    key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) if kind == "rsa"
           else ed25519.Ed25519PrivateKey.generate())
    path = tmp_path / f"{kind}.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o600)
    return str(path)


@pytest.mark.parametrize("kind,level", [("rsa", PASS), ("ed25519", FAIL)])
def test_live_kalshi_key_must_be_rsa(tmp_path, monkeypatch, kind, level):
    """The 2026-10-13 incident: an Ed25519 key (another tool's) passed as
    'present' and would have failed on the first order."""
    monkeypatch.setenv("KALSHI_API_KEY_ID", "id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", _write_key(tmp_path, kind))
    monkeypatch.setenv("KALSHI_ENV", "prod")
    checks = by_name(check_secrets({"mode": "live", "exchange": "kalshi"}))
    assert checks["secrets.kalshi_key_type"].level == level
    if kind == "ed25519":
        assert "RSA" in checks["secrets.kalshi_key_type"].detail


def test_unloadable_kalshi_key_fails_live(tmp_path, monkeypatch):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a key")
    bad.chmod(0o600)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(bad))
    checks = by_name(check_secrets({"mode": "live", "exchange": "kalshi"}))
    assert checks["secrets.kalshi_key_type"].level == FAIL
    assert "not a key" not in checks["secrets.kalshi_key_type"].detail   # contents never echoed


def test_live_kalshi_on_demo_env_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_API_KEY_ID", "id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", _write_key(tmp_path, "rsa"))
    monkeypatch.delenv("KALSHI_ENV", raising=False)
    checks = by_name(check_secrets({"mode": "live", "exchange": "kalshi"}))
    assert checks["secrets.KALSHI_ENV"].level == WARN
    monkeypatch.setenv("KALSHI_ENV", "prod")
    assert "secrets.KALSHI_ENV" not in by_name(check_secrets({"mode": "live", "exchange": "kalshi"}))


def test_kalshi_auth_probe(monkeypatch):
    from sportsbot.bot import doctor
    from sportsbot.exchanges import kalshi

    class _Ok:
        def _request(self, method, path, auth=False, **kw):
            assert auth and path.endswith("/portfolio/balance")
            return {"balance": 12345}

    class _Bad:
        def _request(self, *a, **kw):
            raise TypeError("Ed25519PrivateKey.sign() takes 1 positional arguments")

    monkeypatch.setattr(kalshi, "KalshiClient", _Ok)
    ok = doctor._check_kalshi_auth()
    assert ok.level == PASS and "$123.45" in ok.detail
    monkeypatch.setattr(kalshi, "KalshiClient", _Bad)
    assert doctor._check_kalshi_auth().level == FAIL
