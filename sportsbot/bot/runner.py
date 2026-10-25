"""The main loop: wire config -> models -> exchange -> scan/price/stake/execute.

One Runner instance per venue. Each cycle:
  1. risk pre-check (kill switches, daily limits)
  2. discover markets per enabled sport
  3. scan (entity-match + predict)
  4. evaluate (blend, edge, Kelly under caps)
  5. risk-gate each intent, execute survivors
  6. arb sweep (bundle, single venue; cross-venue matching exists in bot/arb.py
     but is not wired into the cycle)
  7. expire stale orders, snapshot quotes
"""

from __future__ import annotations

import logging
import os
import re
import signal
import time
from datetime import datetime, timezone
from typing import Optional, Any

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
from sportsbot.bot.ledger import account_equity, snapshot_equity
from sportsbot.bot import alerts
from sportsbot.bot.touch import TouchConfig, evaluate_touch, in_window
from sportsbot.bot.portfolio import (
    allocate_arms,
    effective_bankroll,
    load_allocation,
    running_arms_for,
    summarise_for_dashboard,
)
from sportsbot.bot.strategy import StrategyConfig, evaluate_market_verbose
from sportsbot.core.staking import StakingConfig
from sportsbot.core.types import Side, Sport
from sportsbot.data.store import Store
from sportsbot.engine.baseball import BaseballModel
from sportsbot.engine.sharpline import SharpLineModel
from sportsbot.engine.tabletennis import TableTennisModel
from sportsbot.engine.tennis import TennisModel
from sportsbot.signals.sharp import (
    ODDS_API_KEY_ENV,
    Grader,
    OddsApiClient,
    SharpCollector,
    SharpConfig,
    with_sharp_closes,
)

log = logging.getLogger(__name__)

# How many distinct drop reasons to name per feed row before
# collapsing the rest into a count.
_DROP_EXAMPLES = 3

SPORT_KEYS = {"tennis": Sport.TENNIS, "baseball": Sport.BASEBALL,
              "table_tennis": Sport.TABLE_TENNIS, "basketball": Sport.BASKETBALL,
              "football": Sport.FOOTBALL, "hockey": Sport.HOCKEY,
              "soccer": Sport.SOCCER, "esports": Sport.ESPORTS, "mma": Sport.MMA,
              "college_football": Sport.COLLEGE_FOOTBALL, "cricket": Sport.CRICKET}
# Sports with no rating engine in this repo: only the sharp line can price them.
SHARP_ONLY_SPORTS = ("basketball", "football", "hockey")
# Sports with neither a rating engine nor a sharp-harness key: only the
# touch-maker arm can quote them (`signal: touch`), in paper, for fill
# evidence. Added 2026-10-13 after the Polymarket survey.
TOUCH_ONLY_SPORTS = ("soccer", "esports", "mma", "college_football", "cricket")


class _Unpriced:
    """A market the touch arm quotes without a prediction (no model, no
    sharp line on record): what `_touch_pass` and the decision feed need
    of a ScannedMarket, with `prediction` None."""

    __slots__ = ("market", "prediction")

    def __init__(self, market):
        self.market = market
        self.prediction = None


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


def load_models(cfg: dict, ratings_dir: str, store=None) -> dict[Sport, Any]:
    """One model per enabled sport. `sports.<sport>.signal: sharp` swaps the
    rating model for the sharp-line model (needs `store`, which holds the
    collected lines); the default `model` keeps the Elo-family engines."""
    sports_cfg = cfg.get("sports", {})
    models: dict[Sport, Any] = {}

    def signal_of(key: str) -> str:
        sc = sports_cfg.get(key, {}) or {}
        return str(sc.get("signal", "model")).lower()

    def wants_sharp(key: str) -> bool:
        return signal_of(key) == "sharp"

    def wants_touch(key: str) -> bool:
        # `signal: touch`: no model is loaded for the sport, so the scanner
        # drops its markets as "no model" and only the touch-maker arm quotes
        # them (runner._touch_only_markets). Discovery still runs for it.
        return signal_of(key) == "touch"

    sharp_cfg = SharpConfig.from_cfg(cfg)
    for key, sport in SPORT_KEYS.items():
        enabled = sports_cfg.get(key, {}).get(
            "enabled", key not in SHARP_ONLY_SPORTS and key not in TOUCH_ONLY_SPORTS)
        if enabled and key in TOUCH_ONLY_SPORTS and not wants_touch(key):
            raise ValueError(f"sports.{key} has no rating model and no sharp key here; "
                             f"set sports.{key}.signal: touch or enabled: false")
        if enabled and key in SHARP_ONLY_SPORTS and not (wants_sharp(key) or wants_touch(key)):
            raise ValueError(f"sports.{key} has no rating model here; set "
                             f"sports.{key}.signal: sharp (or touch) or enabled: false")
        if enabled and wants_sharp(key):
            if store is None:
                raise ValueError(f"sports.{key}.signal=sharp needs the store")
            models[sport] = SharpLineModel(store, sport, sharp_cfg)
    if (sports_cfg.get("tennis", {}).get("enabled", True) and not wants_touch("tennis")
            and Sport.TENNIS not in models):
        m = TennisModel(
            surface_weight=float(sports_cfg["tennis"].get("surface_weight", 0.5)),
            min_matches=int(sports_cfg["tennis"].get("min_matches", 10)),
        )
        _try_load(m, os.path.join(ratings_dir, "tennis.json"))
        models[Sport.TENNIS] = m
    if (sports_cfg.get("baseball", {}).get("enabled", True) and not wants_touch("baseball")
            and Sport.BASEBALL not in models):
        b = BaseballModel(
            home_advantage=float(sports_cfg["baseball"].get("home_advantage_elo", 24.0)),
            rest_per_day=float(sports_cfg["baseball"].get("rest_advantage_elo", 2.3)),
            sp_enabled=bool(sports_cfg["baseball"].get("sp_enabled", True)),
            prob_shrink=float(sports_cfg["baseball"].get("prob_shrink", 0.8)),
        )
        _try_load(b, os.path.join(ratings_dir, "baseball.json"))
        models[Sport.BASEBALL] = b
    if (sports_cfg.get("table_tennis", {}).get("enabled", True) and not wants_touch("table_tennis")
            and Sport.TABLE_TENNIS not in models):
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


def staking_from_cfg(cfg: dict) -> StakingConfig:
    """The ONE place config becomes sizing limits. The runner and the
    market backtest both build from here, so a backtest decision is the
    live decision under the same config, not a hand copy of it."""
    bank = cfg.get("bankroll", {})
    return StakingConfig(
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


def strategy_from_cfg(cfg: dict) -> StrategyConfig:
    """Config -> the strategy filters (see `staking_from_cfg`)."""
    ex = cfg.get("execution", {})
    sports_cfg = cfg.get("sports", {})
    return StrategyConfig(
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


def build_exchange(cfg: dict):
    """Return (execution_exchange, data_exchange, fee_fn)."""
    from sportsbot.exchanges.paper import PaperExchange

    venue = cfg.get("exchange", "polymarket")
    mode = cfg.get("mode", "paper")
    if venue == "kalshi":
        from sportsbot.exchanges.kalshi import (
            KalshiClient,
            kalshi_maker_fee_per_share,
            kalshi_taker_fee,
        )

        # Market DATA always comes from prod. KalshiClient() defaults to the
        # demo exchange when KALSHI_ENV is unset, and demo's books are a
        # mirror at best and synthetic at worst — a paper book quoting them
        # is not trading real-time events, and a settled market may show up
        # late or not at all. Orders (live mode only) still honour KALSHI_ENV,
        # so demo remains the place to test order placement.
        data_client = KalshiClient(env="prod")
        exec_client = KalshiClient()

        def fee_fn(price, shares, market_id=None):
            """Kalshi's fee multiplier is per series (MLB 0.5, tennis 1.0), so
            a flat rate is wrong for one of them whichever it picks."""
            return kalshi_taker_fee(price, shares,
                                    data_client.fee_multiplier(market_id))

        def maker_fee_fn(price, shares, market_id=None):
            """What a RESTING order pays when the tape fills it (per
            contract, series multiplier applied)."""
            return kalshi_maker_fee_per_share(
                market_id or "", data_client.fee_multiplier(market_id)) * shares
    elif venue == "polymarket_us":
        from sportsbot.exchanges.polymarket_us import PolymarketUSClient, taker_fee_us

        # One client for data and orders: the public gateway needs no keys;
        # orders need POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY and the
        # live gate below. The venue publishes no keyless trade tape, so in
        # paper mode resting orders here never fill (no tape, no fill).
        data_client = PolymarketUSClient()
        exec_client = data_client

        def fee_fn(price, shares, market_id=None):
            """Taker 0.0695 x C x p x (1 - p) on every sport the client
            discovers (table tennis' 0.10 is never listed here)."""
            return taker_fee_us(price, shares)

        def maker_fee_fn(price, shares, market_id=None):
            """Makers pay nothing on Polymarket US; the rebate (0.0125 coefficient
            until 2026-10-19, volume tiers after) is ignored (conservative)."""
            return 0.0
    else:
        from sportsbot.exchanges.polymarket import PolymarketClient, taker_fee

        data_client = PolymarketClient()
        exec_client = data_client

        def fee_fn(price, shares, market_id=None):
            """Per-market rate from Gamma's feeSchedule (discovery fills it);
            the documented 0.05 for anything not yet discovered."""
            return taker_fee(price, shares, market_id,
                             fee_rate=data_client.fee_rate_for(market_id))

        def maker_fee_fn(price, shares, market_id=None):
            """Polymarket makers pay nothing; the 15% rebate they earn is
            ignored here (conservative)."""
            return 0.0
    if mode == "live" and os.environ.get("SPORTSBOT_LIVE") == "1":
        return exec_client, data_client, fee_fn
    paper = PaperExchange(
        data_client=data_client,
        starting_balance=float(cfg.get("bankroll", {}).get("amount", 1000.0)),
        fee_fn=fee_fn,
        maker_fee_fn=maker_fee_fn,
    )
    return paper, data_client, fee_fn


_EDGE_RE = re.compile(r"edge ([+-]?\d+\.\d+) vs the (\d+\.\d+) bar")


def _tally_edge(seen: dict, market, intent, why: str, staking_cfg, strategy_cfg) -> None:
    """One evaluated market's distance to the bar: the intent's edge when it
    bet, the near-miss edge the strategy printed when it passed, and the
    spread blocks separately (they never reached the edge test)."""
    sport = market.sport.value if market.sport else "unknown"
    bar = float(strategy_cfg.min_edge_override.get(sport, staking_cfg.min_edge))
    d = seen.setdefault(sport, {"best": None, "priced": 0, "spread_blocked": 0,
                                "within_1pt": 0, "bets": 0, "bar": bar})
    d["priced"] += 1
    edge = None
    if intent is not None:
        edge = float(intent.edge)
        d["bets"] += 1
    else:
        m = _EDGE_RE.search(why or "")
        if m:
            edge = float(m.group(1))
        elif "spread" in (why or ""):
            d["spread_blocked"] += 1
    if edge is not None:
        if d["best"] is None or edge > d["best"]:
            d["best"] = round(edge, 4)
        if edge >= bar - 0.01:
            d["within_1pt"] += 1


class Runner:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        storage = cfg.get("storage", {})
        self.store = Store(storage.get("sqlite_path", "data/sportsbot.sqlite"))
        self.ratings_dir = storage.get("ratings_dir", "data/ratings")
        self.models = load_models(cfg, self.ratings_dir, store=self.store)
        self.exchange, self.data_client, self.fee_fn = build_exchange(cfg)
        self.touch = TouchConfig.from_cfg(cfg)
        self.mode = cfg.get("mode", "paper")
        if self.mode == "live" and os.environ.get("SPORTSBOT_LIVE") != "1":
            log.warning("config mode=live but SPORTSBOT_LIVE!=1 — forcing paper mode")
            self.mode = "paper"

        # The dashboard account this process writes to. `self.mode` is already
        # the EFFECTIVE mode — a live config without SPORTSBOT_LIVE=1 has been
        # forced to paper above, so its rows land in the sim book, which is
        # where they belong.
        self.account = "real" if self.mode == "live" else "sim"
        # The equity curve must be anchored to the SAME starting balance the
        # dashboard renders against, or the stored curve and the Equity tile
        # disagree by whatever the two settings differ by.
        from sportsbot.dashboard import starting_balance

        self.starting_balance = starting_balance(cfg, self.account)
        self.staking = staking_from_cfg(cfg)
        self.strategy = strategy_from_cfg(cfg)
        ex = cfg.get("execution", {})
        risk_cfg = cfg.get("risk", {})
        self.risk = RiskManager(
            RiskConfig(
                daily_loss_limit=float(risk_cfg.get("daily_loss_limit", 100.0)),
                max_daily_new_risk=float(risk_cfg.get("max_daily_new_risk", 0.25)),
                max_drawdown=float(risk_cfg.get("max_drawdown", 250.0)),
                stale_quote_seconds=float(risk_cfg.get("stale_quote_seconds", 120.0)),
                min_minutes_before_start=float(risk_cfg.get("min_minutes_before_start", 10.0)),
                max_pre_match_move=float(risk_cfg.get("max_pre_match_move", 0.08)),
                calibration_min_bets=int(risk_cfg.get("calibration_min_bets", 50)),
                calibration_max_brier=float(risk_cfg.get("calibration_max_brier", 0.26)),
                bankroll=self.staking.bankroll,
                daily_loss_fraction=(float(risk_cfg["daily_loss_fraction"])
                                     if risk_cfg.get("daily_loss_fraction") is not None else None),
                max_drawdown_fraction=(float(risk_cfg["max_drawdown_fraction"])
                                       if risk_cfg.get("max_drawdown_fraction") is not None else None),
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
        self.venue = cfg.get("exchange", "polymarket")
        # Portfolio allocation: the operator's file decides what may be
        # overridden by hand; evidence and the learning budget decide the
        # rest, every cycle, tighten-only against the risk caps.
        self.alloc_cfg = load_allocation(
            str(cfg.get("allocation", {}).get("path", "config/allocation.yaml")))
        self.running_arms = running_arms_for(cfg)
        self.scanner = Scanner(self.models)
        # Sharp-line CLV harness (data only; see signals/sharp.py). Without
        # an API key it still records market metadata so decisions can be
        # graded later, once lines are being collected.
        self.sharp_cfg = SharpConfig.from_cfg(cfg)
        api_key = os.environ.get(ODDS_API_KEY_ENV, "").strip()
        sharp_client = None
        if self.sharp_cfg.enabled and api_key:
            sharp_client = OddsApiClient(api_key)
        elif self.sharp_cfg.enabled:
            log.warning("sharp: %s not set — recording market metadata only; "
                        "no sportsbook lines will be collected", ODDS_API_KEY_ENV)
        self.sharp = SharpCollector(self.store, self.sharp_cfg, sharp_client)
        self.executor = Executor(
            self.exchange, self.store, mode=self.mode,
            order_ttl_seconds=float(ex.get("order_ttl_seconds", 120.0)),
        )
        # Every booked fill gets marked at +5 s/+60 s/+5 min/+30 min against
        # the live book (bot/markout.py): the adverse-selection measurement
        # the pilot exists to make. Data only; runs on paper fills too.
        from sportsbot.bot.markout import MarkoutRecorder

        self.markouts = MarkoutRecorder(self.store, self.data_client)
        self.executor.on_fill = self.markouts.schedule
        self._ratings_cache: tuple[float, dict, dict] | None = None

    # ------------------------------------------------------------------
    def maker_fee_fn(self, price: float, market_id: str = "") -> float:
        """Per-contract cost of a RESTING order on this venue.

        Kalshi reports `fee_type: "quadratic_with_maker_fees"` on both sports
        series, so a maker fill is not free — and the strategy prefers maker
        execution, which made this the most-used execution path in the bot
        and the one whose cost was modelled as zero.
        """
        if self.venue != "kalshi":
            return 0.0          # Polymarket makers pay no taker fee
        from sportsbot.exchanges.kalshi import kalshi_maker_fee_per_share

        return kalshi_maker_fee_per_share(market_id, price=price)

    def decision_fee_fn(self, market_id: str):
        """Fee function for EDGE/SIZING/EXIT math on one market.

        `self.fee_fn` is the venue's actual charge (Kalshi ceils the whole
        order to the cent), which is right for accounting but wrong as a
        marginal rate: calling it with shares=1.0 quantises the per-share
        fee to a whole cent and overstates cost by up to ~2x inside the
        entry band. Decision paths get the linear marginal instead, with
        the market's own series multiplier (KXMLBGAME pays half).
        """
        if self.venue != "kalshi":
            return self.fee_fn
        from sportsbot.exchanges.kalshi import (
            kalshi_fee_multiplier,
            kalshi_fee_per_share,
        )

        mult = kalshi_fee_multiplier(market_id)
        # Same (price, shares, market_id=None) shape as `fee_fn`: the
        # multiplier is already bound to this market, so the third argument
        # is accepted and ignored rather than making callers special-case it.
        return lambda price, shares, market_id=None: (
            kalshi_fee_per_share(price, mult) * shares)

    # ------------------------------------------------------------------
    def _priceable_sports(self) -> set[str]:
        """Config keys of the sports whose loaded model has something to
        price with: rated entities, or sharp lines on record."""
        out = set()
        for key, sport in SPORT_KEYS.items():
            model = self.models.get(sport)
            if model is None:
                continue
            if isinstance(model, SharpLineModel):
                model.refresh()
            try:
                if self.scanner._rated_entities(model):
                    out.add(key)
            except Exception:
                log.exception("rated-entity check failed for %s", key)
        return out

    def _sharp_fee_for(self, exchange: str, market_id: str):
        fn = self.decision_fee_fn(market_id)
        return lambda price: fn(price, 1.0)

    def _sharp_tick(self, scanned: list) -> dict | None:
        """Record who each scanned market is between, then take a budgeted
        sportsbook snapshot if one is due. Telemetry: never raises."""
        if not self.sharp_cfg.enabled:
            return None
        try:
            self.sharp.record_markets([sm.market for sm in scanned])
            sports = {sm.market.sport for sm in scanned if sm.market.sport}
            if not sports:
                return None
            summary = self.sharp.snapshot(sports)
            if summary.get("calls"):
                log.info("sharp snapshot: %s", summary)
            return summary
        except Exception:
            log.exception("sharp tick failed")
            return None

    def _sharp_backfill(self) -> int:
        """Write the sharp close onto bets that do not have one yet."""
        if not self.sharp_cfg.enabled:
            return 0
        try:
            grader = Grader(self.store, self.sharp_cfg, self._sharp_fee_for)
            return len(grader.grade_bets(self.mode, write=True, only_missing=True))
        except Exception:
            log.exception("sharp backfill failed")
            return 0

    # ------------------------------------------------------------------
    def _mlb_start_check(self, markets):
        """Pull baseball starts earlier to MLB's scheduled first pitch where
        the venue's clock is late (`bot/start_check.py`). The schedule window
        opens yesterday so a game started late on the previous UTC date is
        still seen as Live. A failed read keeps the venue's times."""
        from datetime import datetime, timezone

        from sportsbot.bot.start_check import earliest_starts
        from sportsbot.data.mlb_data import MLBStatsClient

        try:
            games = MLBStatsClient().upcoming(days=2, back_days=1)
        except Exception as exc:  # noqa: BLE001 — the venue clock still stands
            log.warning("MLB schedule unavailable for the start check: %s", exc)
            return markets, 0
        return earliest_starts(markets, games, datetime.now(timezone.utc))

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
        open_bets = self.store.open_bets(mode=getattr(self, "mode", None))   # this book only
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

        # Bankroll: config amount, or the account's live equity when the
        # allocation file says `bankroll_mode: equity` (profits roll in and
        # losses roll out). The risk limits that scale with it follow.
        bankroll = self.staking.bankroll
        if self.alloc_cfg.bankroll_mode == "equity":
            try:
                eq = account_equity(self.store, self.account, self.starting_balance)
                bankroll = effective_bankroll(self.alloc_cfg, self.staking.bankroll,
                                              eq["equity"])
            except Exception:
                log.exception("equity read failed; sizing on the config bankroll")
        if bankroll != staking.bankroll:
            staking = StakingConfig(**{**staking.__dict__, "bankroll": bankroll})
        self.risk.set_bankroll(bankroll)

        # Portfolio: dollars per arm -> dollars and style per sport.
        try:
            fees = self.store.get_kv("fees_verified")
            port = allocate_arms(
                bankroll, self.cfg, self.alloc_cfg,
                self.store.settled_bets(limit=1_000_000, mode=self.mode),
                self.running_arms,
                fees_verified=isinstance(fees, dict) and bool(fees.get("verified")),
                mode=self.mode, priceable_sports=self._priceable_sports())
            self.store.set_kv("portfolio:last", {
                "ts": datetime.now(timezone.utc).isoformat(),
                "account": self.account, "bankroll": port["bankroll"],
                "total_cap": port["total_cap"], "allocated": port["allocated"],
                "sport_budget": port["sport_budget"],
                "style_by_sport": port["style_by_sport"],
                "learning": port["learning"], "bankroll_mode": port["bankroll_mode"],
                "rows": summarise_for_dashboard(port)})
            strategy = StrategyConfig(**{**strategy.__dict__,
                                         "sport_budget": port["sport_budget"],
                                         "style_override": port["style_by_sport"]})
        except Exception:
            # Fail closed: with no allocation nothing is funded.
            log.exception("portfolio allocation failed; no sport is funded this cycle")
            strategy = StrategyConfig(**{**strategy.__dict__, "sport_budget": {},
                                         "style_override": {
                                             s: "none" for s in SPORT_KEYS}})

        # This book's bets only: a live book's drawdown and CLV must never
        # be read off the paper sim's history, or vice versa.
        settled = self.store.settled_bets(limit=1_000_000, mode=self.mode)
        if self.sharp_cfg.enabled and self.sharp_cfg.enforce_adaptive:
            # Benchmark the rolling CLV against Pinnacle where a sharp close
            # exists. `adaptive_overrides` is tighten-only, so this can only
            # make a sport stricter, never looser.
            settled = with_sharp_closes(settled)

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
                self.store.open_bets_for(self.mode)).items():
            pair = quoted.get(market_id)
            if pair is None:
                continue  # not discoverable this cycle (e.g. in play): hold
            _market, quote = pair
            decision = evaluate_exit(agg, quote, self.positions,
                                     model_weight=strategy.model_weight,
                                     fee_fn=self.decision_fee_fn(market_id))
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
            self._record_exit(agg, decision,
                              _market.sport.value
                              if _market is not None and _market.sport else None)
            log.info("closed %s/%s: %s -> proceeds $%.2f on stake $%.2f",
                     market_id, side, decision.reason,
                     total_proceeds, agg["stake"])
        return exits

    def _record_exit(self, agg: dict, decision, sport: str | None) -> None:
        """A close is a decision too: without it the feed shows how a position
        was opened and never how it ended."""
        try:
            self.store.record_decision(
                account=self.account, market_id=agg["market_id"],
                sport=sport, title=agg["market_id"], action="exit",
                side=agg.get("side"), model_prob=agg.get("model_prob"),
                price=getattr(decision, "exit_price", None),
                stake=agg.get("stake"),
                reason=getattr(decision, "reason", None))
        except Exception:
            log.exception("exit decision record failed")

    def _alert_new_fills(self, bets_before: int) -> int:
        """One alert per cycle naming the fills booked since `bets_before`
        (arm, side, price, size); nothing when there were none."""
        try:
            rows = self.store.all_bets(self.mode)
            new = rows[bets_before:]
            if not new:
                return 0
            parts = [f"{r.get('arm') or '?'} {r['side']} {float(r['size']):.2f} @ {float(r['entry_price']):.3f}"
                     for r in new[:8]]
            more = f" (+{len(new) - 8} more)" if len(new) > 8 else ""
            alerts.notify("fill", f"[{self.mode}] {len(new)} fill(s): " + "; ".join(parts) + more,
                          dedupe_seconds=0.0)
            return len(new)
        except Exception:
            log.exception("fill alert failed")
            return 0

    def _record_edge_bar(self, edge_seen: dict) -> None:
        """Fold this cycle's best edge per sport into today's row
        (`edgebar:<account>:<date>`), so the board can show how far the
        slate sat from the entry bar day by day -- the arbitrage desk's
        "distance to breakeven", which a flat equity line cannot show.
        Survives decision-feed pruning. Telemetry: never raises."""
        if not edge_seen:
            return
        try:
            key = f"edgebar:{self.account}:{datetime.now(timezone.utc).date().isoformat()}"
            row = self.store.get_kv(key) or {}
            for sport, d in edge_seen.items():
                cur = row.get(sport) or {"best": None, "priced": 0, "spread_blocked": 0,
                                         "within_1pt": 0, "bets": 0, "cycles": 0, "bar": d["bar"]}
                if d["best"] is not None and (cur["best"] is None or d["best"] > cur["best"]):
                    cur["best"] = d["best"]
                for k in ("priced", "spread_blocked", "within_1pt", "bets"):
                    cur[k] = cur.get(k, 0) + d[k]
                cur["cycles"] = cur.get("cycles", 0) + 1
                cur["bar"] = d["bar"]
                row[sport] = cur
            self.store.set_kv(key, row)
        except Exception:
            log.exception("edge-bar record failed")

    TOUCH_DROP_CATEGORIES = ("no sharp line", "no model")

    def _touch_only_markets(self, drops, now=None, tally: Optional[dict] = None) -> list:
        """Markets the scanner dropped for want of a sharp line or a model,
        whose sport the touch arm quotes anyway: it rests at the venue's own
        price and needs neither. Games outside the arm's start window are
        counted in `tally["skipped"]` here, before any quote is fetched, so a
        slate of hundreds of far-dated books costs no venue calls. Every
        other drop category stays dropped."""
        if not getattr(self.touch, "enabled", False):
            return []
        out = []
        for d in drops:
            if d.category not in self.TOUCH_DROP_CATEGORIES:
                continue
            key = d.market.sport.value if d.market.sport else None
            if not key or not self.touch.runs(key):
                continue
            ok, why = in_window(d.market, self.touch, now)
            if not ok:
                if tally is not None:
                    k = why.split(" ", 2)[0]
                    tally.setdefault("skipped", {})[k] = tally["skipped"].get(k, 0) + 1
                continue
            out.append(d.market)
        return out

    def _touch_pass(self, sm, quote, exposure: dict, strategy_cfg, summary: dict) -> bool:
        """The touch-maker arm's turn on one quoted market (bot/touch.py).
        Posts at most one resting order per market per cycle, through the
        same risk check and executor as every other intent; routine skips
        are tallied in `summary["touch"]`, not written to the decision
        feed. Returns True when an order was placed."""
        sport_key = sm.market.sport.value if sm.market.sport else "unknown"
        if not self.touch.runs(sport_key):
            return False
        tally = summary.setdefault("touch", {"posted": 0, "vetoed": 0, "skipped": {}})
        try:
            intent, why = evaluate_touch(
                sm.market, quote, self.touch, exposure,
                self.executor.resting_sides(sm.market.market_id),
                self.maker_fee_fn,
                sport_budget=strategy_cfg.sport_budget.get(sport_key),
                style=strategy_cfg.style_override.get(sport_key, "both"),
                resting_usd=self.executor.resting_stake_by_sport().get(sport_key, 0.0),
            )
        except Exception:
            log.exception("touch evaluation failed for %s", sm.market.market_id)
            return False
        mid = ((quote.bid + quote.ask) / 2.0
               if quote.bid is not None and quote.ask is not None else None)
        if intent is None:
            key = why.split(" ", 2)[0] if why else "skip"
            tally["skipped"][key] = tally["skipped"].get(key, 0) + 1
            return False
        ok, reason = self.risk.check_intent(intent, quote)
        if not ok:
            tally["vetoed"] += 1
            self._record_decision(sm, "skip", mid, intent=intent,
                                  reason=f"touch risk veto: {reason}")
            return False
        order = self.executor.submit(intent, quote=quote)
        if order.status.value == "rejected":
            self._record_decision(sm, "skip", mid, intent=intent,
                                  reason="touch order rejected by venue")
            return False
        tally["posted"] += 1
        summary["orders"] = summary.get("orders", 0) + 1
        self._record_decision(sm, "bet", mid, intent=intent, reason=why)
        return True

    def _record_decision(self, sm, action: str, mid: float | None,
                         intent=None, reason: str | None = None) -> None:
        """Persist one line of the decision feed. Never raises — the feed is
        telemetry and must not be able to stop the loop."""
        try:
            self.store.record_decision(
                account=self.account,
                market_id=sm.market.market_id,
                sport=sm.market.sport.value if sm.market.sport else None,
                title=sm.market.slug or sm.market.market_id,
                action=action,
                side=intent.side.value if intent is not None else None,
                model_prob=sm.prediction.prob_yes if sm.prediction is not None else None,
                market_prob=mid,
                price=intent.price if intent is not None else None,
                edge=intent.edge if intent is not None else None,
                stake=(round(intent.price * intent.size, 2)
                       if intent is not None else None),
                reason=reason,
            )
        except Exception:
            log.exception("decision record failed")

    def _record_discovery_failure(self, sport_key: str, exc: BaseException) -> None:
        """A discovery error is a failure, not a warning: a cycle that saw 0
        markets because the venue call raised must say so in the feed, or
        "the bot placed no bets" reads as "nothing was worth betting". The
        reason carries the exception type so a 422 from a renamed query
        parameter (which once silently emptied the slate) is visible."""
        try:
            self.store.record_decision(
                account=self.account,
                market_id=f"({sport_key} discovery)",
                sport=sport_key,
                title=f"{sport_key} market discovery failed",
                action="skip",
                reason=f"discovery error — {type(exc).__name__}: {str(exc)[:160]}",
            )
        except Exception:
            log.exception("discovery-failure record failed")

    def _record_scan_drops(self, drops: list) -> None:
        """Put the scanner's funnel loss in the decision feed, AGGREGATED.

        One row per (sport, category), never one per market. The dashboard
        renders the most recent decisions, and the drop counts here are
        large: an unfit model loses every market in its sport, and a slate
        whose players are mostly unrated loses two markets per player.
        Written individually those would evict every real bet and skip from
        the feed within a single cycle, breaking the view this exists to
        improve.

        Grouping is on `category` rather than `reason` because the reason
        names the specific player or market — grouping on it would put a
        row per unrated player back in the feed. The specifics are not lost:
        a few are carried as examples, with a count for the rest.
        """
        if not drops:
            return
        grouped: dict[tuple[str, str], list] = {}
        for d in drops:
            sport = d.market.sport.value if d.market.sport else "unknown"
            grouped.setdefault((sport, d.category), []).append(d)
        for (sport, category), group in sorted(grouped.items(),
                                               key=lambda kv: -len(kv[1])):
            n = len(group)
            seen: list[str] = []
            for d in group:
                if d.reason not in seen:
                    seen.append(d.reason)
                if len(seen) == _DROP_EXAMPLES:
                    break
            detail = "; ".join(seen)
            extra = len({d.reason for d in group}) - len(seen)
            if extra > 0:
                detail += f"; and {extra} more"
            try:
                self.store.record_decision(
                    account=self.account,
                    # Not a venue ticker: this row stands for a group of
                    # markets, so it is labelled as one rather than
                    # impersonating a market that could be looked up.
                    market_id=f"({n} {sport} markets)",
                    sport=sport,
                    title=f"{n} {sport} market{'s' if n != 1 else ''} not scanned",
                    action="skip",
                    reason=f"dropped before pricing — {category}: {detail}",
                )
            except Exception:
                log.exception("scan-drop record failed")

    def cycle(self) -> dict:
        """One scan cycle. Returns a summary dict."""
        summary = {"markets": 0, "scanned": 0, "dropped": 0, "intents": 0,
                   "orders": 0, "arbs": 0, "settled": 0, "exits": 0,
                   "blocked": None, "touch": {"posted": 0, "vetoed": 0, "skipped": {}}}
        bets_before = len(self.store.all_bets(self.mode))
        self.executor.reconcile_open_orders()
        summary["settled"] = self._settle_resolved()
        summary["sharp_closes"] = self._sharp_backfill()
        ok, reason = self.risk.check_global()
        if not ok:
            log.warning("cycle blocked by risk: %s", reason)
            summary["blocked"] = reason
            alerts.notify("halt", f"[{self.mode}] cycle blocked: {reason}", dedupe_seconds=6 * 3600)
            return summary
        staking_cfg, strategy_cfg = self._cycle_configs()

        sports = self.cfg.get("scan", {}).get("sports", list(SPORT_KEYS))
        markets = []
        for sport_key in sports:
            if sport_key not in SPORT_KEYS:
                continue
            try:
                markets.extend(self.data_client.list_sports_markets(sport_key))
            except Exception as exc:
                log.exception("market discovery failed for %s", sport_key)
                self._record_discovery_failure(sport_key, exc)
        summary["markets"] = len(markets)
        if any(m.sport == Sport.BASEBALL for m in markets):
            markets, summary["starts_moved"] = self._mlb_start_check(markets)

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

        for model in self.models.values():
            if isinstance(model, SharpLineModel):
                model.refresh()
        scanned, drops = self.scanner.scan_verbose(markets, extra_context)
        summary["scanned"] = len(scanned)
        summary["dropped"] = len(drops)
        self._record_scan_drops(drops)
        summary["sharp"] = self._sharp_tick(scanned)

        exposure = self.store.exposure_by(mode=self.mode)
        quoted: dict[str, tuple] = {}
        edge_seen: dict[str, dict] = {}
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
            mkt_fee_fn = self.decision_fee_fn(sm.market.market_id)
            arb = find_bundle_arb(sm.market, quote, mkt_fee_fn)
            if arb:
                summary["arbs"] += 1
                log.info("ARB FOUND (log-only): %s profit=%.3f/pair x %.0f",
                         arb.description, arb.profit_per_pair, arb.max_pairs)
                self.store.set_kv(f"arb:{sm.market.market_id}", arb.__dict__)

            if self._touch_pass(sm, quote, exposure, strategy_cfg, summary):
                exposure = self.store.exposure_by()

            intent, why = evaluate_market_verbose(
                sm.market, quote, sm.prediction, staking_cfg,
                strategy_cfg, mkt_fee_fn, exposure,
                maker_fee_fn=self.maker_fee_fn,
            )
            mid = ((quote.bid + quote.ask) / 2.0
                   if quote.bid is not None and quote.ask is not None else None)
            _tally_edge(edge_seen, sm.market, intent, why, staking_cfg, strategy_cfg)
            if intent is None:
                self._record_decision(sm, "skip", mid, reason=why)
                continue
            summary["intents"] += 1
            ok, reason = self.risk.check_intent(intent, quote)
            if not ok:
                log.info("intent vetoed (%s): %s", reason, intent.market.slug)
                self._record_decision(sm, "skip", mid, intent=intent,
                                      reason=f"risk veto: {reason}")
                continue
            order = self.executor.submit(intent, quote=quote)
            if order.status.value not in ("rejected",):
                summary["orders"] += 1
                self._record_decision(sm, "bet", mid, intent=intent, reason=why)
                # keep exposure fresh within the cycle
                exposure = self.store.exposure_by(mode=self.mode)
            else:
                self._record_decision(sm, "skip", mid, intent=intent,
                                      reason="order rejected by venue")

        # The touch arm needs no price: it also quotes the markets the
        # scanner dropped for want of a sharp line in the sports it runs
        # (NHL/NFL moneylines without ODDS_API_KEY), so the paper fill
        # evidence keeps accruing when nothing else can price the slate.
        tally = summary.setdefault("touch", {"posted": 0, "vetoed": 0, "skipped": {}})
        for m in self._touch_only_markets(drops, tally=tally):
            try:
                quote = self.exchange.get_quote(m)
            except Exception:
                log.exception("quote failed for %s", m.market_id)
                continue
            quoted[m.market_id] = (m, quote)
            self.store.snapshot_quote(m.market_id, quote.bid, quote.ask)
            tally["unpriced"] = tally.get("unpriced", 0) + 1
            if self._touch_pass(_Unpriced(m), quote, exposure, strategy_cfg, summary):
                exposure = self.store.exposure_by()

        summary["exits"] = self._manage_positions(quoted, strategy_cfg)
        self._record_edge_bar(edge_seen)
        self._alert_new_fills(bets_before)

        canceled = self.executor.expire_stale_orders()
        if canceled:
            log.info("expired %d stale orders", canceled)

        try:
            eq = snapshot_equity(self.store, self.account, self.starting_balance)
            summary["equity"] = eq["equity"]
            self.store.prune_decisions()
        except Exception:  # telemetry must never take the trading loop down
            log.exception("equity snapshot failed")
        return summary

    def drain_resting(self, poll_seconds: float = 5.0,
                      max_wait: Optional[float] = None) -> dict:
        """Let the orders a cycle left resting live out their TTL, then
        settle their fate: reconcile (paper tape fills, live fills) every
        `poll_seconds` until the oldest order is past the TTL, expire the
        rest, and return what happened. For a one-shot cycle this is what
        turns a posted touch order into evidence (a fill or a shadowed
        cancel) instead of a row left `open` when the process exits.
        Bounded by `max_wait` (default TTL + 10 s)."""
        ex = self.executor
        ttl = float(ex.order_ttl)
        fills_before = len(self.store.all_bets(self.mode))
        # Wait for the NEWEST order's TTL, not the oldest's: a touch-all cycle
        # posts for minutes, and stopping at the first expiry left the late
        # posts `open` when the process exited (2026-10-13: 20 of 30), to be
        # swept as orphans without a tape shadow. Orders past their TTL are
        # expired (and shadowed) as the loop goes, so each one's window ends
        # at its own TTL.
        if max_wait is not None:
            deadline = time.time() + float(max_wait)
        elif ex._open:
            newest = max(t.placed_at for t in ex._open.values())
            remaining = ttl - (datetime.now(timezone.utc) - newest).total_seconds()
            deadline = time.time() + max(0.0, remaining) + 10.0
        else:
            deadline = time.time()
        canceled = 0
        while ex._open and time.time() < deadline:
            ex.reconcile_open_orders()
            canceled += ex.expire_stale_orders()
            if not ex._open:
                break
            time.sleep(max(0.0, min(poll_seconds, deadline - time.time())))
        ex.reconcile_open_orders()
        canceled += ex.expire_stale_orders()
        # Anything still resting (a failed cancel, or max_wait cut the wait
        # short) is cancelled now with its shadow rather than left open.
        canceled += ex.cancel_all_resting("drain_end")
        self._alert_new_fills(fills_before)
        return {"fills_booked": len(self.store.all_bets(self.mode)) - fills_before,
                "canceled": canceled, "still_resting": len(ex._open)}

    def request_stop(self, *_args) -> None:
        """Signal handler / test hook: finish the current cycle, cancel every
        resting order with its tape shadow, then return from run_forever."""
        self._stop = True

    def run_forever(self) -> None:
        interval = float(self.cfg.get("scan", {}).get("interval_seconds", 300))
        log.info("sportsbot runner starting: mode=%s venue=%s interval=%ss",
                 self.mode, self.exchange.exchange.value, interval)
        # Orders left resting by a previous process: book what filled while
        # we were away and cancel the rest on the venue, then close any row
        # still shown resting (paper books die with their process). Done HERE,
        # not in __init__, so a read-only `sportsbot scan` against a live
        # config can never cancel the running book's orders.
        try:
            self.executor.restore_open_orders()
        except Exception:
            log.exception("restore of resting orders failed; continuing")
        try:
            self.executor.sweep_orphans()
        except Exception:
            log.exception("orphan sweep failed; continuing")
        markouts = getattr(self, "markouts", None)
        if markouts is not None:
            markouts.start()
        self._stop = False
        try:
            signal.signal(signal.SIGTERM, self.request_stop)
            signal.signal(signal.SIGINT, self.request_stop)
        except ValueError:
            pass   # not the main thread (tests): the stop flag still works
        while not self._stop:
            started = time.time()
            try:
                summary = self.cycle()
                log.info("cycle done: %s", summary)
            except Exception:
                log.exception("cycle crashed; continuing")
            elapsed = time.time() - started
            deadline = time.time() + max(5.0, interval - elapsed)
            while not self._stop and time.time() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.time())))
        n = self.executor.cancel_all_resting("shutdown")
        if markouts is not None:
            markouts.stop()
        log.info("sportsbot runner stopping: %d resting order(s) cancelled with their tape shadow", n)
