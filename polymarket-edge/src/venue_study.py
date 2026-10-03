"""Does Polymarket's price lead Kalshi's on the same soccer matches?

    python -m src.venue_study            # collect + analyse
    python -m src.venue_study --analyse  # re-run on the cached CSV

The one edge class with evidence behind it is a reference that LEADS the
market. Kalshi is the US-legal venue; Polymarket is the deeper one. If
Kalshi's price converges toward Polymarket's rather than the other way round,
Polymarket is a leading reference for Kalshi trades, and that is measurable
on settled matches with no model at all.

Data: Kalshi's settled KXLALIGAGAME / KXBUNDESLIGAGAME / KXLIGUE1GAME markets
(three per match: home, tie, away) with hourly candlesticks, paired with the
Polymarket rows already collected by sportsbook_study (price 1h/6h/24h before
kickoff, outcome). Matching: Kalshi's team names mapped to football-data
names by an explicit table, joined to the Polymarket match by league, teams
and date. Unmatched is skipped.

Three tests per horizon on the same rows:
1. Brier of each venue's price against the outcome (paired bootstrap).
2. Lead-lag: regress Kalshi's move from horizon h to 1h on the gap
   (Polymarket - Kalshi) at h, and the reverse. The venue whose gap predicts
   the other's move is the one that leads.
3. Buy YES on Kalshi at the ask when Polymarket is `threshold` higher, taker
   fee 0.07*p*(1-p), either held to settlement or marked at Kalshi's 1h mid.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .http_util import retrying_session
from .sportsbook_study import (
    HORIZONS_H, OUTCOMES, PolyMatch, brier, fetch_poly_matches, load_rows, normalize,
    ols, paired_brier_diff, team,
)

log = logging.getLogger(__name__)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = {"KXLALIGAGAME": "SP1", "KXBUNDESLIGAGAME": "D1", "KXLIGUE1GAME": "F1"}
KALSHI_FEE_RATE = 0.07      # quadratic_with_maker_fees, multiplier 1 on these series

# Kalshi yes_sub_title -> football-data name
KALSHI_ALIASES = {
    # La Liga
    "alaves": "Alaves", "atletico": "Ath Madrid", "barcelona": "Barcelona",
    "bilbao": "Ath Bilbao", "celta vigo": "Celta", "deportivo de la coruna": "La Coruna",
    "elche": "Elche", "espanyol": "Espanol", "getafe": "Getafe", "levante": "Levante",
    "malaga": "Malaga", "osasuna": "Osasuna", "real betis": "Betis",
    "real madrid": "Real Madrid", "real sociedad": "Sociedad", "santander": "Santander",
    "sevilla": "Sevilla", "valencia": "Valencia", "vallecano": "Vallecano",
    "villarreal": "Villarreal",
    # Bundesliga
    "augsburg": "Augsburg", "bayern munich": "Bayern Munich", "bremen": "Werder Bremen",
    "dortmund": "Dortmund", "elversberg": "Elversberg", "fc koln": "FC Koln",
    "frankfurt": "Ein Frankfurt", "freiburg": "Freiburg", "hamburg": "Hamburg",
    "hoffenheim": "Hoffenheim", "leipzig": "RB Leipzig", "leverkusen": "Leverkusen",
    "mainz": "Mainz", "m'gladbach": "M'gladbach", "paderborn": "Paderborn",
    "schalke": "Schalke 04", "stuttgart": "Stuttgart", "union berlin": "Union Berlin",
    # Ligue 1
    "angers": "Angers", "auxerre": "Auxerre", "le havre": "Le Havre", "le mans": "Le Mans",
    "lens": "Lens", "lille": "Lille", "lorient": "Lorient", "lyon": "Lyon",
    "marseille": "Marseille", "monaco": "Monaco", "nice": "Nice", "psg": "Paris SG",
    "paris": "Paris FC", "paris fc": "Paris FC", "stade brest 29": "Brest",
    "stade rennais": "Rennes", "strasbourg alsace": "Strasbourg", "toulouse": "Toulouse",
    "troyes": "Troyes",
}

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
# KXLALIGAGAME-26SEP20VCFRSO-VCF
_TICKER_RE = re.compile(r"^(KX[A-Z0-9]+)-(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]+)-([A-Z0-9]+)$")


def kalshi_team(name: str) -> str | None:
    # Kalshi writes M´gladbach with an acute accent, which NFKD would split into
    # a space and a combining mark: swap it for the apostrophe first
    return KALSHI_ALIASES.get(normalize((name or "").replace("´", "'").replace("`", "'")))


def parse_ticker(ticker: str) -> tuple[str, datetime, str, str] | None:
    """(series, match date, team-pair code, outcome code)."""
    m = _TICKER_RE.match(ticker or "")
    if not m:
        return None
    series, yy, mon, dd, pair, code = m.groups()
    month = _MONTHS.get(mon)
    if month is None:
        return None
    return series, datetime(2000 + int(yy), month, int(dd)), pair, code


@dataclass
class KalshiGame:
    league: str
    event: str
    date: datetime
    home: str               # football-data names
    away: str
    tickers: dict[str, str]  # H/D/A -> market ticker
    result: str | None


def group_games(markets: list[dict]) -> tuple[list[KalshiGame], set[str]]:
    """Fold settled Kalshi markets into games. Home is the team whose code
    opens the pair in the event ticker (verified against football-data)."""
    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        by_event[m.get("event_ticker", "")].append(m)
    games, unmatched = [], set()
    for event, ms in by_event.items():
        if len(ms) != 3:
            continue
        parsed = {m["ticker"]: parse_ticker(m["ticker"]) for m in ms}
        if any(p is None for p in parsed.values()):
            continue
        series, date, pair, _ = next(iter(parsed.values()))
        league = KALSHI_SERIES.get(series)
        if league is None:
            continue
        tickers: dict[str, str] = {}
        names: dict[str, str] = {}
        codes = {parsed[m["ticker"]][3]: m for m in ms}
        if "TIE" not in codes:
            continue
        tickers["D"] = codes["TIE"]["ticker"]
        others = [c for c in codes if c != "TIE"]
        home_code = next((c for c in others if pair.startswith(c)), None)
        away_code = next((c for c in others if c != home_code), None)
        if home_code is None or away_code is None or pair != home_code + away_code:
            continue
        for side, code in (("H", home_code), ("A", away_code)):
            m = codes[code]
            tickers[side] = m["ticker"]
            fd = kalshi_team(m.get("yes_sub_title") or "")
            if fd is None:
                unmatched.add(m.get("yes_sub_title") or "")
            names[side] = fd
        if names.get("H") is None or names.get("A") is None:
            continue
        winners = [s for s in OUTCOMES if codes_result(codes, tickers[s]) == "yes"]
        games.append(KalshiGame(league=league, event=event, date=date, home=names["H"],
                                away=names["A"], tickers=tickers,
                                result=winners[0] if len(winners) == 1 else None))
    return games, unmatched


def codes_result(codes: dict[str, dict], ticker: str) -> str:
    for m in codes.values():
        if m["ticker"] == ticker:
            return m.get("result") or ""
    return ""


def fetch_settled(http, series: str) -> list[dict]:
    out, cursor = [], None
    while True:
        params = {"series_ticker": series, "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = http.get(f"{KALSHI}/markets", params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        rows = data.get("markets") or []
        out.extend(rows)
        cursor = data.get("cursor")
        if not cursor or not rows:
            break
    log.info("kalshi %s: %d settled markets", series, len(out))
    return out


def pair_games(games: list[KalshiGame], poly: list[PolyMatch]) -> list[tuple[KalshiGame, PolyMatch]]:
    by_key: dict[tuple, list[PolyMatch]] = defaultdict(list)
    for pm in poly:
        h, a = team(pm.home), team(pm.away)
        if h and a:
            by_key[(pm.league, h, a)].append(pm)
    pairs = []
    for g in games:
        cands = [pm for pm in by_key.get((g.league, g.home, g.away), [])
                 if abs((pm.kickoff.replace(tzinfo=None).replace(hour=0, minute=0, second=0)
                         - g.date).days) <= 1]
        if len(cands) != 1:
            continue
        pm = cands[0]
        if g.result != pm.result:
            log.warning("result mismatch %s vs %s — skipped", g.event, pm.slug)
            continue
        pairs.append((g, pm))
    return pairs


# ---------------------------------------------------------------- candles

def fetch_candles(http, series: str, ticker: str, kickoff: datetime, cache_dir: Path,
                  pause: float = 0.4) -> list[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{ticker}.json"
    if path.exists():
        return json.loads(path.read_text())
    t0 = int(kickoff.timestamp())
    r = http.get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks",
                 params={"start_ts": t0 - 26 * 3600, "end_ts": t0 + 3600,
                         "period_interval": 60}, timeout=60)
    r.raise_for_status()
    candles = r.json().get("candlesticks") or []
    path.write_text(json.dumps(candles))
    time.sleep(pause)
    return candles


def quote_at(candles: list[dict], kickoff_ts: int, horizon_h: float,
             tolerance_h: float = 3) -> tuple[float, float] | None:
    """(yes_bid, yes_ask) from the last hourly candle ending at or before
    kickoff - horizon, within `tolerance_h`; None without a two-sided book."""
    target = kickoff_ts - horizon_h * 3600
    before = [c for c in candles if c.get("end_period_ts", 0) <= target]
    if not before:
        return None
    c = before[-1]
    if target - c["end_period_ts"] > tolerance_h * 3600:
        return None
    try:
        bid = float(c["yes_bid"]["close_dollars"])
        ask = float(c["yes_ask"]["close_dollars"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 < bid < ask < 1):
        return None
    return bid, ask


def build_rows(http, pairs: list[tuple[KalshiGame, PolyMatch]], pm_rows: list[dict],
               cache_dir: Path) -> list[dict]:
    pm_by = {(r["slug"], r["side"], r["horizon_h"]): r for r in pm_rows}
    out = []
    for g, pm in pairs:
        t0 = int(pm.kickoff.timestamp())
        series = g.event.split("-", 1)[0]
        for side in OUTCOMES:
            try:
                candles = fetch_candles(http, series, g.tickers[side], pm.kickoff, cache_dir)
            except Exception:  # noqa: BLE001 — one market failing is not fatal
                log.exception("candles failed for %s", g.tickers[side])
                continue
            for h in HORIZONS_H:
                q = quote_at(candles, t0, h)
                pr = pm_by.get((pm.slug, side, h))
                if q is None or pr is None:
                    continue
                out.append({"league": g.league, "slug": pm.slug, "ticker": g.tickers[side],
                            "kickoff": pm.kickoff.isoformat(), "side": side, "horizon_h": h,
                            "pm_price": pr["pm_price"], "k_bid": q[0], "k_ask": q[1],
                            "k_mid": round((q[0] + q[1]) / 2, 4),
                            "outcome": 1 if g.result == side else 0})
    return out


# ---------------------------------------------------------------- analysis

def kalshi_fee(price: float) -> float:
    return KALSHI_FEE_RATE * price * (1 - price)


def lead_lag(rows: list[dict], h_from: int, h_to: int) -> dict[str, tuple[float, float, int]]:
    """Regress each venue's move from h_from to h_to on the other venue's
    lead at h_from. Returns {mover: (beta, t, n)}. A beta near 1 on
    'kalshi' means Kalshi closes the gap toward Polymarket."""
    by = {(r["slug"], r["side"], r["horizon_h"]): r for r in rows}
    pts = []
    for (slug, side, h), r in by.items():
        if h != h_from:
            continue
        later = by.get((slug, side, h_to))
        if later is None:
            continue
        gap = r["pm_price"] - r["k_mid"]
        pts.append({"gap": gap, "outcome": later["k_mid"] - r["k_mid"],
                    "gap_rev": -gap, "pm_move": later["pm_price"] - r["pm_price"]})
    if len(pts) < 10:
        return {}
    k = ols(pts, ("gap",))["gap"]
    p = ols([{"gap_rev": x["gap_rev"], "outcome": x["pm_move"]} for x in pts], ("gap_rev",))["gap_rev"]
    return {"kalshi": (k[0], k[1], len(pts)), "polymarket": (p[0], p[1], len(pts))}


def strategy(rows: list[dict], threshold: float, exit_rows: list[dict] | None = None) -> dict:
    """Buy YES on Kalshi at the ask when Polymarket's price exceeds it by
    `threshold`. Return per $1 after the taker fee, to settlement, or marked
    at the 1h Kalshi mid when `exit_rows` is given."""
    exit_by = {(r["slug"], r["side"]): r["k_mid"] for r in (exit_rows or [])}
    rets = []
    for r in rows:
        if r["pm_price"] - r["k_ask"] < threshold:
            continue
        cost = r["k_ask"] + kalshi_fee(r["k_ask"])
        if exit_rows is not None:
            ex = exit_by.get((r["slug"], r["side"]))
            if ex is None:
                continue
            rets.append(ex / cost - 1)
        else:
            rets.append(r["outcome"] / cost - 1)
    if not rets:
        return {"n": 0}
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1)
    return {"n": len(rets), "mean_ret": mean, "se": math.sqrt(var / len(rets))}


def analyse(rows: list[dict], out=sys.stdout) -> None:
    for h in HORIZONS_H:
        hr = [r for r in rows if r["horizon_h"] == h]
        if len(hr) < 30:
            continue
        n_matches = len({r["slug"] for r in hr})
        d, lo, hi = paired_brier_diff(hr, "pm_price", "k_mid")
        reg = ols(hr, ("pm_price", "k_mid"))
        gap = sorted(abs(r["pm_price"] - r["k_mid"]) for r in hr)
        spread = sorted(r["k_ask"] - r["k_bid"] for r in hr)
        print(f"\n=== {h}h before kickoff: n={len(hr)} rows / {n_matches} matches ===", file=out)
        print(f"Brier polymarket {brier(hr, 'pm_price'):.4f} vs kalshi mid {brier(hr, 'k_mid'):.4f}"
              f" | diff {d:+.4f} [{lo:+.4f},{hi:+.4f}] | beta_pm {reg['pm_price'][0]:+.2f} "
              f"(t {reg['pm_price'][1]:+.1f}), beta_kalshi {reg['k_mid'][0]:+.2f} "
              f"(t {reg['k_mid'][1]:+.1f})", file=out)
        print(f"|polymarket - kalshi mid|: median {gap[len(gap) // 2]:.3f}, 90th "
              f"{gap[int(0.9 * len(gap))]:.3f} | kalshi spread median "
              f"{spread[len(spread) // 2]:.3f}", file=out)
        if h > 1:
            ll = lead_lag(rows, h, 1)
            if ll:
                print(f"lead-lag {h}h -> 1h: kalshi move on (pm - k) gap: beta "
                      f"{ll['kalshi'][0]:+.2f} (t {ll['kalshi'][1]:+.1f}); polymarket move on "
                      f"(k - pm) gap: beta {ll['polymarket'][0]:+.2f} (t {ll['polymarket'][1]:+.1f})"
                      f"; n={ll['kalshi'][2]}", file=out)
        one_h = [r for r in rows if r["horizon_h"] == 1]
        for thr in (0.02, 0.03, 0.05):
            s = strategy(hr, thr)
            if not s["n"]:
                continue
            line = (f"    buy kalshi when pm - ask >= {thr:.2f}: n={s['n']:3d} to settlement "
                    f"{s['mean_ret']:+.3f}/$1 (se {s['se']:.3f})")
            if h > 1:
                m = strategy(hr, thr, exit_rows=one_h)
                if m["n"]:
                    line += f" | marked at 1h mid {m['mean_ret']:+.3f}/$1 (se {m['se']:.3f})"
            print(line, file=out)


FIELDS = ["league", "slug", "ticker", "kickoff", "side", "horizon_h", "pm_price",
          "k_bid", "k_ask", "k_mid", "outcome"]


def load_venue_rows(path: Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(path.open()):
        row = {k: (v if k in ("league", "slug", "ticker", "kickoff", "side") else float(v))
               for k, v in r.items()}
        row["horizon_h"] = int(row["horizon_h"])
        row["outcome"] = int(row["outcome"])
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket vs Kalshi on the same soccer matches")
    parser.add_argument("--analyse", action="store_true")
    parser.add_argument("--pm-csv", default="docs/sportsbook_rows.csv")
    parser.add_argument("--csv", default="docs/venue_rows.csv")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(__file__).resolve().parent.parent
    csv_path = root / args.csv
    if not args.analyse:
        http = retrying_session(total=5, backoff=2.0)
        markets = [m for s in KALSHI_SERIES for m in fetch_settled(http, s)]
        games, unmatched = group_games(markets)
        log.info("kalshi: %d games; unmatched names: %s", len(games), sorted(unmatched))
        poly = fetch_poly_matches(http)
        pairs = pair_games(games, poly)
        log.info("paired %d of %d kalshi games with polymarket", len(pairs), len(games))
        pm_rows = load_rows(root / args.pm_csv)
        rows = build_rows(http, pairs, pm_rows, root / "data" / "kalshi-candles")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        log.info("wrote %d rows to %s", len(rows), csv_path)
    rows = load_venue_rows(csv_path)
    print(f"{len(rows)} rows, {len({r['slug'] for r in rows})} matches, leagues "
          f"{sorted({r['league'] for r in rows})}")
    analyse(rows)


if __name__ == "__main__":
    main()
