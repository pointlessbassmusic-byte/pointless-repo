"""Wallet scan: are Polymarket's top wallets, in any category, worth copying?
READ-ONLY public data, no keys, nothing wired to trading.

Results 14 and 18 answered this for MLB: the best wallets are informed,
but a copier loses the fee at any delay. The user asked the same question
across every category: politics, crypto, economics, culture, weather and
the rest. Only polymarket.com exposes wallets; Kalshi and Polymarket US are
anonymous, and stocks have no per-trader public trail.

Design, built against leaderboard survivorship:

* pool: the public leaderboard's top wallets by P&L in each category, all
  time and this month. Being on it is partly luck, so nothing is concluded
  from leaderboard P&L;
* per wallet, its most recent `max_rows` activity rows split in TIME into
  an older half (rank) and a newer half (test);
* per trade (`wallet_review.WalletReview.grade`): gross settlement P&L,
  24 h mark-out, and the COPY: a follower who enters `copy_delay` later at
  the then-current price plus a half-spread plus the taker fee, held to
  settlement. Price histories are sampled at up to `max_tokens` tokens per
  wallet;
* two-sided share: the fraction of a wallet's markets where it bought both
  outcomes. Market makers score high, and copying a maker's fills as
  directional bets is meaningless;
* verdict: whether the older half's mark-out predicts the newer half's
  across wallets, and the newer-half copy P&L of the wallets ranked best
  on the older half, with errors clustered by market.
"""

from __future__ import annotations

import hashlib
import logging
import math
import statistics
from collections import defaultdict
from typing import Optional

from sportsbot.backtest import wallet_review as wr

log = logging.getLogger(__name__)

LEADERBOARD = "https://data-api.polymarket.com/v1/leaderboard"
CATEGORIES = ("OVERALL", "POLITICS", "SPORTS", "CRYPTO", "ECONOMICS", "FINANCE", "CULTURE",
              "WEATHER", "TECH", "MENTIONS")


def pool(review_http, per_category: int = 5, month: int = 3) -> dict[str, dict]:
    """Leaderboard wallets -> {address: {name, categories, pnl, vol}}."""
    out: dict[str, dict] = {}
    for cat in CATEGORIES:
        for period, n in (("ALL", per_category), ("MONTH", month)):
            r = review_http.get(LEADERBOARD, params={"timePeriod": period, "orderBy": "PNL",
                                                     "limit": n, "category": cat})
            if r.status_code != 200:
                continue
            for row in r.json() or []:
                addr = str(row.get("proxyWallet") or "").lower()
                if not wr.ADDRESS_RE.match(addr):
                    continue
                w = out.setdefault(addr, {"name": row.get("userName") or addr[:10], "categories": set(),
                                          "pnl": 0.0, "vol": 0.0})
                w["categories"].add(f"{cat}/{period}")
                w["pnl"] = max(w["pnl"], float(row.get("pnl") or 0))
                w["vol"] = max(w["vol"], float(row.get("vol") or 0))
    return out


def _sampled(token: str, keep: float) -> bool:
    """Deterministic token sample: the same tokens every run."""
    return int(hashlib.sha1(token.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < keep


def copy_value(g: wr.Graded, hist: list[tuple[float, float]], delay: float, fee_rate: float,
               half_spread: float = 0.005) -> Optional[float]:
    """Settlement P&L per share of copying trade g `delay` seconds later.
    A BUY is copied as a BUY of the same outcome; a SELL as a BUY of the
    other outcome. Skipped when the game started before the copy."""
    if g.payout is None:
        return None
    t = g.ts + delay
    if g.extra.get("start_ts") and t >= g.extra["start_ts"]:
        return None
    px = next((p for ts, p in hist if ts >= t), None)
    if px is None or not (0.01 < px < 0.99):
        return None
    if g.side == "BUY":
        entry, won = px + half_spread, g.payout
    else:
        entry, won = (1.0 - px) + half_spread, 1.0 - g.payout
    if entry >= 1.0:
        return None
    return won - entry - fee_rate * entry * (1 - entry)


class Scanner:
    def __init__(self, review_factory=wr.WalletReview, max_rows: int = 3000, max_tokens: int = 120,
                 copy_delay: float = 600.0):
        self.factory = review_factory
        self.max_rows = max_rows
        self.max_tokens = max_tokens
        self.copy_delay = copy_delay

    def wallet(self, address: str) -> dict:
        rv = self.factory(address)
        rows = rv.activity(max_rows=self.max_rows)[: self.max_rows]
        trades = [r for r in rows if r.get("type") == "TRADE"]
        if len(trades) < 20:
            return {"address": address, "trades": len(trades), "skipped": "too few trades"}
        markets = rv.markets(r.get("conditionId") for r in trades)
        graded = rv.grade(trades, markets, with_history=False)
        # two-sided share: markets where the wallet bought both outcomes
        bought = defaultdict(set)
        for g in graded:
            if g.side == "BUY":
                bought[g.condition_id].add(g.outcome_index)
        two_sided = sum(1 for s in bought.values() if len(s) > 1) / max(1, len(bought))
        # sampled histories for the 24 h mark-out and the copy
        tokens = {}
        for g in graded:
            mk = markets.get(g.condition_id)
            if mk and g.outcome_index < len(mk.tokens):
                tokens.setdefault(mk.tokens[g.outcome_index], []).append(g)
                if mk.start_ts:
                    g.extra["start_ts"] = mk.start_ts
        keep = min(1.0, self.max_tokens / max(1, len(tokens)))
        hist: dict[str, list] = {}
        for tok, gs in tokens.items():
            if not _sampled(tok, keep):
                continue
            lo = min(g.ts for g in gs)
            hi = max(g.ts for g in gs) + 2 * 86400
            try:
                hist[tok] = rv.history(tok, lo - 3600, hi)
            except Exception as exc:  # noqa: BLE001 — a missing history is a missing mark-out
                log.debug("history failed %s: %s", tok[:10], exc)
        for tok, gs in tokens.items():
            h = hist.get(tok)
            if not h:
                continue
            for g in gs:
                mk = markets[g.condition_id]
                sign = 1.0 if g.side == "BUY" else -1.0
                end = mk.start_ts or mk.end_ts
                t = g.ts + 86400 if not end else min(g.ts + 86400, end)
                if t > g.ts:
                    px = wr._at_or_before(h, t, after=g.ts)
                    if px is not None:
                        g.markout_24h = sign * (px - g.price)
                g.extra["copy"] = copy_value(g, h, self.copy_delay, mk.fee_rate)
        graded.sort(key=lambda g: g.ts)
        half = len(graded) // 2
        return {"address": address, "trades": len(graded), "two_sided": two_sided,
                "first_ts": graded[0].ts, "last_ts": graded[-1].ts,
                "older": _half_stats(graded[:half]), "newer": _half_stats(graded[half:]),
                "newer_copy_rows": [(g.extra["copy"], g.condition_id) for g in graded[half:]
                                    if g.extra.get("copy") is not None]}


def _mean_se(pairs: list[tuple[float, str]]) -> tuple[Optional[float], Optional[float], int]:
    if not pairs:
        return None, None, 0
    vals = [v for v, _ in pairs]
    m = statistics.mean(vals)
    by: dict[str, float] = defaultdict(float)
    for v, g in pairs:
        by[g] += v - m
    se = math.sqrt(sum(x * x for x in by.values())) / len(vals) if len(vals) > 1 else None
    return m, se, len(vals)


def _half_stats(gs: list[wr.Graded]) -> dict:
    out = {}
    for key, fn in (("settle", lambda g: g.pnl_share), ("markout_24h", lambda g: g.markout_24h),
                    ("copy", lambda g: g.extra.get("copy"))):
        m, se, n = _mean_se([(fn(g), g.condition_id) for g in gs if fn(g) is not None])
        out[key] = {"mean": m, "se": se, "n": n}
    return out


def verdict(results: list[dict], top_frac: float = 0.25, min_n: int = 15) -> dict:
    """Does the older half rank the newer half, and does copying the best pay?"""
    usable = [r for r in results if "older" in r and r["older"]["markout_24h"]["n"] >= min_n
              and r["newer"]["markout_24h"]["n"] >= min_n]
    out: dict = {"wallets": len(results), "usable": len(usable)}
    if len(usable) >= 4:
        xs = [r["older"]["markout_24h"]["mean"] for r in usable]
        ys = [r["newer"]["markout_24h"]["mean"] for r in usable]
        out["markout_rank_corr"] = _spearman(xs, ys)
        ranked = sorted(usable, key=lambda r: -r["older"]["markout_24h"]["mean"])
        top = ranked[: max(1, int(len(ranked) * top_frac))]
        rest = ranked[len(top):]
        for name, grp in (("top", top), ("rest", rest)):
            rows = [row for r in grp for row in r["newer_copy_rows"]]
            m, se, n = _mean_se(rows)
            out[f"{name}_newer_copy"] = {"wallets": len(grp), "mean": m, "se": se, "n": n}
            mo = [(r["newer"]["markout_24h"]["mean"], r["address"]) for r in grp]
            out[f"{name}_newer_markout"] = statistics.mean(v for v, _ in mo)
    return out


def _spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for k, i in enumerate(order):
            r[i] = float(k)
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def format_scan(pool_info: dict[str, dict], results: list[dict], v: dict) -> str:
    def f(d):
        if not d or d.get("mean") is None:
            return "—"
        se = f"±{d['se'] * 100:.1f}" if d.get("se") is not None else ""
        return f"{d['mean'] * 100:+.1f}{se} ({d['n']})"

    lines = [f"{'wallet':20s} {'categories':28s} {'2-sided':>7s} {'trades':>6s}  "
             f"{'older 24h mark-out':>20s} {'newer 24h mark-out':>20s} {'newer copy, after fee':>22s}"]
    for r in sorted(results, key=lambda r: -(pool_info.get(r["address"], {}).get("pnl") or 0)):
        info = pool_info.get(r["address"], {})
        cats = ",".join(sorted({c.split("/")[0] for c in info.get("categories", [])}))[:28]
        if "older" not in r:
            lines.append(f"{info.get('name', r['address'][:10])[:20]:20s} {cats:28s} {'':>7s} {r['trades']:6d}  "
                         f"skipped: {r.get('skipped')}")
            continue
        lines.append(f"{info.get('name', r['address'][:10])[:20]:20s} {cats:28s} {r['two_sided']:7.0%} "
                     f"{r['trades']:6d}  {f(r['older']['markout_24h']):>20s} {f(r['newer']['markout_24h']):>20s} "
                     f"{f(r['newer']['copy']):>22s}")
    lines.append("")
    lines.append(f"usable wallets {v.get('usable')} of {v.get('wallets')}; "
                 f"older-vs-newer 24 h mark-out rank correlation {v.get('markout_rank_corr', float('nan')):+.2f}")
    for name in ("top", "rest"):
        d = v.get(f"{name}_newer_copy")
        if d:
            lines.append(f"{name} quarter by older mark-out ({d['wallets']} wallets): newer-half copy "
                         f"{f(d)} pt per share after a {int(600 / 60)}-min delay, half-spread and fee; "
                         f"newer mark-out {v[f'{name}_newer_markout'] * 100:+.1f} pt")
    return "\n".join(lines)
