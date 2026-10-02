"""Net value of a hypothetical resting quote under Kalshi LIP, from lip.sqlite.

For each snapshot of a rewarded market, and each side (YES bids, NO bids):
  quote variants: 'join' = best bid; 'behind1' = best bid - 1 tick
  size Q contracts (default 100)
  reward  = period_reward * unit * (recorded fraction of the period) * (share of
            combined score, averaged over snapshots; random times -> unbiased)
  fills   = trades between this snapshot and the next breach our price
            (queue-aware, sportsbot.signals.lip.simulate_fill); each fill is held
            to settlement, then we re-quote at the next snapshot
  loss    = Q * (payout - price) over fills (payout from results; unsettled
            markets are marked to the last snapshot mid and reported separately)
Net = reward - loss. Mean per market-day, clustered by calendar day.
Usage: python research/lip/analyze.py lip.sqlite [Q]
"""
import json
import math
import sqlite3
import sys
from collections import defaultdict

from sportsbot.signals.lip import REWARD_UNIT_USD, TICK, order_score, reference_price, simulate_fill

db = sqlite3.connect(sys.argv[1])
Q = float(sys.argv[2]) if len(sys.argv) > 2 else 100.0

progs = defaultdict(list)
for pid, t, s, e, rew, dbps, tgt in db.execute(
        "SELECT id, market_ticker, start_ts, end_ts, period_reward, discount_bps, target_size FROM programs"):
    progs[t].append((s, e, rew, dbps / 1e4, tgt, pid))
results = {t: v for t, v in db.execute("SELECT ticker, settlement_value FROM results")}
cats = {t: c for t, c in db.execute("SELECT ticker, category FROM panel")}


def program_at(t, ts):
    for p in progs.get(t, []):
        if p[0] <= ts < p[1]:
            return p
    return None


def combined_share(yes, no, quotes, tgt, disc):
    """quotes: [(side, price)] for our orders (size Q each)."""
    tot = ours = 0.0
    for side, book in (("yes", yes), ("no", no)):
        mine = [(p, Q) for s, p in quotes if s == side]
        full = book + mine
        ref = reference_price(full, tgt)
        tot += sum(order_score(p, sz, ref, disc) for p, sz in full)
        ours += sum(order_score(p, sz, ref, disc) for p, sz in mine)
    return ours / tot if tot > 0 else 0.0


acc = defaultdict(lambda: defaultdict(lambda: {"share": [], "loss": 0.0, "fills": 0, "settled": True, "t0": None, "t1": None}))
tickers = [r[0] for r in db.execute("SELECT DISTINCT ticker FROM snapshots")]
for tk in tickers:
    snaps = db.execute("SELECT ts, yes_bids, no_bids FROM snapshots WHERE ticker=? ORDER BY ts", (tk,)).fetchall()
    trades = [dict(zip(("ts", "yes_price", "count", "taker_side"), r)) for r in db.execute(
        "SELECT ts, yes_price, count, taker_side FROM trades WHERE ticker=? ORDER BY ts", (tk,))]
    last_mid = None
    for i, (ts, yj, nj) in enumerate(snaps):
        prog = program_at(tk, ts)
        if not prog:
            continue
        yes, no = [tuple(x) for x in json.loads(yj)], [tuple(x) for x in json.loads(nj)]
        if not yes or not no:
            continue
        by, bn = max(p for p, _ in yes), max(p for p, _ in no)
        last_mid = (by + (1 - bn)) / 2
        nxt = snaps[i + 1][0] if i + 1 < len(snaps) else ts + 600
        window = [t for t in trades if ts < t["ts"] <= nxt]
        for variant, off in (("join", 0.0), ("behind1", TICK)):
            quotes = [("yes", round(by - off, 2)), ("no", round(bn - off, 2))]
            rec = acc[variant][(tk, prog[5])]
            rec["share"].append(combined_share(yes, no, quotes, prog[4], prog[3]))
            rec["t0"] = ts if rec["t0"] is None else rec["t0"]
            rec["t1"] = nxt
            for side, px in quotes:
                ahead = sum(s for p, s in (yes if side == "yes" else no) if p >= px - 1e-9)
                if px <= 0 or not simulate_fill(px, ahead, window, side):
                    continue
                v = results.get(tk)
                if v is None:
                    v, rec["settled"] = last_mid, False
                payout = v if side == "yes" else 1 - v
                rec["loss"] -= Q * (payout - px)        # negative loss = fill made money
                rec["fills"] += 1

for variant, recs in acc.items():
    rows = []
    for (tk, pid), r in recs.items():
        s, e, rew, disc, tgt, _ = next(p for p in progs[tk] if p[5] == pid)
        # prorate: only the recorded window counts, matching the fill window
        covered = max(0, min(e, r["t1"]) - max(s, r["t0"])) / max(1, e - s)
        reward = rew * REWARD_UNIT_USD * covered * (sum(r["share"]) / len(r["share"]))
        rows.append((tk, cats.get(tk, "?"), reward, r["loss"], r["fills"], r["settled"], len(r["share"])))
    print(f"\n== {variant}, Q={Q:.0f} per side; {len(rows)} market-programs ==")
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r[1]].append(r)
    for c, rs in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        net = [r[2] - r[3] for r in rs]
        n = len(net)
        mu = sum(net) / n
        sd = math.sqrt(sum((x - mu) ** 2 for x in net) / (n - 1)) if n > 1 else 0
        print(f"{c:24s} n={n:4d} reward ${sum(r[2] for r in rs):9.2f} fill P&L ${-sum(r[3] for r in rs):9.2f} "
              f"fills {sum(r[4] for r in rs):5d} net/market ${mu:+7.2f} t={mu / (sd / math.sqrt(n)) if sd else 0:5.2f} "
              f"unsettled {sum(1 for r in rs if not r[5])} snaps/market {sum(r[6] for r in rs) / n:.1f}")
