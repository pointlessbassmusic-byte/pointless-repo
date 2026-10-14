"""Fresh-wallet flow: do brand-new Polymarket wallets that put real money
on cheap outcomes know something, and can a follower still earn it?
READ-ONLY public data; nothing here trades.

The pattern leaked information leaves on a public ledger is a wallet with
no history buying a long shot in size shortly before news. Leaderboard
wallets (wallet_scan) and the best-CLV sports wallets (Results 14, 18) are
a different population. This test was written down before the first run
(2026-10-14); the definitions below are the pre-registration.

* universe: resolved binary markets whose scheduled end fell in the last
  `days`, volume >= $50k, NOT sports and NOT crypto (wallet_review's
  categoriser): politics, economics, culture, tech, weather, other;
* candidate trade: a taker BUY of >= $1,000 at a price <= 0.35, before
  the market's scheduled end; one row per (wallet, market, outcome), the
  first such trade;
* signal: the wallet's first-ever Polymarket activity is <= 14 days
  before the trade. Control: every other candidate trade;
* measures, each a mean per share, clustered by market:
  - surplus = outcome - price paid (did the long shot win more often than
    its price said?);
  - copy P&L for a follower who buys the same outcome 10 minutes and
    1 hour later at the then-current price (price history) plus a
    half-spread and the market's taker fee, held to settlement.
* the bar, written first: signal copy P&L at 10 minutes > 0 with t >= 2
  AND above the control's by t >= 2; anything less is "no edge". A
  surplus without a copy profit means the information was real but
  priced in before a follower could act.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sportsbot.backtest import wallet_review as wr

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com/markets"
TRADES = "https://data-api.polymarket.com/trades"
ACTIVITY = "https://data-api.polymarket.com/activity"
CACHE = "data/cache/fresh_wallet"
MIN_VOLUME = 50_000
MIN_CASH = 1_000
MAX_PRICE = 0.35
FRESH_DAYS = 14
DELAYS = (600, 3600)
HALF_SPREAD = 0.005
EXCLUDED = ("sports", "crypto")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Study:
    def __init__(self, http=None, cache: str = CACHE, pause: float = 0.12, sleep=time.sleep, clock=time.time):
        if http is None:
            import httpx
            http = httpx.Client(timeout=30, headers={"User-Agent": "sportsbot/1.0"})
        self.http, self.cache, self.pause, self.sleep, self.clock = http, cache, pause, sleep, clock
        os.makedirs(cache, exist_ok=True)

    def _get(self, url, params):
        for wait in (1.0, 2.0, 4.0, 8.0, None):
            r = self.http.get(url, params=params)
            if r.status_code == 429 and wait is not None:
                self.sleep(wait)
                continue
            r.raise_for_status()
            self.sleep(self.pause)
            return r.json()
        return None

    def _cached(self, name: str, fn):
        path = os.path.join(self.cache, name)
        if os.path.exists(path):
            with open(path) as fh:
                return json.load(fh)
        val = fn()
        with open(path, "w") as fh:
            json.dump(val, fh)
        return val

    # -------------------------------------------------------------- data
    def markets(self, days: int) -> list[dict]:
        """Resolved, liquid, non-sports non-crypto binaries, by 5-day end-date slices."""
        now = self.clock()
        out: dict[str, dict] = {}
        t = now - days * 86400
        while t < now:
            t2 = min(now, t + 5 * 86400)
            offset = 0
            while offset < 2000:
                rows = self._get(GAMMA, {"closed": "true", "limit": 500, "offset": offset,
                                         "end_date_min": _iso(t), "end_date_max": _iso(t2),
                                         "volume_num_min": MIN_VOLUME}) or []
                for m in rows:
                    mk = wr.Market.from_gamma(m)
                    ev = (m.get("events") or [{}])[0] or {}
                    cat = wr.category(str(ev.get("slug") or m.get("slug") or ""), str(m.get("question") or ""))
                    if cat in EXCLUDED or len(mk.payouts) != 2 or mk.payouts[0] is None:
                        continue
                    out[mk.condition_id] = {"cid": mk.condition_id, "slug": mk.slug, "category": cat,
                                            "end_ts": mk.end_ts, "payouts": mk.payouts, "tokens": mk.tokens,
                                            "fee_rate": mk.fee_rate}
                if len(rows) < 500:
                    break
                offset += 500
            t = t2
        return list(out.values())

    def big_buys(self, cid: str) -> list[dict]:
        rows, offset = [], 0
        while offset < 3000:
            page = self._get(TRADES, {"market": cid, "filterType": "CASH", "filterAmount": MIN_CASH,
                                      "limit": 500, "offset": offset, "takerOnly": "true"}) or []
            rows.extend(page)
            if len(page) < 500:
                break
            offset += 500
        return [r for r in rows if str(r.get("side")).upper() == "BUY"]

    def first_seen(self, wallet: str) -> Optional[float]:
        a = self._get(ACTIVITY, {"user": wallet, "limit": 1, "sortBy": "TIMESTAMP", "sortDirection": "ASC"}) or []
        return float(a[0]["timestamp"]) if a else None

    def history(self, token: str, start: float, end: float) -> list[tuple[float, float]]:
        data = self._get(f"{wr.CLOB}/prices-history", {"market": token, "startTs": int(start),
                                                        "endTs": int(end), "fidelity": 5}) or {}
        return sorted((float(h["t"]), float(h["p"])) for h in data.get("history") or [])

    # -------------------------------------------------------------- run
    def candidates(self, markets: list[dict]) -> list[dict]:
        out = []
        for i, m in enumerate(markets):
            trades = self._cached(f"t_{m['cid']}.json", lambda m=m: self.big_buys(m["cid"]))
            seen = set()
            for t in sorted(trades, key=lambda x: x["timestamp"]):
                p, ts = float(t["price"]), float(t["timestamp"])
                idx = int(t.get("outcomeIndex") or 0)
                if p > MAX_PRICE or p * float(t["size"]) < MIN_CASH or idx > 1:
                    continue
                if m["end_ts"] and ts >= m["end_ts"]:
                    continue
                key = (t["proxyWallet"], idx)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"cid": m["cid"], "wallet": t["proxyWallet"], "ts": ts, "price": p, "idx": idx,
                            "cash": p * float(t["size"]), "won": m["payouts"][idx], "category": m["category"]})
            if i % 100 == 0:
                log.info("markets: %d of %d, %d candidate trades", i, len(markets), len(out))
        return out

    def run(self, days: int = 120, control_sample: int = 1500) -> dict:
        markets = self._cached(f"markets_{days}d_{int(self.clock() // 86400)}.json", lambda: self.markets(days))
        by_cid = {m["cid"]: m for m in markets}
        cands = self.candidates(markets)
        firsts = self._cached("first_seen.json", lambda: {})
        for i, w in enumerate(sorted({c["wallet"] for c in cands})):
            if w not in firsts:
                try:
                    firsts[w] = self.first_seen(w)
                except Exception as exc:  # noqa: BLE001
                    log.debug("first_seen %s: %s", w[:10], exc)
                    firsts[w] = None
            if i % 500 == 0:
                log.info("wallet ages: %d", i)
                with open(os.path.join(self.cache, "first_seen.json"), "w") as fh:
                    json.dump(firsts, fh)
        with open(os.path.join(self.cache, "first_seen.json"), "w") as fh:
            json.dump(firsts, fh)
        for c in cands:
            f = firsts.get(c["wallet"])
            c["fresh"] = f is not None and c["ts"] - f <= FRESH_DAYS * 86400
        signal = [c for c in cands if c["fresh"]]
        control = [c for c in cands if not c["fresh"]]
        # copy prices: every signal trade, a deterministic sample of the control
        control_s = sorted(control, key=lambda c: hashlib.sha1(f"{c['wallet']}{c['cid']}".encode()).hexdigest())
        control_s = control_s[:control_sample]
        for c in signal + control_s:
            m = by_cid[c["cid"]]
            tok = m["tokens"][c["idx"]] if c["idx"] < len(m["tokens"]) else None
            if not tok:
                continue
            try:
                hist = self.history(tok, c["ts"] - 60, c["ts"] + max(DELAYS) + 3600)
            except Exception as exc:  # noqa: BLE001
                log.debug("history %s: %s", tok[:10], exc)
                continue
            for d in DELAYS:
                px = next((p for ts, p in hist if ts >= c["ts"] + d), None)
                if px is None or not (0.0 < px < 0.99):
                    continue
                entry = px + HALF_SPREAD
                c[f"copy_{d}"] = c["won"] - entry - m["fee_rate"] * entry * (1 - entry)
        return {"markets": len(markets), "candidates": len(cands), "signal": signal, "control": control,
                "control_sampled": control_s}


def _ms(rows: list[dict], key: str) -> dict:
    vals = [(r[key], r["cid"]) for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0}
    m = statistics.mean(v for v, _ in vals)
    by: dict[str, float] = defaultdict(float)
    for v, g in vals:
        by[g] += v - m
    se = math.sqrt(sum(x * x for x in by.values())) / len(vals) if len(vals) > 1 else None
    return {"n": len(vals), "markets": len(by), "mean": m, "se": se, "t": (m / se) if se else None}


def summarize(res: dict) -> dict:
    out = {"markets": res["markets"], "candidates": res["candidates"]}
    for name, rows in (("signal", res["signal"]), ("control", res["control"]),
                       ("control_sampled", res["control_sampled"])):
        for r in rows:
            r["surplus"] = r["won"] - r["price"]
        out[name] = {"surplus": _ms(rows, "surplus"),
                     **{f"copy_{d}": _ms(rows, f"copy_{d}") for d in DELAYS}}
    return out


def format_summary(s: dict) -> str:
    def f(d):
        if not d.get("n"):
            return "n 0"
        se = f" ± {d['se'] * 100:.1f}" if d.get("se") else ""
        t = f" (t {d['t']:+.1f})" if d.get("t") else ""
        return f"{d['mean'] * 100:+.1f}{se}{t} n {d['n']}/{d.get('markets')} mkts"

    lines = [f"{s['markets']} resolved non-sports non-crypto markets, {s['candidates']} candidate long-shot buys"]
    for name in ("signal", "control", "control_sampled"):
        b = s[name]
        lines.append(f"{name:16s} surplus {f(b['surplus']):34s} copy +10m {f(b['copy_600']):34s} "
                     f"copy +1h {f(b['copy_3600'])}")
    return "\n".join(lines)
