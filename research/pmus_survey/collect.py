"""docs/PMUS_SURVEY_PREREG_2026-10-09.md — collector.
Stage 1 lists every resolved match-winner market Nov 2025 - Oct 2026 (metadata);
stage 2 pulls the pre-match price-history window per market. Run in a scratch dir:
writes markets.json, split.json (median startDate, computed BEFORE stage 2) and ph/<slug>.json."""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

G = "https://gateway.polymarket.us"
H = {"User-Agent": "Mozilla/5.0"}
LO, HI = "2025-11-01", "2026-10-01"
WINNER = ("_match_winner", "_full_game_winner")


def g(u):
    for a in range(8):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + a)
                continue
            if e.code == 404:
                return None
            time.sleep(min(20, 1.5 ** a))
        except Exception:
            time.sleep(min(20, 1.5 ** a))
    return None


def ts(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


# Sport tag ids from /v2/sports (2026-10-09). The typed MONEYLINE filter misses
# the newer per-sport winner types, and the unfiltered list is ~95% props, so
# the listing is done per sport tag and filtered to the `aec-` match-winner
# slugs client-side. Soccer is excluded (three-way markets).
SPORT_TAGS = {"baseball": 15, "tennis": 22, "table_tennis": 655, "football": 13,
              "basketball": 11, "hockey": 14, "mma": 16, "esports": 39}

if not os.path.exists("markets.json"):
    out = {}
    for grp, tag in SPORT_TAGS.items():
        day = dt.date.fromisoformat(LO)
        end = dt.date.fromisoformat(HI)
        n0 = len(out)
        while day < end:
            nxt = min(day + dt.timedelta(days=7), end)
            off = 0
            while True:
                q = urllib.parse.urlencode({"limit": 500, "offset": off, "closed": "true", "tagIds": tag,
                                            "startDateMin": f"{day}T00:00:00Z", "startDateMax": f"{nxt}T00:00:00Z"})
                d = g(f"{G}/v1/markets?{q}") or []
                ms = d if isinstance(d, list) else d.get("markets", [])
                for m in ms:
                    t = str(m.get("sportsMarketType") or "")
                    if not m["slug"].startswith("aec-") or m.get("status") != "MARKET_STATUS_RESOLVED":
                        continue
                    if t and not (t == "moneyline" or t.endswith(WINNER)):
                        continue
                    sides = m.get("marketSides") or []
                    if len(sides) != 2 or not all(s.get("teamId") for s in sides):
                        continue
                    long_s = next((s for s in sides if s.get("long")), None)
                    if not long_s or long_s.get("price") not in ("1", "0", "1.0", "0.0"):
                        continue
                    if not m.get("gameStartTime"):
                        continue
                    parts = m["slug"].split("-")
                    out[m["slug"]] = {"league": parts[1] if len(parts) > 2 else "", "group": grp,
                                      "start": ts(m["gameStartTime"]), "startDate": m.get("startDate"),
                                      "settle": float(long_s["price"]), "home": long_s.get("description"),
                                      "away": next(s.get("description") for s in sides if not s.get("long"))}
                if len(ms) < 500:
                    break
                off += 500
            day = nxt
        print(grp, "markets", len(out) - n0, flush=True)
        json.dump(out, open("markets.partial.json", "w"))
    json.dump(out, open("markets.json", "w"))
    starts = sorted(m["startDate"] for m in out.values())
    json.dump({"median_startDate": starts[len(starts) // 2], "n": len(starts)}, open("split.json", "w"))
    print("universe", len(out), "median", starts[len(starts) // 2], flush=True)

mk = json.load(open("markets.json"))
os.makedirs("ph", exist_ok=True)


def pull(item):
    slug, m = item
    fn = f"ph/{slug}.json"
    if os.path.exists(fn):
        return
    q = urllib.parse.urlencode({"symbol": slug, "fidelity": 1,
                                "timestamp.startTimestamp": m["start"] - 3600,
                                "timestamp.endTimestamp": m["start"]})
    d = g(f"{G}/v1/price-history?{q}")
    if d is None:
        return
    json.dump(d.get("history", []), open(fn, "w"))


if len(sys.argv) > 1 and sys.argv[1] == "prices":
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(pull, sorted(mk.items())))
    print("PRICES DONE", len(os.listdir("ph")), "of", len(mk), flush=True)
