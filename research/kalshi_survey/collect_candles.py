"""Phase C: per series, up to 40 events spread over time; per event up to 25 markets
chosen at RANDOM (seeded; not by volume, which would leak the outcome path).
Batch candlesticks; period by lifetime (<=2h:1m, <=7d:60m, else 1d).
Resumable: events already in candles.jsonl are skipped."""
import json, time, os, glob, random, urllib.request, urllib.error, datetime as dt, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
B = "https://api.elections.kalshi.com/trade-api/v2"
P = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
EV_PER_SERIES, MK_PER_EVENT, MAX_CANDLES = 120, 25, 4000

def get(path):
    for a in range(7):
        try:
            with urllib.request.urlopen(B + path, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(30, 2 ** a)); continue
            return {"error": e.code}
        except Exception:
            time.sleep(min(30, 2 ** a))
    return {"error": "net"}

events = defaultdict(list)
for fn in glob.glob("day_*.jsonl"):
    for line in open(fn):
        m = json.loads(line)
        events[m["event_ticker"]].append(m)
by_series = defaultdict(list)
for ev, ms in events.items():
    by_series[ev.split("-")[0]].append((max(P(m["close_time"]) for m in ms), ev))
rng = random.Random(7)
todo = []
for s, evs in sorted(by_series.items()):
    evs.sort()
    k = len(evs)
    pick = evs if k <= EV_PER_SERIES else [evs[int(i * k / EV_PER_SERIES)] for i in range(EV_PER_SERIES)]
    for _, ev in pick:
        ms = events[ev][:]
        rng.shuffle(ms)
        todo.append((s, ev, ms[:MK_PER_EVENT]))
json.dump(sorted(t[1] for t in todo), open("selected.json", "w"))
done = set()
if os.path.exists("candles.jsonl"):
    for line in open("candles.jsonl"):
        try: done.add(json.loads(line)["event"])
        except Exception: pass
todo = [t for t in todo if t[1] not in done]
print("series", len(by_series), "events todo", len(todo), "done", len(done), flush=True)
POINTS = (0.25, 0.50, 0.65, 0.73, 0.75, 0.90, 0.98)

def compact(m, cs):
    """Keep, for each pre-registered time point, the last candle at or before it with
    both bid and ask present (identical to forward-fill at analysis time)."""
    te = m.get("expected_expiration_time")
    if te: T = P(te)
    elif not m.get("can_close_early"): T = P(m["close_time"])
    else: return []
    o = P(m["open_time"])
    keep = {}
    for f in POINTS:
        t = o + f * (T - o)
        best = None
        for c in cs:
            if c[0] <= t and c[1] is not None and c[2] is not None:
                best = c
            elif c[0] > t:
                break
        if best: keep[best[0]] = best
    return [keep[k] for k in sorted(keep)]

lock = threading.Lock()
out = open("candles.jsonl", "a")

def fetch(item):
    s, ev, ms = item
    o = min(P(m["open_time"]) for m in ms); c = max(P(m["close_time"]) for m in ms)
    life = c - o
    per = 1 if life <= 7200 else 60 if life <= 7 * 86400 else 1440
    o = max(o, c - 4900 * per * 60)  # API caps candles per market
    nper = max(1, (c - o) // (per * 60) + 1)
    chunk = max(1, min(100, MAX_CANDLES // nper))
    res = {}
    for i in range(0, len(ms), chunk):
        part = ms[i:i + chunk]
        d = get(f"/markets/candlesticks?market_tickers={','.join(m['ticker'] for m in part)}&start_ts={o}&end_ts={c}&period_interval={per}")
        for x in d.get("markets", []):
            res[x["market_ticker"]] = [[k["end_period_ts"],
                                        (k.get("yes_bid") or {}).get("close_dollars"),
                                        (k.get("yes_ask") or {}).get("close_dollars"),
                                        float(k.get("volume_fp") or 0)] for k in x["candlesticks"]]
    rec = {"series": s, "event": ev, "period": per,
           "markets": [dict(m, candles=compact(m, res.get(m["ticker"], []))) for m in ms]}
    with lock:
        out.write(json.dumps(rec) + "\n"); out.flush()

n = 0
with ThreadPoolExecutor(8) as ex:
    for _ in ex.map(fetch, todo):
        n += 1
        if n % 500 == 0: print("events", n, flush=True)
print("CANDLES DONE", flush=True)
