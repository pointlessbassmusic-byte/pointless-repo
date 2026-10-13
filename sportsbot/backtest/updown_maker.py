"""Maker version of the crypto Up/Down "paired position" strategies, on the
public taker tape of resolved `btc-updown-5m` windows. Data only.

Result 12 measured the TAKER versions of what the X posts describe
(complete sets bought at minute prints, mean reversion): both lose. The
posts' own profitable wallet was a two-sided maker, so the question left is
whether resting bids on BOTH sides -- no fee, a 20% rebate -- turn the same
oscillations into sets that pay:

* rest a bid at T on Up and on Down when the window opens;
* when one leg fills, stop bidding that side (inventory limit) and either
  keep the other bid at T or raise it to 1 - cost - m, the "working price =
  fair - inventory penalty" completion the posts describe;
* when a set completes, optionally re-post both bids (up to `sets` per
  window); anything unpaired at the cancel time is held to resolution.

Fills are strict: a resting bid fills only when a later print trades
THROUGH it (`mode="through"`), so queue position cannot flatter it.
`mode="at"` also counts a seller-initiated print AT the bid, which assumes
the front of the queue -- an upper bound. A Down bid at d is the same
order as an Up ask at 1 - d (Polymarket matches complements), so every
print is read in the Up frame.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CACHE = "data/cache/updown5m_tape"
WINDOW = 300
EPS = 1e-9


@dataclass
class Window:
    ep: int                         # window start (epoch s)
    up_won: bool
    prints: list[tuple[int, float, int, float]]   # (ts, Up-frame price, Up flow +1/-1, size)
    fee_rate: float = 0.07
    rebate_rate: float = 0.2


def orient(rows: list[list], up: str, down: str) -> list[tuple[int, float, int, float]]:
    """Taker rows [ts, asset, side, price, size] -> Up frame. Flow +1 means
    the taker bought Up (or sold Down); -1 means the taker sold Up (or bought
    Down) -- the prints that hit Up bids."""
    out = []
    for ts, asset, side, price, size in rows:
        buy = str(side).upper() == "BUY"
        if asset == up:
            out.append((int(ts), float(price), 1 if buy else -1, float(size)))
        elif asset == down:
            out.append((int(ts), 1.0 - float(price), -1 if buy else 1, float(size)))
    out.sort()
    return out


def fetch_window(ep: int, http=None, asset: str = "btc") -> Optional[Window]:
    """One resolved window, cached on disk; None when it is missing or not
    resolved to 0/1."""
    import httpx

    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{asset}-{ep}.json")
    raw = None
    if os.path.exists(path):
        try:
            with open(path) as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = None
    if raw is None:
        http = http or httpx.Client(timeout=30)
        evs = http.get(f"{GAMMA}/events", params={"slug": f"{asset}-updown-5m-{ep}"}).json()
        if not evs:
            return None
        m = evs[0]["markets"][0]
        toks = m.get("clobTokenIds")
        toks = json.loads(toks) if isinstance(toks, str) else toks
        prices = m.get("outcomePrices")
        prices = json.loads(prices) if isinstance(prices, str) else prices
        if not m.get("closed") or not prices or float(prices[0]) not in (0.0, 1.0):
            return None
        rows: list[list] = []
        for off in range(0, 20000, 1000):
            batch = http.get(f"{DATA_API}/trades", params={
                "market": m["conditionId"], "limit": 1000, "offset": off,
                "takerOnly": "true"}).json()
            if not isinstance(batch, list):
                break
            rows += [[int(t["timestamp"]), str(t["asset"]), t["side"], float(t["price"]),
                      float(t["size"])] for t in batch]
            if len(batch) < 1000:
                break
        raw = {"ep": ep, "up": str(toks[0]), "down": str(toks[1]),
               "outcome_up": int(float(prices[0]) == 1.0),
               "fee": m.get("feeSchedule") or {}, "trades": rows}
        try:
            with open(path, "w") as fh:
                json.dump(raw, fh)
        except OSError:
            pass
    return from_raw(raw)


def from_raw(raw: dict) -> Window:
    fee = raw.get("fee") or {}
    return Window(ep=int(raw["ep"]), up_won=bool(raw["outcome_up"]),
                  prints=orient(raw["trades"], raw["up"], raw["down"]),
                  fee_rate=float(fee.get("rate", 0.07)),
                  rebate_rate=float(fee.get("rebateRate", 0.2)))


def fetch_recent(days: float, workers: int = 8, now: Optional[float] = None) -> list[Window]:
    """Every resolved window in the last `days` (the last 10 minutes are
    skipped: not resolved yet)."""
    from concurrent.futures import ThreadPoolExecutor

    end = int(now or time.time()) // WINDOW * WINDOW - 2 * WINDOW
    eps = [end - WINDOW * i for i in range(int(days * 86400 / WINDOW))]

    def one(ep):
        for attempt in range(3):
            try:
                return fetch_window(ep)
            except Exception as exc:  # noqa: BLE001 — one window is not fatal
                log.warning("window %d attempt %d: %s", ep, attempt, exc)
                time.sleep(2 * (attempt + 1))
        return None

    with ThreadPoolExecutor(workers) as ex:
        out = [w for w in ex.map(one, eps) if w is not None and w.prints]
    out.sort(key=lambda w: w.ep)
    return out


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def _fills_up(bid: float, p_up: float, flow: int, mode: str) -> bool:
    return p_up < bid - EPS or (mode == "at" and flow < 0 and p_up <= bid + EPS)


def _fills_down(bid: float, p_up: float, flow: int, mode: str) -> bool:
    ask_up = 1.0 - bid
    return p_up > ask_up + EPS or (mode == "at" and flow > 0 and p_up >= ask_up - EPS)


@dataclass
class Outcome:
    pnl: float          # per one-share legs, maker fee 0, rebate excluded
    sets: int
    unpaired: int       # legs left without a partner at the cancel time
    rebate: float       # what the maker rebate would add (not in pnl)


def simulate(w: Window, level: float, margin: Optional[float] = None,
             max_sets: int = 1, mode: str = "through", post_after: int = 0,
             cancel_before: int = 10, latency: int = 1) -> Outcome:
    """One window, one-share legs. `margin=None` keeps the second leg's bid at
    `level`; a number re-prices it to 1 - (first leg's price) - margin."""
    t_end = w.ep + WINDOW - cancel_before
    bid_up: Optional[float] = level
    bid_dn: Optional[float] = level
    posted = w.ep + post_after
    up = dn = 0
    cost = rebate = 0.0
    open_leg_price = 0.0
    for ts, p, flow, _size in w.prints:
        if ts < posted + latency:
            continue
        if ts >= t_end:
            break
        hit = False
        if bid_up is not None and _fills_up(bid_up, p, flow, mode):
            up += 1
            cost += bid_up
            rebate += w.rebate_rate * w.fee_rate * bid_up * (1 - bid_up)
            open_leg_price = bid_up
            bid_up, hit = None, True
        if bid_dn is not None and _fills_down(bid_dn, p, flow, mode):
            dn += 1
            cost += bid_dn
            rebate += w.rebate_rate * w.fee_rate * bid_dn * (1 - bid_dn)
            open_leg_price = bid_dn
            bid_dn, hit = None, True
        if not hit:
            continue
        posted = ts
        if up == dn:
            if up < max_sets:
                bid_up = bid_dn = level
            else:
                bid_up = bid_dn = None
        elif up > dn:                       # long Up: never add to it, chase Down
            bid_up = None
            bid_dn = level if margin is None else round(1.0 - open_leg_price - margin, 3)
        else:
            bid_dn = None
            bid_up = level if margin is None else round(1.0 - open_leg_price - margin, 3)
    pnl = up * (1.0 if w.up_won else 0.0) + dn * (0.0 if w.up_won else 1.0) - cost
    sets = min(up, dn)
    return Outcome(pnl=pnl, sets=sets, unpaired=up + dn - 2 * sets, rebate=rebate)


def _mean_se(vals: list[float]) -> tuple[float, float]:
    n = len(vals)
    if n < 2:
        return (vals[0] if vals else 0.0), 0.0
    mu = sum(vals) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / (n - 1))
    return mu, sd / math.sqrt(n)


def grid(windows: list[Window], levels=(0.20, 0.30, 0.40, 0.45, 0.49),
         margins=(None, 0.02, 0.01), max_sets=(1, 5), modes=("through", "at")) -> list[dict]:
    half = len(windows) // 2
    rows = []
    for mode in modes:
        for level in levels:
            for margin in margins:
                for ms in max_sets:
                    out = [simulate(w, level, margin, ms, mode) for w in windows]
                    pnl = [o.pnl for o in out]
                    mu, se = _mean_se(pnl)
                    rows.append({
                        "mode": mode, "level": level, "margin": margin, "max_sets": ms,
                        "pnl": mu, "se": se,
                        "first_half": _mean_se(pnl[:half])[0], "second_half": _mean_se(pnl[half:])[0],
                        "sets": sum(o.sets for o in out) / len(out),
                        "unpaired_windows": sum(1 for o in out if o.unpaired) / len(out),
                        "rebate": sum(o.rebate for o in out) / len(out)})
    return rows


def format_grid(rows: list[dict], windows: list[Window]) -> str:
    up = sum(w.up_won for w in windows) / len(windows) if windows else 0.0
    lines = [f"{len(windows)} resolved BTC 5-minute windows with prints "
             f"(Up won {up:.3f}); per-window PnL of one-share legs, maker fee 0, "
             f"rebate shown separately and NOT included"]
    for r in rows:
        m = "hold" if r["margin"] is None else f"{r['margin']:.2f}"
        lines.append(
            f"{r['mode']:7s} bid {r['level']:.2f} second-leg {m:>4} sets<={r['max_sets']}: "
            f"{r['pnl']:+.4f} ± {r['se']:.4f} (halves {r['first_half']:+.4f} / "
            f"{r['second_half']:+.4f}); sets/window {r['sets']:.2f}, "
            f"windows left unpaired {r['unpaired_windows']:.0%}, rebate +{r['rebate']:.4f}")
    return "\n".join(lines)
