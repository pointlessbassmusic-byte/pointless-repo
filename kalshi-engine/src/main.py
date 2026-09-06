"""Kalshi predictions engine — entrypoint.

Usage:
    python -m src.main --dry-run
    python -m src.main --once --dry-run
    python -m src.main --live    # requires live: true in config.yaml + API creds in .env
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from .client import KalshiClient
from .config import load_config
from .execution.executor import Executor
from .storage.db import Database
from .strategy.edge import build_signals
from .substrate.base import Context
from .substrate.ensemble import Ensemble
from .substrate.generators.market_implied import MarketImplied
from .substrate.generators.mean_reversion import MeanReversion
from .substrate.generators.time_decay import TimeDecay

log = logging.getLogger("kalshi-engine")

GENERATOR_REGISTRY = {
    "market_implied": MarketImplied,
    "mean_reversion": MeanReversion,
    "time_decay": TimeDecay,
}


def build_ensemble(substrate_cfg: dict) -> Ensemble:
    gens = []
    for name, gcfg in (substrate_cfg.get("generators") or {}).items():
        cls = GENERATOR_REGISTRY.get(name)
        if cls is None:
            log.warning("unknown generator in config: %s", name)
            continue
        if gcfg.get("enabled", True):
            gens.append(cls(gcfg))
    log.info("substrate: %d generators active: %s", len(gens), [g.name for g in gens])
    return Ensemble(gens)


def run_cycle(cfg, client: KalshiClient, ensemble: Ensemble, executor: Executor, db: Database) -> None:
    mcfg = cfg.markets
    whitelist = mcfg.get("series_whitelist") or []
    statuses = mcfg.get("statuses", ["open"])
    max_markets = int(mcfg.get("max_markets_per_scan", 500))

    if whitelist:
        markets = []
        for series in whitelist:
            markets.extend(client.markets(statuses=statuses, series_ticker=series,
                                          max_markets=max_markets))
    else:
        markets = client.markets(statuses=statuses, max_markets=max_markets)

    # filters
    min_volume = int(mcfg.get("min_volume", 0))
    max_days = float(mcfg.get("max_days_to_expiry", 365))
    horizon = datetime.now(timezone.utc) + timedelta(days=max_days)
    markets = [
        m for m in markets
        if m.volume >= min_volume and 0 < m.mid < 1
        and (m.expiration is None or m.expiration <= horizon)
    ]
    log.info("%d markets after filters", len(markets))

    # build context from *prior* scans' history, then record this scan's prices —
    # recording first would make "N scans ago" off by one for every generator
    ctx = Context(price_history=db.price_history([m.ticker for m in markets]))
    db.record_prices({m.ticker: m.mid for m in markets})

    results, n_forecasts = [], 0
    for m in markets:
        res = ensemble.predict(m, ctx)
        if res:
            results.append(res)
            n_forecasts += len(res.forecasts)
            db.record_forecasts(m.ticker, res.forecasts)

    signals = build_signals(
        results,
        cfg.strategy,
        exclude_tickers=db.placed_tickers(),
        existing_exposure=db.live_exposure(),
    )
    db.record_scan(len(markets), n_forecasts, len(signals))
    executor.execute(signals)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi predictions engine")
    parser.add_argument("--live", action="store_true", help="place real orders (requires live: true in config)")
    parser.add_argument("--dry-run", action="store_true", help="explicitly force dry run")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config()

    live = args.live and cfg.live and not args.dry_run
    if args.live and not cfg.live:
        log.warning("--live passed but config has live: false — staying in DRY RUN")
    log.info("mode: %s | exchange: %s", "LIVE TRADING" if live else "dry run",
             "DEMO" if cfg.use_demo else "PROD")

    db = Database(cfg.db_path)
    client = KalshiClient(cfg.api_key_id, cfg.private_key_path, demo=cfg.use_demo)
    ensemble = build_ensemble(cfg.substrate)
    executor = Executor(client, db, live=live)

    while True:
        try:
            run_cycle(cfg, client, ensemble, executor, db)
        except Exception:  # noqa: BLE001 — keep the loop alive across transient API failures
            log.exception("scan cycle failed")
        if args.once:
            break
        time.sleep(cfg.scan_interval_sec)


if __name__ == "__main__":
    main()
