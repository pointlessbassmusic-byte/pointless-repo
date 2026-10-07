"""Phase A: enumerate settled Kalshi markets on 20 sampled days over the last 120 days.
Resumable: one jsonl per day; a day is done when its .done marker exists."""
import json, time, os, sys, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
B = "https://api.elections.kalshi.com/trade-api/v2"
NOW = 1790812800  # fixed anchor 2026-10-01T00:00Z so reruns are identical
DAYS = list(range(2, 122, 6))  # 20 days
KEEP = ("ticker", "event_ticker", "open_time", "close_time", "result", "settlement_value_dollars",
        "volume_fp", "last_price_dollars", "market_type", "can_close_early", "expected_expiration_time", "latest_expiration_time")

def get(path):
    for a in range(6):
        try:
            with urllib.request.urlopen(B + path, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** a); continue
            raise
        except Exception:
            time.sleep(2 ** a)
    raise RuntimeError(path)

def day(off):
    out = f"day_{off}.jsonl"
    if os.path.exists(out + ".done"):
        return off, -1
    lo, hi = NOW - off * 86400, NOW - (off - 1) * 86400
    n, seen = 0, set()
    with open(out, "w") as f:
        # live listing only serves recent settlements; older ones live under /historical
        for base in ("/markets?status=settled&", "/historical/markets?"):
            cur = ""
            while True:
                d = get(f"{base}limit=1000&mve_filter=exclude&min_close_ts={lo}&max_close_ts={hi}" + (f"&cursor={cur}" if cur else ""))
                for m in d["markets"]:
                    if m["ticker"] in seen or m["ticker"].startswith("KXMVE") or float(m.get("volume_fp") or 0) <= 0:
                        continue
                    seen.add(m["ticker"])
                    f.write(json.dumps({k: m.get(k) for k in KEEP}) + "\n"); n += 1
                cur = d.get("cursor")
                if not cur or not d["markets"]:
                    break
    open(out + ".done", "w").close()
    print("day", off, n, flush=True)
    return off, n

with ThreadPoolExecutor(4) as ex:
    for r in ex.map(day, DAYS):
        pass
print("ENUM DONE", flush=True)
