"""`sportsbot doctor` — go-live preflight. One command that checks what
bites operators in production: config sanity, the paper/live double gate,
DB schema + migrations, ratings freshness, secret PRESENCE (values are
never read beyond truthiness, never printed), venue reachability, and
clock skew (RSA-signed orders need a true clock).

Every check returns PASS / WARN / FAIL with a one-line detail. FAIL means
"do not go live like this"; WARN is degraded-but-runnable. Pure reads —
the doctor never mutates anything except a KV round-trip probe it deletes.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

GAMMA_PING = "https://gamma-api.polymarket.com/events?limit=1"
CLOB_PING = "https://clob.polymarket.com/ok"
KALSHI_PING = {
    "prod": "https://api.elections.kalshi.com/trade-api/v2/exchange/status",
    "demo": "https://demo-api.kalshi.co/trade-api/v2/exchange/status",
}
RATINGS_STALE_DAYS = 7.0
CLOCK_WARN_S, CLOCK_FAIL_S = 10.0, 60.0


@dataclass
class Check:
    name: str
    level: str
    detail: str


def _ck(name: str, ok: bool, detail_ok: str, detail_bad: str,
        bad_level: str = FAIL) -> Check:
    return Check(name, PASS if ok else bad_level,
                 detail_ok if ok else detail_bad)


# ---------------------------------------------------------------- config

def check_mode(cfg: dict) -> list[Check]:
    out = []
    mode = cfg.get("mode", "paper")
    exchange = cfg.get("exchange", "polymarket")
    out.append(_ck("config.mode", mode in ("paper", "live"),
                   f"mode={mode}", f"unknown mode {mode!r}"))
    out.append(_ck("config.exchange",
                   exchange in ("polymarket", "kalshi", "paper"),
                   f"exchange={exchange}", f"unknown exchange {exchange!r}"))
    if mode == "live":
        gate = os.environ.get("SPORTSBOT_LIVE") == "1"
        out.append(Check("config.live_gate", PASS if gate else WARN,
                         "mode=live and SPORTSBOT_LIVE=1: LIVE TRADING armed"
                         if gate else
                         "mode=live but SPORTSBOT_LIVE!=1 — runner will "
                         "force paper (the double gate, working as designed)"))
    else:
        out.append(Check("config.live_gate", PASS, "paper mode"))
    return out


def check_params(cfg: dict) -> list[Check]:
    try:
        return _check_params(cfg)
    except (TypeError, ValueError) as exc:
        # A null/non-numeric knob in the YAML is exactly what the doctor
        # exists to report — as a FAIL row, never a traceback.
        return [Check("params", FAIL, f"unparseable config value: {exc}")]


def _check_params(cfg: dict) -> list[Check]:
    out = []
    bank = cfg.get("bankroll", {})
    km = float(bank.get("kelly_multiplier", 0.25))
    out.append(_ck("params.kelly", 0.0 < km <= 1.0,
                   f"kelly_multiplier={km}", f"kelly_multiplier={km} outside (0,1]"))
    fm = float(bank.get("max_fraction_per_market", 0.05))
    fs = float(bank.get("max_fraction_per_sport", 0.20))
    ft = float(bank.get("max_total_exposure", 0.50))
    out.append(_ck("params.caps", 0 < fm <= fs <= ft <= 1.0,
                   f"market {fm:.0%} <= sport {fs:.0%} <= total {ft:.0%}",
                   f"cap ordering broken: {fm}/{fs}/{ft}"))
    pos = cfg.get("positions", {})
    ee = float(pos.get("exit_edge", -0.05))
    sf = float(pos.get("stop_fraction", 0.5))
    out.append(_ck("params.positions", ee < 0.0 and 0.0 < sf < 1.0,
                   f"exit_edge={ee} stop_fraction={sf}",
                   f"exit_edge must be <0 and stop_fraction in (0,1): "
                   f"{ee}/{sf}"))
    ad = cfg.get("adaptive", {})
    fl = float(ad.get("stake_floor", 0.25))
    sc = float(ad.get("stake_cut", 0.5))
    out.append(_ck("params.adaptive", 0.0 < fl <= 1.0 and 0.0 < sc <= 1.0,
                   f"stake_floor={fl} stake_cut={sc}",
                   f"adaptive knobs outside (0,1]: floor={fl} cut={sc}"))
    return out


# ---------------------------------------------------------------- storage

def check_storage(cfg: dict) -> list[Check]:
    from sportsbot.data.store import Store

    out = []
    path = cfg.get("storage", {}).get("sqlite_path", "data/sportsbot.sqlite")
    if not os.path.exists(path):
        # Opening would CREATE a fresh DB (and pass every check) — a wrong
        # cwd/path must not masquerade as a healthy install.
        return [Check("storage", WARN,
                      f"{path} does not exist — fresh install (bot has not "
                      "run here), or the doctor is running from the wrong "
                      "directory")]
    try:
        store = Store(path)
        cols = [r[1] for r in store.conn.execute("PRAGMA table_info(bets)")]
        out.append(_ck("storage.schema", "status" in cols,
                       f"{path}: bets schema current (status column present)",
                       f"{path}: bets.status missing — migration didn't run"))
        probe = "doctor:ping"
        store.set_kv(probe, "ok")
        ok = store.get_kv(probe) == "ok"
        with store._lock:
            store.conn.execute("DELETE FROM kv WHERE key=?", (probe,))
            store.conn.commit()
        out.append(_ck("storage.kv", ok, "KV read/write OK",
                       "KV round-trip failed"))
        ks = store.get_kv("kill_switch_tripped", False)
        out.append(Check("storage.kill_switch", WARN if ks else PASS,
                         f"TRIPPED: {ks} — reset-kill-switch after review"
                         if ks else "clear"))
    except Exception as exc:
        out.append(Check("storage", FAIL, f"{path}: {exc}"))
    return out


def check_ratings(cfg: dict) -> list[Check]:
    out = []
    ratings_dir = cfg.get("storage", {}).get("ratings_dir", "data/ratings")
    files = {"tennis": "tennis.json", "baseball": "baseball.json",
             "table_tennis": "tabletennis.json"}
    for sport, scfg in (cfg.get("sports", {}) or {}).items():
        if not (isinstance(scfg, dict) and scfg.get("enabled", True)):
            continue
        fname = files.get(sport)
        if fname is None:
            continue
        path = os.path.join(ratings_dir, fname)
        if not os.path.exists(path):
            out.append(Check(f"ratings.{sport}", WARN,
                             f"{path} missing — run `sportsbot fit {sport}` "
                             "(model starts cold: 0 markets scanned)"))
            continue
        age_d = (time.time() - os.path.getmtime(path)) / 86400.0
        stale = age_d > RATINGS_STALE_DAYS
        out.append(Check(f"ratings.{sport}", WARN if stale else PASS,
                         f"{path}: {age_d:.1f}d old"
                         + (" — stale, refresh via cron" if stale else "")))
    return out


# ---------------------------------------------------------------- secrets

def check_secrets(cfg: dict) -> list[Check]:
    """Presence only. Values are never read beyond truthiness or printed."""
    out = []
    mode = cfg.get("mode", "paper")
    exchange = cfg.get("exchange", "polymarket")
    missing_level = FAIL if mode == "live" else WARN
    if exchange == "kalshi":
        for var in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH"):
            out.append(_ck(f"secrets.{var}", bool(os.environ.get(var)),
                           "set", "not set — authed endpoints unavailable",
                           bad_level=missing_level))
        key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        if key_path:
            if not os.path.exists(key_path):
                out.append(Check("secrets.kalshi_key_file", missing_level,
                                 "key file path set but file not found"))
            elif os.name == "posix" and (os.stat(key_path).st_mode & 0o077):
                out.append(Check("secrets.kalshi_key_file", WARN,
                                 "key file is group/world readable — chmod 600"))
            else:
                out.append(Check("secrets.kalshi_key_file", PASS, "present"))
    elif exchange == "polymarket" and mode == "live":
        out.append(_ck("secrets.POLYMARKET_PRIVATE_KEY",
                       bool(os.environ.get("POLYMARKET_PRIVATE_KEY")),
                       "set", "not set — live orders impossible"))
    else:
        out.append(Check("secrets", PASS,
                         "paper mode on public data: no keys required"))
    return out


# ---------------------------------------------------------------- network

def _ping(url: str, timeout: float = 6.0):
    import httpx

    t0 = time.time()
    resp = httpx.get(url, timeout=timeout,
                     headers={"User-Agent": "sportsbot-doctor/1.0"})
    return resp, (time.time() - t0) * 1000.0


def check_network(cfg: dict) -> list[Check]:
    out = []
    skew: float | None = None
    exchange = cfg.get("exchange", "polymarket")
    # Only the venues this deployment actually talks to: a blocked venue
    # the config never uses must not fail the preflight.
    targets = []
    if exchange in ("polymarket", "paper"):
        targets += [("net.gamma", GAMMA_PING), ("net.clob", CLOB_PING)]
    if exchange == "kalshi":
        kalshi_env = (os.environ.get("KALSHI_ENV") or "demo").lower()
        targets.append(("net.kalshi",
                        KALSHI_PING["prod" if kalshi_env == "prod" else "demo"]))
    for name, url in targets:
        try:
            resp, ms = _ping(url)
            # Skew is measured against the clock IMMEDIATELY after this
            # response — never after later (possibly slow) pings.
            if skew is None and resp.headers.get("date"):
                try:
                    skew = abs((datetime.now(timezone.utc)
                                - parsedate_to_datetime(resp.headers["date"])
                                ).total_seconds())
                except (TypeError, ValueError):
                    pass
            if resp.status_code < 400:
                out.append(Check(name, PASS,
                                 f"{resp.status_code} in {ms:.0f}ms"))
            else:
                out.append(Check(name, WARN,
                                 f"HTTP {resp.status_code} — reachable but "
                                 "not OK (geoblock/auth/maintenance?)"))
        except Exception as exc:
            out.append(Check(name, FAIL, f"unreachable: {exc}"))
    if skew is not None:
        level = (FAIL if skew > CLOCK_FAIL_S
                 else WARN if skew > CLOCK_WARN_S else PASS)
        out.append(Check("net.clock_skew", level,
                         f"{skew:.1f}s vs venue Date header"
                         + ("" if level == PASS else
                            " — fix NTP/chrony before signing orders")))
    return out


# ---------------------------------------------------------------- driver

def run_checks(cfg: dict, offline: bool = False) -> list[Check]:
    checks = (check_mode(cfg) + check_params(cfg) + check_storage(cfg)
              + check_ratings(cfg) + check_secrets(cfg))
    if not offline:
        checks += check_network(cfg)
    return checks


def worst_level(checks: list[Check]) -> str:
    levels = {c.level for c in checks}
    return FAIL if FAIL in levels else WARN if WARN in levels else PASS
