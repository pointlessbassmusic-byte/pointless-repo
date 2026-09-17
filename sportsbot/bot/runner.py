"""The main loop: wire config -> models -> exchange -> scan/price/stake/execute.

One Runner instance per venue. Each cycle:
  1. risk pre-check (kill switches, daily limits)
  2. discover markets per enabled sport
  3. scan (entity-match + predict)
  4. evaluate (blend, edge, Kelly under caps)
  5. risk-gate each intent, execute survivors
  6. arb sweep (bundle; cross-venue when both clients configured)
  7. expire stale orders, snapshot quotes
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import yaml

from sportsbot.bot.arb import find_bundle_arb
from sportsbot.bot.executor import Executor
from sportsbot.bot.positions import (
    PositionConfig,
    adaptive_overrides,
    aggregate_open_bets,
    evaluate_exit,
    scaled_kelly,
)
from sportsbot.bot.risk import RiskConfig, RiskManager
from sportsbot.bot.scanner import Scanner
from sportsbot.bot.strategy import StrategyConfig, evaluate_market
from sportsbot.core.staking import StakingConfig
from sportsbot.core.types import Side, Sport
from sportsbot.data.store import Store
from sportsbot.engine.baseball import BaseballModel
from sportsbot.engine.tabletennis import TableTennisModel
from sportsbot.engine.tennis import TennisModel

log = logging.getLogger(__name__)

SPORT_KEYS = {"tennis": Sport.TENNIS, "baseball": Sport.BASEBALL,
              "table_tennis": Sport.TABLE_TENNIS}


def load_config(path: str = "config/default.yaml",
                local_path: str = "config/local.yaml") -> dict:
    with open(path) as fh:
        cfg = yaml.safe_load(fh) or {}
    if os.path.exists(local_path):
        with open(local_path) as fh:
            local = yaml.safe_load(fh) or {}
        cfg = _deep_merge(cfg, local)
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_models(cfg: dict, ratings_dir: str) -> dict[Sport, Any]:
    sports_cfg = cfg.get("sports", {})
    models: dict[Sport, Any] = {}
    if sports_cfg.get("tennis", {}).get("enabled", True):
        m = TennisModel(
            surface_weight=float(sports_cfg["tennis"].get("surface_weight", 0.5)),
            min_matches=int(sports_cfg["tennis"].get("min_matches", 10)),
        )
        _try_load(m, os.path.join(ratings_dir, "tennis.json"))
        models[Sport.TENNIS] = m
    if sports_cfg.get("baseball", {}).get("enabled", True):
        b = BaseballModel(
            home_advantage=float(sports_cfg["baseball"].get("home_advantage_elo", 24.0)),
            rest_per_day=float(sports_cfg["baseball"].get("rest_advantage_elo", 2.3)),
            sp_enabled=bool(sports_cfg["baseball"].get("sp_enabled", True)),
            prob_shrink=float(sports_cfg["baseball"].get("prob_shrink", 0.8)),
        )
        _try_load(b, os.path.join(ratings_dir, "baseball.json"))
        models[Sport.BASEBALL] = b
    if sports_cfg.get("table_tennis", {}).get("enabled", True):
        t = TableTennisModel(
            min_matches=int(sports_cfg["table_tennis"].get("min_matches", 8)),
            prob_shrink=float(sports_cfg["table_tennis"].get("prob_shrink", 0.5)),
        )
        _try_load(t, os.path.join(ratings_dir, "tabletennis.json"))
        models[Sport.TABLE_TENNIS] = t
    return models


def _try_load(model: Any, path: str) -> None:
    if os.path.exists(path):
        try:
            model.load(path)
            log.info("loaded ratings for %s from %s", model.name, path)
        except Exception:
            log.exception("failed loading ratings from %s", path)
    else:
        log.warning("no ratings file at %s — model %s starts cold (run "
                    "`sportsbot fit` first)", path, model.name)


def build_exchange(cfg: dict):
    """Return (execution_exchange, data_exchange, fee_fn)."""
    from sportsbot.exchanges.paper import PaperExchange

    venue = cfg.get("exchange", "polymarket")
    mode = cfg.get("mode", "paper")
    if venue == "kalshi":
        from sportsbot.exchanges.kalshi import KalshiClient, kalshi_taker_fee

        data_client = KalshiClient()
        fee_fn = lambda price, shares: kalshi_taker_fee(price, shares)  # noqa: E731
    else:
        from sportsbot.exchanges.polymarket import PolymarketClient, taker_fee

        data_client = PolymarketClient()
        fee_fn = taker_fee
    if mode == "live" and os.environ.get("SPORTSBOT_LIVE") == "1":
        return data_client, data_client, fee_fn
    paper = PaperExchange(
        data_client=data_client,
        starting_balance=float(cfg.get("bankroll", {}).get("amount", 1000.0)),
        fee_fn=fee_fn,
    )
    return paper, data_client, fee_fn


class Runner:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        storage = cfg.get("storage", {})
        self.store = Store(storage.get("sqlite_path", "data/sportsbot.sqlite"))
        self.ratings_dir = storage.get("ratings_dir", "data/ratings")
        self.models = load_models(cfg, self.ratings_dir)
        self.exchange, self.data_client, self.fee_fn = build_exchange(cfg)
        self.mode = cfg.get("mode", "paper")
        if self.mode == "live" and os.environ.get("SPORTSBOT_LIVE") != "1":
            log.warning("config mode=live but SPORTSBOT_LIVE!=1 — forcing paper mode")
            self.mode = "paper"

        bank = cfg.get("bankroll", {})
        self.staking = StakingConfig(
            bankroll=float(bank.get("amount", 1000.0)),
            kelly_multiplier=float(bank.get("kelly_multiplier", 0.25)),
            min_edge=float(bank.get("min_edge", 0.03)),
            min_stake=float(bank.get("min_stake", 5.0)),
            max_stake_per_market=float(bank.get("max_stake_per_market", 50.0)),
            max_fraction_per_market=float(bank.get("max_fraction_per_market", 0.05)),
            max_fraction_per_sport=float(bank.get("max_fraction_per_sport", 0.20)),
            max_total_exposure=float(bank.get("max_total_exposure", 0.50)),
            max_open_positions=int(bank.get("max_open_positions", 20)),
        )
        ex = cfg.get("execution", {})
        sports_cfg = cfg.get("sports", {})
        self.strategy = StrategyConfig(
            model_weight=float(cfg.get("blend", {}).get("model_weight", 0.30)),
            max_spread=float(ex.get("max_spread", 0.03)),
            slippage_buffer=float(ex.get("slippage_buffer", 0.005)),
            max_depth_fraction=float(ex.get("max_depth_fraction", 0.25)),
            post_inside_spread=bool(ex.get("post_inside_spread", True)),
            min_entry_price=float(ex.get("min_entry_price", 0.15)),
            max_entry_price=float(ex.get("max_entry_price", 0.85)),
            min_edge_override={
                k: float(v["min_edge_override"])
                for k, v in sports_cfg.items()
                if isinstance(v, dict) and "min_edge_override" in v
            },
            max_stake_override={
                k: float(v["max_stake_override"])
                for k, v in sports_cfg.items()
                if isinstance(v, dict) and "max_stake_override" in v
            },
        )
        risk_cfg = cfg.get("risk", {})
        self.risk = RiskManager(
            RiskConfig(
                daily_loss_limit=float(risk_cfg.get("daily_loss_limit", 100.0)),
                max_daily_new_risk=float(risk_cfg.get("max_daily_new_risk", 0.25)),
                max_drawdown=float(risk_cfg.get("max_drawdown", 250.0)),
                stale_quote_seconds=float(risk_cfg.get("stale_quote_seconds", 120.0)),
                min_minutes_before_start=float(risk_cfg.get("min_minutes_before_start", 10.0)),
                calibration_min_bets=int(risk_cfg.get("calibration_min_bets", 50)),
                calibration_max_brier=float(risk_cfg.get("calibration_max_brier", 0.26)),
                bankroll=self.staking.bankroll,
            ),
            self.store,
            mode=self.mode,
        )
        pos = cfg.get("positions", {})
        self.positions = PositionConfig(
            manage=bool(pos.get("manage", True)),
            exit_edge=float(pos.get("exit_edge", -0.05)),
            stop_fraction=float(pos.get("stop_fraction", 0.5)),
            min_hold_minutes=float(pos.get("min_hold_minutes", 30.0)),
            slippage_buffer=float(ex.get("slippage_buffer", 0.005)),
        )
        self.adaptive = cfg.get("adaptive", {})
        self.scanner = Scanner(self.models)
        self.executor = Executor(
            self.exchange, self.store, mode=self.mode,
            order_ttl_seconds=float(ex.get("order_ttl_seconds", 120.0)),
        )

    # ------------------------------------------------------------------
    def _mlb_context(self) -> dict[str, dict]:
        """Probable pitchers + rest-day differential for MLB markets.

        Rest feeds the model's +2.3 Elo/rest-day adjustment (capped at ±3
        days there); a team that hasn't appeared in the lookback window is
        treated as fully rested at the cap.
        """
        try:
            from datetime import datetime, timedelta, timezone

            from sportsbot.data.mlb_data import MLBStatsClient

            client = MLBStatsClient()
            games = client.upcoming(days=2)
            today = datetime.now(timezone.utc).date()
            last_played: dict[str, object] = {}
            for r in client.results(today - timedelta(days=6), today):
                for team in (r.home, r.away):
                    d = r.date.date()
                    if team not in last_played or d > last_played[team]:
                        last_played[team] = d

            def rest(team: str, game_date) -> float:
                last = last_played.get(team)
                if last is None:
                    return 3.0  # no game in a week: fully rested (model cap)
                return max(0.0, (game_date - last).days - 1)

            out = {}
            for g in games:
                gd = g.date.date()
                out[(g.home, g.away)] = {
                    "home_sp": g.home_sp,
                    "away_sp": g.away_sp,
                    "rest_diff_days": rest(g.home, gd) - rest(g.away, gd),
                }
            return out
        except Exception:
            log.exception("MLB context fetch failed; predicting without SP/rest")
            return {}

    def _settle_resolved(self) -> int:
        """Settle open bets whose markets have resolved. Returns count settled."""
        settled = 0
        open_bets = self.store.open_bets()
        by_market: dict[str, list[dict]] = {}
        for b in open_bets:
            by_market.setdefault(b["market_id"], []).append(b)
        for market_id, bets in by_market.items():
            try:
                yes_won = self.data_client.get_resolution(market_id)
            except Exception:
                log.exception("resolution check failed for %s", market_id)
                continue
            if yes_won is None:
                continue
            snap = self.store.last_snapshot(market_id)
            # Partial early closes banked proceeds and shrank the venue
            # position; settlement pays only the remainder.
            side_sizes: dict[str, float] = {}
            for b in bets:
                side_sizes[b["side"]] = side_sizes.get(b["side"], 0.0) + b["size"]
            banks = {s: (self.store.get_kv(f"partial_close:{market_id}:{s}")
                         or {"closed": 0.0, "proceeds": 0.0})
                     for s in side_sizes}
            for b in bets:
                side_won = (b["side"] == "yes") == yes_won
                total = side_sizes[b["side"]] or 1e-9
                bank = banks[b["side"]]
                frac_remaining = max(0.0, total - bank["closed"]) / total
                bank_share = bank["proceeds"] * b["size"] / total
                payout = b["size"] * frac_remaining if side_won else 0.0
                pnl = round(bank_share + payout - b["stake"], 2)
                closing = None
                if snap and snap.get("bid") is not None and snap.get("ask") is not None:
                    mid = (snap["bid"] + snap["ask"]) / 2.0
                    closing = mid if b["side"] == "yes" else 1.0 - mid
                self.store.settle_bet(b["id"], outcome=1 if side_won else 0,
                                      pnl=pnl, closing_price=closing)
                settled += 1
            for s, bank in banks.items():
                if bank["closed"]:
                    self.store.set_kv(f"partial_close:{market_id}:{s}", None)
            self.executor.settle_paper(market_id, yes_won)
            log.info("settled %s: yes_won=%s (%d bets)", market_id, yes_won, len(bets))
        return settled

    def _cycle_configs(self) -> tuple[StakingConfig, StrategyConfig]:
        """Cycle-local staking/strategy configs after the adaptive layer.

        Everything here only ever REDUCES risk relative to the configured
        base (smaller Kelly under drawdown, higher edge bars and smaller
        caps for negative-CLV sports). No mechanism may loosen the base or
        raise stakes in response to losses — that's loss-chasing, and it is
        deliberately impossible in this code path.
        """
        staking, strategy = self.staking, self.strategy
        ad = self.adaptive
        settled = self.store.settled_bets()

        if bool(ad.get("drawdown_stake_scaling", True)) and settled:
            cum = peak = 0.0
            for r in reversed(settled):  # oldest first
                cum += r.get("pnl") or 0.0
                peak = max(peak, cum)
            current_dd = peak - cum
            eff = scaled_kelly(staking.kelly_multiplier, current_dd,
                               self.risk.cfg.max_drawdown,
                               float(ad.get("stake_floor", 0.25)))
            if eff < staking.kelly_multiplier:
                log.info("drawdown $%.2f below peak: Kelly multiplier "
                         "%.3f -> %.3f", current_dd,
                         staking.kelly_multiplier, eff)
                staking = StakingConfig(**{**staking.__dict__,
                                           "kelly_multiplier": eff})

        if bool(ad.get("enabled", True)) and settled:
            edge_over, stake_over, tightened = adaptive_overrides(
                settled,
                self.strategy.min_edge_override, staking.min_edge,
                self.strategy.max_stake_override, staking.max_stake_per_market,
                window=int(ad.get("clv_window", 50)),
                min_bets=int(ad.get("clv_min_bets", 30)),
                tighten_edge=float(ad.get("tighten_edge", 0.02)),
                stake_cut=float(ad.get("stake_cut", 0.5)),
            )
            if tightened:
                log.info("adaptive tightening (negative rolling CLV): %s",
                         tightened)
                strategy = StrategyConfig(**{**strategy.__dict__,
                                             "min_edge_override": edge_over,
                                             "max_stake_override": stake_over})
        return staking, strategy

    def _manage_positions(self, quoted: dict, strategy: StrategyConfig) -> int:
        """Exit pass over open positions with a fresh quote this cycle.
        Returns the number of (market, side) positions closed."""
        if not self.positions.manage:
            return 0
        closer = getattr(self.exchange, "close_position", None)
        exits = 0
        for (market_id, side), agg in aggregate_open_bets(
                self.store.open_bets()).items():
            pair = quoted.get(market_id)
            if pair is None:
                continue  # not discoverable this cycle (e.g. in play): hold
            _market, quote = pair
            decision = evaluate_exit(agg, quote, self.positions,
                                     model_weight=strategy.model_weight,
                                     fee_fn=self.fee_fn)
            if not decision.close:
                continue
            if decision.sellable < agg["size"] * 0.999:
                log.info("exit signal on %s/%s but book too thin "
                         "(%.1f of %.1f sellable); retrying next cycle",
                         market_id, side, decision.sellable, agg["size"])
                continue
            if closer is None:
                log.warning("exit signal on %s/%s (%s) but venue has no close "
                            "path; holding", market_id, side, decision.reason)
                continue
            result = closer(market_id, Side(side), quote,
                            self.positions.min_exit_price)
            if not result:
                continue
            # A live IOC can partially fill against a moving book. Bank the
            # partial's proceeds in the KV store; the bets close only once
            # the whole aggregate is out (next cycles retry the remainder),
            # and settlement reconciles any bank left over.
            bank_key = f"partial_close:{market_id}:{side}"
            bank = self.store.get_kv(bank_key) or {"closed": 0.0, "proceeds": 0.0}
            total_closed = bank["closed"] + result["closed_size"]
            total_proceeds = bank["proceeds"] + result["proceeds"]
            if total_closed < agg["size"] * 0.999:
                self.store.set_kv(bank_key, {"closed": total_closed,
                                             "proceeds": total_proceeds})
                log.warning("partial close on %s/%s: %.1f of %.1f out so far; "
                            "proceeds banked, retrying next cycle",
                            market_id, side, total_closed, agg["size"])
                continue
            total_stake = agg["stake"] or 1e-9
            for bet_id, stake in agg["bets"]:
                pnl = round(total_proceeds * (stake / total_stake) - stake, 2)
                self.store.close_bet(bet_id, pnl=pnl,
                                     closing_price=result["avg_price"])
            self.store.set_kv(bank_key, None)
            exits += 1
            log.info("closed %s/%s: %s -> proceeds $%.2f on stake $%.2f",
                     market_id, side, decision.reason,
                     total_proceeds, agg["stake"])
        return exits

    def cycle(self) -> dict:
        """One scan cycle. Returns a summary dict."""
        summary = {"markets": 0, "scanned": 0, "intents": 0, "orders": 0,
                   "arbs": 0, "settled": 0, "exits": 0, "blocked": None}
        self.executor.reconcile_open_orders()
        summary["settled"] = self._settle_resolved()
        ok, reason = self.risk.check_global()
        if not ok:
            log.warning("cycle blocked by risk: %s", reason)
            summary["blocked"] = reason
            return summary
        staking_cfg, strategy_cfg = self._cycle_configs()

        sports = self.cfg.get("scan", {}).get("sports", list(SPORT_KEYS))
        markets = []
        for sport_key in sports:
            if sport_key not in SPORT_KEYS:
                continue
            try:
                markets.extend(self.data_client.list_sports_markets(sport_key))
            except Exception:
                log.exception("market discovery failed for %s", sport_key)
        summary["markets"] = len(markets)

        # Attach MLB probable-pitcher context by fuzzy-pairing team names.
        extra_context: dict[str, dict] = {}
        mlb_ctx = self._mlb_context() if any(
            m.sport == Sport.BASEBALL for m in markets) else {}
        if mlb_ctx:
            from sportsbot.bot.matching import similarity

            for m in markets:
                if m.sport != Sport.BASEBALL or not m.home:
                    continue
                best = max(
                    mlb_ctx.items(),
                    key=lambda kv: similarity(m.home, kv[0][0]) + similarity(m.away or "", kv[0][1]),
                    default=None,
                )
                if best and similarity(m.home, best[0][0]) > 0.85:
                    extra_context[m.market_id] = best[1]

        scanned = self.scanner.scan(markets, extra_context)
        summary["scanned"] = len(scanned)

        exposure = self.store.exposure_by()
        quoted: dict[str, tuple] = {}
        for sm in scanned:
            try:
                quote = self.exchange.get_quote(sm.market)
            except Exception:
                log.exception("quote failed for %s", sm.market.market_id)
                continue
            quoted[sm.market.market_id] = (sm.market, quote)
            self.store.snapshot_quote(sm.market.market_id, quote.bid, quote.ask)
            self.store.record_prediction(
                sm.market.market_id,
                sm.market.sport.value if sm.market.sport else "unknown",
                sm.prediction.model, sm.prediction.prob_yes,
                sm.prediction.prob_raw, sm.prediction.features,
            )

            # Arb sweep runs for every quoted market, independent of whether
            # the model produces a bet.
            arb = find_bundle_arb(sm.market, quote, self.fee_fn)
            if arb:
                summary["arbs"] += 1
                log.info("ARB FOUND (log-only): %s profit=%.3f/pair x %.0f",
                         arb.description, arb.profit_per_pair, arb.max_pairs)
                self.store.set_kv(f"arb:{sm.market.market_id}", arb.__dict__)

            intent = evaluate_market(
                sm.market, quote, sm.prediction, staking_cfg,
                strategy_cfg, self.fee_fn, exposure,
            )
            if intent is None:
                continue
            summary["intents"] += 1
            ok, reason = self.risk.check_intent(intent, quote)
            if not ok:
                log.info("intent vetoed (%s): %s", reason, intent.market.slug)
                continue
            order = self.executor.submit(intent, quote=quote)
            if order.status.value not in ("rejected",):
                summary["orders"] += 1
                # keep exposure fresh within the cycle
                exposure = self.store.exposure_by()

        summary["exits"] = self._manage_positions(quoted, strategy_cfg)

        canceled = self.executor.expire_stale_orders()
        if canceled:
            log.info("expired %d stale orders", canceled)
        return summary

    def run_forever(self) -> None:
        interval = float(self.cfg.get("scan", {}).get("interval_seconds", 300))
        log.info("sportsbot runner starting: mode=%s venue=%s interval=%ss",
                 self.mode, self.exchange.exchange.value, interval)
        while True:
            started = time.time()
            try:
                summary = self.cycle()
                log.info("cycle done: %s", summary)
            except Exception:
                log.exception("cycle crashed; continuing")
            elapsed = time.time() - started
            time.sleep(max(5.0, interval - elapsed))
