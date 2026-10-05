import json, time, urllib.request, collections, datetime as dt
H = {"User-Agent": "Mozilla/5.0"}
def g(u):
    for a in range(5):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60))
        except Exception as e: err = e; time.sleep(2 ** a)
    print("fail", u, err); return None
W = {"dan1ro0_wallet": "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82", "mo-money": "0x32ed2e546b187ca15e2841edc82b22c713cf8ec3"}
for name, a in W.items():
    rows, end = [], None
    for _ in range(60):   # page backwards in time by end timestamp
        u = f"https://data-api.polymarket.com/activity?user={a}&limit=500" + (f"&end={end}" if end else "")
        d = g(u)
        if not d: break
        rows += d
        if len(d) < 500: break
        end = d[-1]["timestamp"] - 1
    json.dump(rows, open(f"act_{name}.json", "w"))
    ts = [r["timestamp"] for r in rows]
    print(f"\n== {name} {a}: {len(rows)} activities {dt.datetime.utcfromtimestamp(min(ts)):%Y-%m-%d} .. {dt.datetime.utcfromtimestamp(max(ts)):%Y-%m-%d}")
    c = collections.Counter(r["type"] for r in rows); usd = collections.Counter()
    for r in rows: usd[r["type"]] += float(r.get("usdcSize") or 0)
    print(" by type:", {k: (c[k], round(usd[k])) for k in c})
    tr = [r for r in rows if r["type"] == "TRADE"]
    sides = collections.Counter(r["side"] for r in tr)
    titles = collections.Counter((r.get("title") or "")[:40] for r in tr)
    print(" trade sides:", dict(sides), "| median trade $", sorted(float(r["usdcSize"]) for r in tr)[len(tr)//2] if tr else None)
    print(" top markets:", titles.most_common(6))
    pr = [float(r["price"]) for r in tr]
    print(" price deciles:", [round(sorted(pr)[int(i*len(pr)/10)],2) for i in range(10)] if pr else None)
    for path in (f"https://data-api.polymarket.com/value?user={a}", f"https://lb-api.polymarket.com/profit?window=all&address={a}", f"https://data-api.polymarket.com/v1/leaderboard?user={a}&timePeriod=all"):
        print(" ", path.split('/')[-1][:40], str(g(path))[:200])
