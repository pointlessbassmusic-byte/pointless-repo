"""Period-B copy test (docs/SHARPS_PREREG_2026-10-05.md)."""
import json, os, math, time, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
H = {"User-Agent": "Mozilla/5.0"}
def g(u):
    for a in range(6):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60))
        except Exception: time.sleep(min(20, 2 ** a))
    return None
mk = {c: m for c, m in json.load(open("markets.json")).items() if m["vol"] >= 1e4}; sets = json.load(open("sets.json"))
import datetime as dt
B0 = int(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp()); B1 = int(dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp())
def endts(m): return int(dt.datetime.fromisoformat(m["end"].replace("Z", "+00:00")).timestamp())
def signals(wallets):
    first = {}
    for w in wallets:
        fn = f"actB/{w}.json"
        if not os.path.exists(fn): continue
        for r in json.load(open(fn))["rows"]:
            c = r.get("conditionId")
            if r["type"] != "TRADE" or r["side"] != "BUY" or c not in mk or not (B0 <= endts(mk[c]) < B1): continue
            if c not in first or r["timestamp"] < first[c]["timestamp"]: first[c] = dict(r, wallet=w)
    return first
def price_after(asset, t):
    d = g(f"https://clob.polymarket.com/prices-history?market={asset}&startTs={t}&endTs={t + 900}&fidelity=1") or {}
    h = sorted(d.get("history", []), key=lambda x: x["t"])
    after = [x["p"] for x in h if x["t"] >= t + 60]
    return after[0] if after else (h[-1]["p"] if h else None)
fee = lambda p: 0.05 * p * (1 - p)
def evaluate(name, sig):
    items = list(sig.items())
    with ThreadPoolExecutor(8) as ex: later = list(ex.map(lambda kv: price_after(kv[1]["asset"], kv[1]["timestamp"]), items))
    rows, upper = [], []
    for (c, r), p60 in zip(items, later):
        pay = mk[c]["payout"][int(r["outcomeIndex"])]; theirs = float(r["price"])
        if not 0 < theirs < 1: continue
        upper.append((r["timestamp"] // 86400, pay - theirs - fee(theirs), theirs + fee(theirs)))
        if p60 is None: continue
        e = min(0.99, max(theirs, float(p60)) + 0.005)
        rows.append((r["timestamp"] // 86400, pay - e - fee(e), e + fee(e)))
    for label, rs in (("copy +60s (PRIMARY)" if name == "sharps" else "copy +60s", rows), ("their exact price (upper bound)", upper)):
        n = len(rs)
        if n < 2: print(f"{name} {label}: n={n}"); continue
        xs = [r[1] for r in rs]; mu = sum(xs) / n
        byd = defaultdict(list)
        for r in rs: byd[r[0]].append(r[1])
        dm = [sum(v) / len(v) for v in byd.values()]; dmu = sum(dm) / len(dm)
        dsd = math.sqrt(sum((x - dmu) ** 2 for x in dm) / (len(dm) - 1)) if len(dm) > 1 else 0
        roi = sum(xs) / sum(r[2] for r in rs)
        ok = n >= 200 and mu > 0 and dsd and dmu / (dsd / math.sqrt(len(dm))) >= 2 and roi >= 0.01
        print(f"{name:8s} {label:32s} n={n:4d} days={len(dm)} mean={mu:+.4f}/share day_t={dmu / (dsd / math.sqrt(len(dm))) if dsd else 0:+.2f} roi={roi:+.3f}" + (("  -> PASS" if ok else "  -> FAIL") if label.endswith("(PRIMARY)") else ""))
for name in ("sharps", "placebo"):
    evaluate(name, signals(sets[name]))
