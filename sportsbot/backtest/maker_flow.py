"""Who earns on Polymarket sports: the MAKER side of every pre-game taker
fill, graded against the venue close and held to settlement, split by
trailing VPIN, fill size, price and lead. Data only.

Rank 3 of `reports/Beating prediction market prices.md`: Bartlett & O'Hara
find maker profit carried to settlement and losses concentrated in high
trailing-VPIN buckets. The public taker tape (`wallet_follow.fetch_trades`)
gives every fill's maker side for free: it holds the opposite direction at
the taker's price. This module asks whether that side is paid, and where.

VPIN here is the standard volume-synchronised version per market: pre-game
dollar volume cut into `buckets` equal buckets (a fill spanning a boundary
is split), imbalance |buy - sell| / volume over the trailing `window`
buckets, measured BEFORE the fill. Absolute thresholds from other venues do
not transfer, so the report splits by terciles fitted on the train games.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sportsbot.backtest.polymarket_market import PMGame
from sportsbot.backtest.wallet_follow import (
    Trade, _ci, _fmt, side_price, split_games, venue_close)


@dataclass
class MakerFill:
    market: str
    vpin: Optional[float]
    clv: float          # close - maker's price, maker's side frame
    settle: float       # payoff - maker's price
    usd: float          # fill notional at the taker's price
    price: float        # maker's price, own side frame
    hours: float        # before the start


def vpin_series(trades: list[Trade], buckets: int = 50, window: int = 20) -> list[Optional[float]]:
    """Trailing VPIN before each trade (None until `window` buckets exist)."""
    vol = sum(t.size * side_price(t.p_home, t.direction) for t in trades)
    if vol <= 0:
        return [None] * len(trades)
    size = vol / buckets
    done: list[tuple[float, float]] = []
    buy = sell = filled = 0.0
    out: list[Optional[float]] = []
    for t in trades:
        if len(done) >= window:
            recent = done[-window:]
            tot = sum(b + s for b, s in recent)
            out.append(sum(abs(b - s) for b, s in recent) / tot if tot > 0 else None)
        else:
            out.append(None)
        left = t.size * side_price(t.p_home, t.direction)
        while left > 1e-12:
            take = min(left, size - filled)
            if t.direction > 0:
                buy += take
            else:
                sell += take
            filled += take
            left -= take
            if filled >= size - 1e-9:
                done.append((buy, sell))
                buy = sell = filled = 0.0
    return out


def maker_fills(game: PMGame, trades: list[Trade], buckets: int = 50,
                window: int = 20) -> list[MakerFill]:
    close = venue_close(trades, game.start_ts)
    if close is None:
        return []
    pre = [t for t in trades if t.ts < game.start_ts]
    vps = vpin_series(pre, buckets, window)
    y = 1.0 if game.home_won else 0.0
    out = []
    for t, vp in zip(pre, vps):
        m = -t.direction                       # the maker took the other side
        price = side_price(t.p_home, m)
        out.append(MakerFill(
            market=game.condition_id or game.token, vpin=vp,
            clv=side_price(close, m) - price,
            settle=(y if m > 0 else 1.0 - y) - price,
            usd=t.size * side_price(t.p_home, t.direction), price=price,
            hours=(game.start_ts - t.ts) / 3600))
    return out


def _tercile_edges(vals: list[float]) -> tuple[float, float]:
    v = sorted(vals)
    return v[len(v) // 3], v[2 * len(v) // 3]


def run(games: list[PMGame], tapes: dict[str, list[Trade]], train_frac: float = 0.7,
        fee_rate: float = 0.05, rebate_rate: float = 0.2) -> dict:
    train, test = split_games(games, train_frac)

    def fills(gs):
        out = []
        for g in gs:
            out.extend(maker_fills(g, tapes.get(g.condition_id or g.token, [])))
        return out

    tr, te = fills(train), fills(test)
    edges = _tercile_edges([f.vpin for f in tr if f.vpin is not None]) if tr else (0.0, 0.0)

    def split(fs: list[MakerFill]) -> dict:
        def stat(sel, attr="clv"):
            return _ci([getattr(f, attr) for f in sel], [f.market for f in sel])
        res = {"n": len(fs), "games": len({f.market for f in fs}),
               "clv": stat(fs), "settle": stat(fs),
               "rebate": (sum(rebate_rate * fee_rate * f.price * (1 - f.price) for f in fs)
                          / len(fs)) if fs else 0.0}
        res["settle"] = stat(fs, "settle")
        lo, hi = edges
        res["vpin"] = {
            "low": stat([f for f in fs if f.vpin is not None and f.vpin < lo]),
            "mid": stat([f for f in fs if f.vpin is not None and lo <= f.vpin < hi]),
            "high": stat([f for f in fs if f.vpin is not None and f.vpin >= hi]),
            "undefined": stat([f for f in fs if f.vpin is None])}
        res["size"] = {f"[{a},{b})": stat([f for f in fs if a <= f.usd < b])
                       for a, b in ((0, 10), (10, 100), (100, 1000), (1000, 10 ** 12))}
        res["price"] = {f"[{a},{b})": stat([f for f in fs if a <= f.price < b])
                        for a, b in ((0, .3), (.3, .45), (.45, .55), (.55, .7), (.7, 1.01))}
        return res

    return {"vpin_terciles": edges, "train": split(tr), "test": split(te)}


def format_report(res: dict, sport: str) -> str:
    lo, hi = res["vpin_terciles"]
    lines = [f"maker side of every pre-game taker fill, Polymarket {sport} "
             f"(maker fee 0; rebate shown, not added); VPIN terciles from train: "
             f"{lo:.3f} / {hi:.3f}"]
    for part in ("train", "test"):
        d = res[part]
        lines.append(f"{part.upper()}: {d['n']} fills on {d['games']} games")
        lines.append(f"  CLV to close        {_fmt(d['clv'])}")
        lines.append(f"  held to settlement  {_fmt(d['settle'])}")
        lines.append(f"  rebate if every fill were ours: +{d['rebate']:.4f}")
        for k, v in d["vpin"].items():
            lines.append(f"  VPIN {k:9s} CLV {_fmt(v)}")
        for k, v in d["size"].items():
            lines.append(f"  fill ${k:16s} CLV {_fmt(v)}")
        for k, v in d["price"].items():
            lines.append(f"  maker price {k:10s} CLV {_fmt(v)}")
    return "\n".join(lines)
