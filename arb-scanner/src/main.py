"""Cross-platform arbitrage scanner — entrypoint.

Detection only: it never places orders. Opportunities are logged and recorded
to SQLite for review; execution across two platforms needs simultaneous fills
and funded accounts on both, which is a deliberate human step.

Usage:
    python -m src.main --once
    python -m src.main            # scan on an interval
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from pathlib import Path

import yaml

from .arb import ArbDetector
from .feeds import KalshiFeed, PolymarketFeed
from .matcher import find_pairs

log = logging.getLogger("arb-scanner")
ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    detected_at TEXT NOT NULL,
    kind TEXT,
    description TEXT,
    yes_platform TEXT, no_platform TEXT,
    yes_market_id TEXT, no_market_id TEXT,
    yes_ask REAL, no_ask REAL,
    gross_edge REAL, net_edge REAL, similarity REAL
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    n_poly INTEGER, n_kalshi INTEGER, n_pairs INTEGER, n_opps INTEGER
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def run_cycle(cfg: dict, poly: PolymarketFeed, kalshi: KalshiFeed,
              detector: ArbDetector, conn: sqlite3.Connection) -> None:
    mcfg = cfg.get("markets", {})
    pm = poly.markets(max_events=int(mcfg.get("poly_max_events", 500)),
                      min_liquidity=float(mcfg.get("poly_min_liquidity", 1000)))
    km = kalshi.markets(max_events=int(mcfg.get("kalshi_max_events", 20000)))
    km = [m for m in km if m.volume >= float(mcfg.get("kalshi_min_volume", 100))]

    match_cfg = cfg.get("matching", {})
    pairs = find_pairs(
        pm, km,
        min_similarity=float(match_cfg.get("min_similarity", 0.5)),
        close_time_slack_hours=float(match_cfg.get("close_time_slack_hours", 26)),
    )
    log.info("matched %d cross-platform pairs", len(pairs))

    opps = detector.scan_pairs(pairs) + detector.scan_bundles(pm) + detector.scan_bundles(km)
    opps.sort(key=lambda o: o.net_edge, reverse=True)

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO scans (ts, n_poly, n_kalshi, n_pairs, n_opps) VALUES (?,?,?,?,?)",
                 (ts, len(pm), len(km), len(pairs), len(opps)))
    for o in opps:
        conn.execute(
            "INSERT INTO opportunities (detected_at, kind, description, yes_platform,"
            " no_platform, yes_market_id, no_market_id, yes_ask, no_ask, gross_edge,"
            " net_edge, similarity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (o.detected_at, o.kind, o.description, o.yes_platform, o.no_platform,
             o.yes_market_id, o.no_market_id, o.yes_ask, o.no_ask,
             o.gross_edge, o.net_edge, o.similarity),
        )
        log.info("ARB[%s] net %.2f%% (gross %.2f%%): YES@%.3f on %s / NO@%.3f on %s | sim %.2f | %s",
                 o.kind, o.net_edge * 100, o.gross_edge * 100, o.yes_ask, o.yes_platform,
                 o.no_ask, o.no_platform, o.similarity, o.description[:120])
    conn.commit()
    if not opps:
        log.info("no opportunities above %.1f%% net edge", detector.min_net_edge * 100)


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket/Kalshi arbitrage scanner")
    parser.add_argument("--once", action="store_true", help="run one scan and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f) or {}

    conn = open_db(ROOT / Path(cfg.get("storage", {}).get("db_path", "data/arb.db")))
    poly, kalshi = PolymarketFeed(), KalshiFeed()
    detector = ArbDetector(cfg.get("arb", {}))

    while True:
        try:
            run_cycle(cfg, poly, kalshi, detector, conn)
        except Exception:  # noqa: BLE001 — keep the loop alive across transient API failures
            log.exception("scan cycle failed")
        if args.once:
            break
        time.sleep(int(cfg.get("scan_interval_sec", 300)))


if __name__ == "__main__":
    main()
