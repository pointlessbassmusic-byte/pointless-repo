import json, os, glob, time, urllib.request, urllib.error, datetime as dt
from concurrent.futures import ThreadPoolExecutor
exec(open("collect.py").read().split("series = [")[0])  # get(), P, K
os.makedirs("cd", exist_ok=True); os.makedirs("obs", exist_ok=True); os.makedirs("cli", exist_ok=True)
CUTOFF = P("2026-08-01T00:00:00Z")
def candles(m):
    o, c = P(m["open_time"]), P(m["close_time"])
    s = m["ticker"].split("-")[0]
    if c < CUTOFF:
        d = get(f"{K}/historical/markets/{m['ticker']}/candlesticks?start_ts={o}&end_ts={c}&period_interval=60") or {}
        return [[k["end_period_ts"], (k.get("yes_bid") or {}).get("close"), (k.get("yes_ask") or {}).get("close")] for k in d.get("candlesticks", [])]
    d = get(f"{K}/series/{s}/markets/{m['ticker']}/candlesticks?start_ts={o}&end_ts={c}&period_interval=60") or {}
    return [[k["end_period_ts"], (k.get("yes_bid") or {}).get("close_dollars"), (k.get("yes_ask") or {}).get("close_dollars")] for k in d.get("candlesticks", [])]
def do_series(fn):
    S = json.load(open(fn)); out = f"cd/{S['series']}.json"
    if not S["markets"] or os.path.exists(out): return
    with ThreadPoolExecutor(6) as ex: cs = list(ex.map(candles, S["markets"]))
    json.dump({m["ticker"]: c for m, c in zip(S["markets"], cs)}, open(out, "w"))
    print("candles", S["series"], sum(1 for c in cs if c), "/", len(cs), flush=True)
def do_station(code):
    if not os.path.exists(f"obs/{code}.csv"):
        t = get(f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station={code}&data=tmpf&year1=2026&month1=5&day1=31&year2=2026&month2=10&day2=2&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&trace=T&report_type=3&report_type=4", js=False)
        if t: open(f"obs/{code}.csv", "w").write(t)
    if not os.path.exists(f"cli/{code}.json"):
        d = get(f"https://mesonet.agron.iastate.edu/json/cli.py?station=K{code}&year=2026")
        if d: json.dump(d, open(f"cli/{code}.json", "w"))
    print("station", code, flush=True)
files = sorted(glob.glob("mk/*.json"))
codes = sorted({json.load(open(f))["code"] for f in files} - {None})
with ThreadPoolExecutor(3) as ex: list(ex.map(do_station, codes))
with ThreadPoolExecutor(3) as ex: list(ex.map(do_series, files))
print("COLLECT2 DONE", flush=True)
