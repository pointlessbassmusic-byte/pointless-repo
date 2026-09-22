"""The scan loop: poll real venues -> price every candidate net of costs ->
decide -> (paper) execute -> persist -> render.

MODES
  sim   (default) — $100 of paper capital against real, live books.
  real            — requires ALL of: mode: real in config, CRYPTOARB_LIVE=1
                    in the environment, and venue API keys. Order placement
                    is NOT implemented yet: real mode currently refuses to
                    arm and says so. The toggle is wired; the trigger is not.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

import allocator as alloc_mod
import strategies as strat
import venues
from broker import PaperBroker
from fees import FeeBook
from store import Store, now

log = logging.getLogger("cryptoarb")

REQUIRED_KEYS = {"coinbase": ["COINBASE_API_KEY", "COINBASE_API_SECRET"],
                 "kraken": ["KRAKEN_API_KEY", "KRAKEN_API_SECRET"],
                 "kucoin": ["KUCOIN_API_KEY", "KUCOIN_API_SECRET",
                            "KUCOIN_API_PASSPHRASE"]}


def live_gate(cfg: dict) -> dict:
    """Everything standing between the current state and real money."""
    mode = (cfg or {}).get("mode", "sim")
    env_armed = os.environ.get("CRYPTOARB_LIVE") == "1"
    keys = {v: all(os.environ.get(k) for k in ks)
            for v, ks in REQUIRED_KEYS.items()}
    return {
        "mode_is_real": mode == "real",
        "env_armed": env_armed,
        "keys_present": keys,
        "executor_implemented": False,   # deliberately not built yet
        "armed": False,                  # therefore never armed
        "blocked_by": ([] if mode == "real" else ["config mode is 'sim'"])
                      + ([] if env_armed else ["CRYPTOARB_LIVE!=1"])
                      + ([f"no {v} keys" for v, ok in keys.items() if not ok])
                      + ["live executor not implemented"],
    }


class Engine:
    def __init__(self, cfg: dict | None = None, db: str = "data/cryptoarb.sqlite"):
        self.cfg = cfg or {}
        self.feebook = FeeBook.from_config(self.cfg)
        self.store = Store(db)
        self.bankroll = float(self.cfg.get("bankroll", 100.0))
        saved = self.store.get_kv("cash")
        self.broker = PaperBroker(starting_cash=self.bankroll,
                                  cash=float(saved) if saved is not None
                                  else self.bankroll)
        self.mode = self.cfg.get("mode", "sim")

    def scan(self) -> list:
        """One pass over every venue; returns priced opportunities."""
        opps = []
        try:
            snap = venues.spot_snapshot()
            opps += strat.cross_exchange(snap, self.feebook)
        except Exception:
            log.exception("cross-exchange scan failed")
        try:
            opps += strat.triangular(venues.kucoin_all_tickers(), self.feebook)
        except Exception:
            log.exception("triangular scan failed")
        if self.cfg.get("scan_polymarket", True):
            try:
                opps += strat.bundle(
                    venues.polymarket_binary_books(
                        limit_events=int(self.cfg.get("pm_events", 25)),
                        max_markets=int(self.cfg.get("pm_markets", 30))),
                    self.feebook)
            except Exception:
                log.exception("bundle scan failed")
        return opps

    def cycle(self) -> dict:
        opps = self.scan()
        tradeable = [o for o in opps if o.tradeable]
        cid = self.store.record_cycle(self.mode, self.broker.equity,
                                      self.broker.cash, len(opps), len(tradeable))
        caps = alloc_mod.allocations(self.bankroll,
                                     self.store.realized_by_strategy())
        deployed: dict = {}
        n_fills = 0
        for o in sorted(opps, key=lambda x: -x.net_bps):
            if not o.tradeable:
                self.store.record_decision(cid, o, "skip")
                continue
            size = alloc_mod.size_for(o, caps, deployed, self.broker.cash)
            if size <= 0:
                self.store.record_decision(cid, o, "skip",
                                           "positive edge but strategy cap exhausted")
                continue
            f = self.broker.execute(o, size, now())
            if f is None:
                self.store.record_decision(cid, o, "skip", "no executable size")
                continue
            deployed[o.strategy] = deployed.get(o.strategy, 0.0) + size
            self.store.record_fill(f)
            self.store.record_decision(cid, o, "fill",
                                       f"took ${size:,.2f} -> {f.pnl:+.4f}")
            n_fills += 1
        self.store.set_kv("cash", self.broker.cash)
        return {"cycle": cid, "opportunities": len(opps),
                "tradeable": len(tradeable), "fills": n_fills,
                "equity": round(self.broker.equity, 4)}

    def run(self, interval: float = 60.0, cycles: int = 0, render_to: str = ""):
        i = 0
        while True:
            i += 1
            started = time.time()
            summary = self.cycle()
            log.info("cycle %s", summary)
            if render_to:
                from arb_dashboard import render_to_file
                render_to_file(self, render_to)
            if cycles and i >= cycles:
                return summary
            time.sleep(max(5.0, interval - (time.time() - started)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once", action="store_true")
    p.add_argument("--cycles", type=int, default=0)
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--db", default="data/cryptoarb.sqlite")
    p.add_argument("--out", default="data/dashboard.html")
    p.add_argument("--mode", default="sim", choices=["sim", "real"])
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    eng = Engine({"mode": args.mode}, db=args.db)
    gate = live_gate(eng.cfg)
    if args.mode == "real" and not gate["armed"]:
        log.warning("real mode requested but NOT armed: %s",
                    "; ".join(gate["blocked_by"]))
        log.warning("continuing in SIM — no real order will be placed")
        eng.mode = "sim"
    print(eng.cycle() if args.once else
          eng.run(args.interval, args.cycles, args.out))
    if args.once:
        from arb_dashboard import render_to_file
        render_to_file(eng, args.out)
        print(f"dashboard -> {args.out}")


if __name__ == "__main__":
    main()
