"""docs/PINNACLE_TENNIS_PREREG_2026-10-08.md
Usage: python research/pinnacle_clv/run.py ATP.xlsx WTA.xlsx <tape_dir> [<tape_dir> ...]
Tape dirs hold <ticker>.json trade tapes ([ts, yes_price, count, taker_side])."""
import json, math, os, sys, time, unicodedata, urllib.request, datetime as dt
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from sportsbot.backtest.markout import Fill, prematch_cut
from sportsbot.core.odds import remove_vig_shin

API = "https://api.elections.kalshi.com/trade-api/v2"
SPLIT = dt.date(2026, 9, 5)
fee = lambda p: 0.07 * p * (1 - p)


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return "".join(c if c.isalpha() or c == " " else " " for c in s).split()


def key_td(name):            # "Sinner J." / "Auger-Aliassime F." -> ("sinner", "j")
    parts = norm(name)
    return (" ".join(parts[:-1]) if len(parts) > 1 else parts[0], parts[-1][:1]) if parts else None


def key_full(name):          # "Jannik Sinner" -> ("sinner", "j")
    parts = norm(name)
    return (parts[-1], parts[0][:1]) if len(parts) > 1 else None


def load_td(paths):
    import openpyxl
    rows = []
    for p in paths:
        ws = openpyxl.load_workbook(p, read_only=True).active
        it = ws.iter_rows(values_only=True); hdr = [str(h) for h in next(it)]
        ix = {h: i for i, h in enumerate(hdr)}
        for r in it:
            try:
                d = r[ix["Date"]]; d = d.date() if hasattr(d, "date") else dt.date.fromisoformat(str(d)[:10])
                psw, psl = float(r[ix["PSW"]]), float(r[ix["PSL"]])
            except (TypeError, ValueError, KeyError):
                continue
            w, l = key_td(r[ix["Winner"]]), key_td(r[ix["Loser"]])
            if w and l and psw > 1 and psl > 1:
                pw, pl = remove_vig_shin([1 / psw, 1 / psl])
                rows.append((d, w, l, pw))
    return rows


def market_meta(ticker, cache):
    if ticker in cache: return cache[ticker]
    for a in range(5):
        try:
            m = json.load(urllib.request.urlopen(f"{API}/markets/{ticker}", timeout=30))["market"]
            cache[ticker] = {"yes": m.get("yes_sub_title"), "title": m.get("title"),
                             "close": m.get("close_time"), "sv": m.get("settlement_value_dollars")}
            return cache[ticker]
        except Exception:
            time.sleep(2 ** a)
    cache[ticker] = None; return None


def main(atp, wta, tape_dirs):
    td = load_td([atp, wta]); print("tennis-data rows with Pinnacle odds:", len(td))
    by_player = defaultdict(list)
    for d, w, l, pw in td:
        by_player[w].append((d, l, pw, True)); by_player[l].append((d, w, pw, False))
    cache_f = "kalshi_meta_cache.json"; cache = json.load(open(cache_f)) if os.path.exists(cache_f) else {}
    rows = []; seen_events = set(); stats = defaultdict(int)
    for tdir in tape_dirs:
        for fn in sorted(os.listdir(tdir)):
            ticker = fn[:-5]; ev = ticker.rsplit("-", 1)[0]
            if ev in seen_events: continue                  # one market per match
            tr = json.load(open(os.path.join(tdir, fn)))
            if len(tr) < 20: continue
            cut = prematch_cut([Fill(ts=t, price=p, taker_book_side=s) for t, p, n, s in tr])
            if cut is None or cut < 20: stats["no_cut"] += 1; continue
            m = market_meta(ticker, cache)
            if not m or m["sv"] is None: stats["no_meta"] += 1; continue
            k = key_full(m["yes"]); close = dt.datetime.fromisoformat(m["close"].replace("Z", "+00:00")).date()
            cands = [c for c in by_player.get(k, []) if abs((c[0] - close).days) <= 1]
            if len(cands) != 1: stats["no_unique_match"] += 1; continue
            d, opp, pw, yes_is_winner = cands[0]
            pin_yes = pw if yes_is_winner else 1 - pw
            sv = float(m["sv"])
            if (sv == 1.0) != yes_is_winner: stats["result_mismatch"] += 1; continue
            pre = tr[:cut]
            last = pre[-1][1]
            ask = next((p for t, p, n, s in reversed(pre) if s == "yes"), None)
            bid = next((p for t, p, n, s in reversed(pre) if s == "no"), None)
            seen_events.add(ev)
            rows.append((d, pin_yes, last, ask, bid, sv))
    json.dump(cache, open(cache_f, "w"))
    print("matched matches:", len(rows), dict(stats))
    if len(rows) < 20: return
    bk = [(r[2] - r[5]) ** 2 for r in rows]; bp = [(r[1] - r[5]) ** 2 for r in rows]
    bb = [((r[1] + r[2]) / 2 - r[5]) ** 2 for r in rows]
    diff = [a - b for a, b in zip(bk, bp)]; n = len(diff); mu = sum(diff) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in diff) / (n - 1))
    print(f"H1 Brier: Kalshi {sum(bk)/n:.4f}  Pinnacle-Shin {sum(bp)/n:.4f}  blend {sum(bb)/n:.4f}  "
          f"(Kalshi minus Pinnacle {mu:+.4f}, t {mu / (sd / math.sqrt(n)):+.2f}, n {n})")
    halves = defaultdict(list)
    for d, pin, last, ask, bid, sv in rows:
        trade = None
        if ask is not None and pin - ask - fee(ask) >= 0.02: trade = (sv - ask - fee(ask), ask + fee(ask))
        elif bid is not None and (1 - pin) - (1 - bid) - fee(1 - bid) >= 0.02: trade = ((1 - sv) - (1 - bid) - fee(1 - bid), 1 - bid + fee(1 - bid))
        if trade: halves["H1" if d < SPLIT else "H2"].append(trade)
    res = {}
    for h in ("H1", "H2"):
        v = halves[h]
        if len(v) < 2: print(h, "trades", len(v)); res[h] = (len(v), 0, 0, 0); continue
        x = [a for a, _ in v]; m_ = sum(x) / len(x); s_ = math.sqrt(sum((y - m_) ** 2 for y in x) / (len(x) - 1))
        res[h] = (len(x), m_, m_ / (s_ / math.sqrt(len(x))), sum(x) / sum(c for _, c in v))
        print(f"H2 {h}: trades {res[h][0]} mean {m_ * 100:+.2f}c t {res[h][2]:+.2f} ROI {res[h][3]:+.3f}")
    a, b = res.get("H1", (0, 0, 0, 0)), res.get("H2", (0, 0, 0, 0))
    print("VERDICT:", "PASS (upper bound; needs forward test)" if a[0] >= 100 and b[0] >= 100 and a[1] > 0 and b[1] > 0 and b[2] >= 2 and b[3] >= 0.01 else "FAIL")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
