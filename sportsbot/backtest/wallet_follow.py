"""Wallet-following test: is there copyable informed taker flow on
Polymarket sports, and does copying it clear fees?

The claim under test (X posts, 2026-10-08: "copy the profitable wallets")
reduces to two measurable questions on the public trade tape
(`data-api.polymarket.com/trades`, taker fills only):

1. **Is taker flow informed?** For every PRE-GAME taker trade, closing-line
   value at the taker's own price: the venue's last pre-start print minus
   the price paid, signed by direction. Population first, then the wallets
   a copier would have picked.
2. **Can it be copied?** Wallets are ranked on a TRAIN window (first
   `train_frac` of games by start time) by realised profit per dollar --
   what a leaderboard shows -- and, separately, by CLV. Their trades in
   the TEST window are then copied at the next taker print at least
   `delay` seconds later, paying a one-tick half spread and the 0.05 taker
   fee. Ranking on the same trades that are copied would be survivorship
   dressed as skill, so the split is by time and never revisited.

Everything is pre-game: the close is the last pre-start print, so an
in-play trade has no closing line to be graded against, and a copier
cannot match in-play latency in any case. Data only.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from sportsbot.backtest.polymarket_market import POST_RESULT_RAIL, PMGame
from sportsbot.signals.sharp import cluster_bootstrap_mean

log = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
TRADES_CACHE = "data/cache/polymarket_trades"
PAGE = 1000


@dataclass
class Trade:
    ts: int
    wallet: str
    direction: int      # +1 = long outcomes[0] (home frame), -1 = long outcomes[1]
    p_home: float       # price in the home frame
    size: float


@dataclass
class Record:
    """One pre-game taker trade with its closing-line value."""

    wallet: str
    market: str
    ts: int
    direction: int
    price: float        # paid, in the side frame
    close: float        # venue close, side frame
    won: bool

    @property
    def clv(self) -> float:
        return self.close - self.price

    @property
    def pnl_per_dollar(self) -> float:
        return (1.0 - self.price) / self.price if self.won else -1.0


# ---------------------------------------------------------------------------
# Tape
# ---------------------------------------------------------------------------
def fetch_trades(condition_id: str, http=None, pause: float = 0.2,
                 taker_only: bool = True, max_pages: int = 50) -> list[dict]:
    """Every (taker) fill on a market, oldest first, cached on disk."""
    import httpx

    os.makedirs(TRADES_CACHE, exist_ok=True)
    path = os.path.join(TRADES_CACHE, f"{condition_id}.json")
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    http = http or httpx.Client(timeout=40)
    rows: list[dict] = []
    for page in range(max_pages):
        try:
            r = http.get(f"{DATA_API}/trades", params={
                "market": condition_id, "limit": PAGE, "offset": page * PAGE,
                "takerOnly": "true" if taker_only else "false"})
            r.raise_for_status()
            batch = r.json()
        except Exception as exc:  # noqa: BLE001 — one market's tape is not fatal
            log.warning("trades failed for %s page %d: %s", condition_id, page, exc)
            return []
        if not isinstance(batch, list):
            break
        rows.extend({"ts": int(t["timestamp"]), "wallet": str(t["proxyWallet"]),
                     "asset": str(t["asset"]), "side": str(t["side"]),
                     "price": float(t["price"]), "size": float(t["size"])}
                    for t in batch if "timestamp" in t and "asset" in t)
        if len(batch) < PAGE:
            break
        time.sleep(pause)
    rows.sort(key=lambda t: t["ts"])
    try:
        with open(path, "w") as fh:
            json.dump(rows, fh)
    except OSError:
        pass
    time.sleep(pause)
    return rows


def orient(rows: Iterable[dict], game: PMGame) -> list[Trade]:
    """Trade rows -> home-frame trades. A BUY of the complement token is a
    short of the home outcome at 1 - price; a SELL of it is a long."""
    out = []
    for t in rows:
        if t["asset"] == game.token:
            long_home = t["side"].upper() == "BUY"
            p_home = t["price"]
        elif t["asset"] == game.token_no:
            long_home = t["side"].upper() != "BUY"
            p_home = 1.0 - t["price"]
        else:
            continue
        if not (0.0 < p_home < 1.0):
            continue
        out.append(Trade(ts=t["ts"], wallet=t["wallet"],
                         direction=1 if long_home else -1,
                         p_home=p_home, size=t["size"]))
    return out


def venue_close(trades: list[Trade], start_ts: int) -> Optional[float]:
    """Last pre-start print in the home frame; refused at the rail, where a
    'closing' price is really the result (late start time, decided match)."""
    pre = [t for t in trades if t.ts < start_ts]
    if not pre:
        return None
    c = pre[-1].p_home
    if not (POST_RESULT_RAIL < c < 1.0 - POST_RESULT_RAIL):
        return None
    return c


def side_price(p_home: float, direction: int) -> float:
    return p_home if direction > 0 else 1.0 - p_home


def copy_price(trades: list[Trade], i: int, delay: float, start_ts: int,
               half_spread: float = 0.005) -> Optional[float]:
    """What a copier pays for trade `i`'s side: the next print at least
    `delay` seconds later and still pre-start, plus a half spread. None
    when no such print exists -- the copy is skipped, not imagined."""
    t0 = trades[i]
    for t in trades[i + 1:]:
        if t.ts >= start_ts:
            return None
        if t.ts - t0.ts >= delay:
            return min(0.999, side_price(t.p_home, t0.direction) + half_spread)
    return None


def taker_fee(price: float, rate: float = 0.05) -> float:
    return rate * price * (1.0 - price)


# ---------------------------------------------------------------------------
# Maker version: rest a bid in the informed direction instead of crossing
# ---------------------------------------------------------------------------
TICK = 0.01


def ask_consumed(trades: list[Trade], i: int, horizon: float = 60.0,
                 latency: float = 2.0) -> Optional[bool]:
    """Could a post-only bid AT trade `i`'s price have rested? Proxy from the
    tape: the next same-direction taker print within `horizon` seconds. Above
    the signal price -> that ask level was used up, a bid there is the new
    best bid. At or below -> the ask is still there and the bid would cross
    (a taker fill, fee and all). None when nothing prints in time."""
    t0 = trades[i]
    p0 = side_price(t0.p_home, t0.direction)
    for t in trades[i + 1:]:
        if t.ts - t0.ts > horizon:
            return None
        if t.direction == t0.direction and t.ts >= t0.ts + latency:
            return side_price(t.p_home, t0.direction) > p0 + 1e-9
    return None


def maker_fill(trades: list[Trade], i: int, limit: float, direction: int,
               end_ts: int, latency: float = 2.0, through: bool = True) -> bool:
    """Strict fill for a bid on `direction`'s side resting from trade `i`
    (+latency) until `end_ts`: some later print in that side's frame trades
    BELOW the bid (`through`) -- the level was swept, so queue position does
    not matter. `through=False` also counts prints AT the bid, which assumes
    the front of the queue: an upper bound, never the headline."""
    t0 = trades[i]
    for t in trades[i + 1:]:
        if t.ts >= end_ts:
            return False
        if t.ts < t0.ts + latency:
            continue
        q = side_price(t.p_home, direction)
        if (q < limit - 1e-9) if through else (q <= limit + 1e-9):
            return True
    return False


def maker_follow(games: list[PMGame], tapes: dict[str, list[Trade]],
                 wallets: set[str], window_s: int = 600, through: bool = True,
                 latency: float = 2.0) -> dict:
    """Follow `wallets` with a resting bid instead of a taker copy. Placement
    is what a post-only order could actually do: AT the signal price when the
    ask there was used up (`ask_consumed`), else one tick lower. One live
    order per market side; cancelled after `window_s` or at the start. Gain
    is close minus our limit, no fee (the maker rebate is ignored)."""
    gains, cl = [], []
    signals = filled = at_price = 0
    for g in games:
        k = g.condition_id or g.token
        trades = tapes.get(k, [])
        close = venue_close(trades, g.start_ts)
        if close is None:
            continue
        busy: dict[int, int] = {}
        for i, t in enumerate(trades):
            if t.ts >= g.start_ts:
                break
            if t.wallet not in wallets or busy.get(t.direction, -1) > t.ts:
                continue
            p = side_price(t.p_home, t.direction)
            rests_at_price = ask_consumed(trades, i, latency=latency) is True
            limit = round(p if rests_at_price else p - TICK, 2)
            if limit <= 0.02:
                continue
            signals += 1
            at_price += rests_at_price
            end = min(g.start_ts, t.ts + window_s)
            busy[t.direction] = end
            if maker_fill(trades, i, limit, t.direction, end, latency, through):
                filled += 1
                gains.append(side_price(close, t.direction) - limit)
                cl.append(k)
    return {"signals": signals, "rests_at_signal_price": at_price,
            "filled": filled, "fill_rate": filled / signals if signals else 0.0,
            "filled_clv": _ci(gains, cl),
            "ev_per_signal": (sum(gains) / signals) if signals else 0.0}


# ---------------------------------------------------------------------------
# Records and ranking
# ---------------------------------------------------------------------------
def records_for(game: PMGame, trades: list[Trade]) -> list[Record]:
    close = venue_close(trades, game.start_ts)
    if close is None:
        return []
    out = []
    for t in trades:
        if t.ts >= game.start_ts:
            break
        won = game.home_won if t.direction > 0 else not game.home_won
        out.append(Record(wallet=t.wallet, market=game.condition_id or game.token,
                          ts=t.ts, direction=t.direction,
                          price=side_price(t.p_home, t.direction),
                          close=side_price(close, t.direction), won=won))
    return out


def wallet_table(records: Iterable[Record]) -> dict[str, dict]:
    by: dict[str, list[Record]] = {}
    for r in records:
        by.setdefault(r.wallet, []).append(r)
    out = {}
    for w, rs in by.items():
        n = len(rs)
        clvs = [r.clv for r in rs]
        pnls = [r.pnl_per_dollar for r in rs]
        m = sum(clvs) / n
        sd = math.sqrt(sum((c - m) ** 2 for c in clvs) / (n - 1)) if n > 1 else 0.0
        out[w] = {"n": n, "markets": len({r.market for r in rs}),
                  "mean_clv": m, "t_clv": (m / (sd / math.sqrt(n)) if sd > 0 else 0.0),
                  "pnl_per_dollar": sum(pnls) / n}
    return out


def select_wallets(table: dict[str, dict], key: str, top: int,
                   min_trades: int, min_markets: int = 5) -> list[str]:
    """Top `top` wallets by `key` among those with enough train history."""
    ok = [(w, d) for w, d in table.items()
          if d["n"] >= min_trades and d["markets"] >= min_markets]
    ok.sort(key=lambda wd: -wd[1][key])
    return [w for w, _ in ok[:top]]


def split_games(games: list[PMGame], train_frac: float) -> tuple[list[PMGame], list[PMGame]]:
    games = sorted(games, key=lambda g: g.start_ts)
    k = int(len(games) * train_frac)
    return games[:k], games[k:]


def _ci(vals: list[float], clusters: list[str]) -> Optional[dict]:
    return cluster_bootstrap_mean(vals, clusters, n_boot=1000, seed=7)


def run(games: list[PMGame], tapes: dict[str, list[Trade]],
        train_frac: float = 0.7, top: int = 20, min_trades: int = 20,
        delay: float = 30.0, fee_rate: float = 0.05,
        half_spread: float = 0.005, maker: bool = False) -> dict:
    """The whole test. `tapes` maps game key (condition_id or token) ->
    oriented trades. Returns a dict of measured quantities; no verdict
    text, the caller formats."""
    train, test = split_games(games, train_frac)

    def recs(gs):
        out = []
        for g in gs:
            out.extend(records_for(g, tapes.get(g.condition_id or g.token, [])))
        return out

    train_recs, test_recs = recs(train), recs(test)
    res = {"games": len(games), "train_games": len(train), "test_games": len(test),
           "train_trades": len(train_recs), "test_trades": len(test_recs)}
    res["population_train"] = _ci([r.clv for r in train_recs], [r.market for r in train_recs])
    res["population_test"] = _ci([r.clv for r in test_recs], [r.market for r in test_recs])
    res["population_test_pnl"] = _ci([r.pnl_per_dollar for r in test_recs],
                                     [r.market for r in test_recs])

    table = wallet_table(train_recs)
    res["train_wallets"] = len(table)
    res["train_wallets_eligible"] = sum(1 for d in table.values() if d["n"] >= min_trades)
    test_by_key = {}
    for g in test:
        test_by_key[g.condition_id or g.token] = g

    for key in ("pnl_per_dollar", "mean_clv"):
        chosen = select_wallets(table, key, top, min_trades)
        own, own_c, own_pnl = [], [], []
        copy_clv, copy_c, copy_pnl, skipped = [], [], [], 0
        copy_gross = []                      # close - next print, no costs
        per_wallet: dict[str, list[float]] = {}
        for g in test:
            k = g.condition_id or g.token
            trades = tapes.get(k, [])
            close = venue_close(trades, g.start_ts)
            if close is None:
                continue
            for i, t in enumerate(trades):
                if t.ts >= g.start_ts:
                    break
                if t.wallet not in chosen:
                    continue
                won = g.home_won if t.direction > 0 else not g.home_won
                own.append(side_price(close, t.direction) - side_price(t.p_home, t.direction))
                own_c.append(k)
                per_wallet.setdefault(t.wallet, []).append(own[-1])
                own_pnl.append(((1.0 - side_price(t.p_home, t.direction))
                                / side_price(t.p_home, t.direction)) if won else -1.0)
                cp = copy_price(trades, i, delay, g.start_ts, half_spread)
                if cp is None:
                    skipped += 1
                    continue
                fee = taker_fee(cp, fee_rate)
                copy_gross.append(side_price(close, t.direction) - (cp - half_spread))
                copy_clv.append(side_price(close, t.direction) - cp - fee)
                copy_c.append(k)
                copy_pnl.append(((1.0 - cp - fee) / cp) if won else (-(cp + fee) / cp))
        res[f"by_{key}"] = {
            "wallets": chosen,
            "train_stats": {w: table[w] for w in chosen},
            "own_price_clv": _ci(own, own_c),
            "own_price_pnl": _ci(own_pnl, own_c),
            "copy_gross_clv": _ci(copy_gross, copy_c),
            "copy_net_clv": _ci(copy_clv, copy_c),
            "copy_net_pnl": _ci(copy_pnl, copy_c),
            "copies_skipped_no_later_print": skipped,
            "test_per_wallet": {w: {"n": len(v), "mean_clv": sum(v) / len(v)}
                                for w, v in per_wallet.items()},
        }
        if maker and key == "mean_clv":
            busiest = max(per_wallet, key=lambda w: len(per_wallet[w]), default=None)
            sets = {"all": set(chosen), "without busiest": set(chosen) - {busiest}}
            res["maker"] = {
                f"{label}, cancel {w // 60}m, {'through' if th else 'at-or-through'}":
                    maker_follow(test, tapes, ws, window_s=w, through=th)
                for label, ws in sets.items() for w in (600, 3600) for th in (True, False)}
    return res


def _fmt(d: Optional[dict]) -> str:
    if not d:
        return "—"
    if d["lo"] is None:
        return f"{d['mean']:+.4f} (n={d['n']})"
    return f"{d['mean']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}] n={d['n']} markets={d['clusters']}"


def format_report(res: dict, sport: str) -> str:
    lines = [
        f"wallet-following test, Polymarket {sport}: {res['games']} resolved games, "
        f"train {res['train_games']} / test {res['test_games']} by start time",
        f"pre-game taker trades: train {res['train_trades']}, test {res['test_trades']}; "
        f"train wallets {res['train_wallets']} (eligible {res['train_wallets_eligible']})",
        f"population CLV at own price, train: {_fmt(res['population_train'])}",
        f"population CLV at own price, test:  {_fmt(res['population_test'])}",
        f"population realised return/$, test: {_fmt(res['population_test_pnl'])}",
    ]
    for key, label in (("pnl_per_dollar", "realised profit/$ (what a leaderboard shows)"),
                       ("mean_clv", "closing-line value")):
        d = res[f"by_{key}"]
        lines.append(f"top {len(d['wallets'])} train wallets by {label}, graded on TEST games:")
        lines.append(f"  own-price CLV      {_fmt(d['own_price_clv'])}")
        lines.append(f"  own-price return/$ {_fmt(d['own_price_pnl'])}")
        lines.append(f"  next-print CLV     {_fmt(d['copy_gross_clv'])}  "
                     f"(close - next print >= delay later, no costs: the information left)")
        lines.append(f"  copy net CLV       {_fmt(d['copy_net_clv'])}  "
                     f"(+ half spread + taker fee; "
                     f"{d['copies_skipped_no_later_print']} uncopyable)")
        lines.append(f"  copy net return/$  {_fmt(d['copy_net_pnl'])}")
        pw = sorted(d["test_per_wallet"].items(), key=lambda kv: -kv[1]["n"])
        if pw:
            top = ", ".join(f"{w[:6]}…×{v['n']} ({v['mean_clv']:+.4f})" for w, v in pw[:5])
            lines.append(f"  test trades by wallet: {len(pw)} active of {len(d['wallets'])}; "
                         f"busiest {top}")
    if res.get("maker"):
        lines.append("maker-follow of the top CLV wallets on TEST games (post-only bid at the "
                     "signal price if that ask was used up, else one tick lower; no fee):")
        for label, m in res["maker"].items():
            lines.append(f"  {label}: signals {m['signals']} ({m['rests_at_signal_price']} at "
                         f"signal price), fill {m['fill_rate']:.1%}, filled CLV "
                         f"{_fmt(m['filled_clv'])}, EV/signal {m['ev_per_signal']:+.4f}")
    return "\n".join(lines)
