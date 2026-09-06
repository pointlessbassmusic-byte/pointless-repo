"""Polymarket sports betting bot — entrypoint.

Usage:
    python -m src.main --dry-run          # default behavior even without the flag
    python -m src.main --once --dry-run   # one scan cycle then exit
    python -m src.main --live             # real orders (also requires live: true in config.yaml)
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .clients.clob import ClobClient
from .clients.gamma import GammaClient
from .clients.odds_api import OddsApiClient
from .config import load_config
from .models.fair_value import match_and_estimate
from .execution.executor import Executor
from .risk import RiskGate
from .storage.db import Database
from .strategy.edge import build_signals

log = logging.getLogger("polymarket-bot")


def run_cycle(cfg, gamma: GammaClient, clob: ClobClient, odds: OddsApiClient, executor: Executor, db: Database) -> None:
    markets = gamma.active_sports_markets()

    games = []
    for sport in cfg.sports:
        try:
            games.extend(odds.h2h_games(sport))
        except Exception:  # noqa: BLE001 — one sport feed failing shouldn't kill the cycle
            log.exception("odds feed failed for %s", sport)

    estimates = match_and_estimate(
        markets,
        games,
        min_books=int(cfg.odds.get("min_books", 3)),
        blend_market_weight=float(cfg.model.get("blend_market_weight", 0.15)),
    )

    token_ids = list({e.market.clob_token_ids[e.outcome_index] for e in estimates})
    quotes = clob.quotes(token_ids) if token_ids else {}
    db.record_estimates(estimates, quotes)

    signals = build_signals(
        estimates,
        quotes,
        cfg.strategy,
        exclude_tokens=db.placed_tokens(),
        existing_exposure=db.live_exposure(),
    )
    db.record_scan(len(markets), len(estimates), len(signals))
    executor.execute(signals)


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket sports betting bot")
    parser.add_argument("--live", action="store_true", help="place real orders (requires live: true in config)")
    parser.add_argument("--dry-run", action="store_true", help="explicitly force dry run")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config()

    live = args.live and cfg.live and not args.dry_run
    if args.live and not cfg.live:
        log.warning("--live passed but config has live: false — staying in DRY RUN")
    log.info("mode: %s", "LIVE TRADING" if live else "dry run")

    db = Database(cfg.db_path)
    gamma = GammaClient()
    clob = ClobClient(cfg.polymarket_private_key, cfg.polymarket_funder)
    odds = OddsApiClient(
        cfg.odds_api_key,
        regions=cfg.odds.get("regions", "us"),
        cache_ttl_sec=float(cfg.odds.get("cache_ttl_sec", 3600)),
    )
    gate = RiskGate(db, cfg.raw.get("risk", {}), Path(__file__).resolve().parent.parent)
    executor = Executor(clob, db, live=live, risk_gate=gate)

    while True:
        try:
            run_cycle(cfg, gamma, clob, odds, executor, db)
        except Exception:  # noqa: BLE001 — keep the loop alive across transient API failures
            log.exception("scan cycle failed")
        if args.once:
            break
        time.sleep(cfg.scan_interval_sec)


if __name__ == "__main__":
    main()
