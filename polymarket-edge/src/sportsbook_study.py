"""Does a sportsbook reference beat Polymarket's price? Measured on settled
soccer, with no API key.

    python -m src.sportsbook_study            # collect + analyse
    python -m src.sportsbook_study --analyse  # re-run the analysis on the cached CSV

The sportsbook-consensus model is the one polymarket-edge model the
resolved-market study still gives a reason to believe in, and it has never
run for want of ODDS_API_KEY. This study tests the class of edge it relies on
with free data: football-data.co.uk publishes Pinnacle and market-average
odds for the big European leagues, both the pre-weekend line (AvgH/AvgD/AvgA)
and the closing line (AvgCH/AvgCD/AvgCA, plus Bet365, Pinnacle where quoted and
the Betfair Exchange); Polymarket lists a moneyline market
per outcome (home, draw, away) for the same matches, and its trade log gives
the price at any time before kickoff.

For every match on both sources: the de-vigged sportsbook probability, the
Polymarket price 1h / 6h / 24h before kickoff, and the result. Three
questions, each answered on the same rows:

1. Whose distribution is closer to the outcomes (Brier, paired bootstrap)?
2. Does the sportsbook carry information the Polymarket price lacks
   (regression of the outcome on both; the coefficient on the book)?
3. Does buying the outcome the book likes more than Polymarket pay, per $1,
   after a 1c spread?

Two comparisons are honest and they bound the answer: the book's pre-weekend
line vs Polymarket a day out (the book has no timing advantage), and the
closing line vs Polymarket an hour out (the book closes at kickoff and sees
an hour more of news). A live scanner sits between those.

Team names are mapped by an explicit table (below). Unmatched means skipped;
the run prints what it could not match so the table can be extended.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import random
import sys
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .calibration_study import yes_price_before
from .http_util import retrying_session
from .models.devig import devig_proportional

log = logging.getLogger(__name__)

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
FOOTBALL_DATA = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
HORIZONS_H = (1, 6, 24)
OUTCOMES = ("H", "D", "A")
REFS = {"avg_open": "Market-average pre-weekend", "avg_close": "Market-average closing",
        "b365_close": "Bet365 closing", "pin_close": "Pinnacle closing",
        "bfe_close": "Betfair Exchange closing"}

# Polymarket tag -> football-data league code
LEAGUES = {"epl": "E0", "la-liga": "SP1", "bundesliga": "D1", "ligue-1": "F1"}
SEASONS = ("2526", "2627")

# Polymarket team name (either of its two naming conventions) -> football-data
# name. Conservative on purpose: a name not in here is skipped, never guessed.
TEAM_ALIASES = {
    # EPL
    "afc bournemouth": "Bournemouth", "bournemouth": "Bournemouth",
    "arsenal": "Arsenal", "arsenal fc": "Arsenal",
    "aston villa": "Aston Villa", "aston villa fc": "Aston Villa",
    "brentford": "Brentford", "brentford fc": "Brentford",
    "brighton": "Brighton", "brighton & hove albion fc": "Brighton",
    "burnley": "Burnley", "burnley fc": "Burnley",
    "chelsea": "Chelsea", "chelsea fc": "Chelsea",
    "coventry city fc": "Coventry", "crystal palace": "Crystal Palace",
    "crystal palace fc": "Crystal Palace", "everton": "Everton", "everton fc": "Everton",
    "fulham": "Fulham", "fulham fc": "Fulham", "hull city afc": "Hull",
    "ipswich town fc": "Ipswich", "leeds united": "Leeds", "leeds united fc": "Leeds",
    "liverpool": "Liverpool", "liverpool fc": "Liverpool",
    "manchester city": "Man City", "manchester city fc": "Man City",
    "manchester united": "Man United", "manchester united fc": "Man United",
    "newcastle": "Newcastle", "newcastle united fc": "Newcastle",
    "nottingham forest": "Nott'm Forest", "nottingham forest fc": "Nott'm Forest",
    "sunderland afc": "Sunderland", "sunderland": "Sunderland",
    "tottenham": "Tottenham", "tottenham hotspur fc": "Tottenham",
    "west ham": "West Ham", "west ham united fc": "West Ham",
    "wolverhampton wanderers fc": "Wolves", "wolves": "Wolves",
    # La Liga
    "alaves": "Alaves", "deportivo alaves": "Alaves", "athletic club": "Ath Bilbao",
    "atletico madrid": "Ath Madrid", "club atletico de madrid": "Ath Madrid",
    "barcelona": "Barcelona", "fc barcelona": "Barcelona",
    "ca osasuna": "Osasuna", "osasuna": "Osasuna",
    "celta vigo": "Celta", "rc celta de vigo": "Celta", "elche cf": "Elche", "elche": "Elche",
    "espanyol": "Espanol", "rcd espanyol de barcelona": "Espanol",
    "getafe": "Getafe", "getafe cf": "Getafe", "girona": "Girona", "girona fc": "Girona",
    "levante ud": "Levante", "levante": "Levante", "mallorca": "Mallorca",
    "rcd mallorca": "Mallorca", "malaga cf": "Malaga", "rc deportivo a coruna": "La Coruna",
    "rayo vallecano": "Vallecano", "rayo vallecano de madrid": "Vallecano",
    "real betis": "Betis", "real betis balompie": "Betis",
    "real madrid": "Real Madrid", "real madrid cf": "Real Madrid",
    "real oviedo": "Oviedo", "real racing club": "Santander",
    "real sociedad": "Sociedad", "real sociedad de futbol": "Sociedad",
    "sevilla": "Sevilla", "sevilla fc": "Sevilla", "valencia": "Valencia",
    "valencia cf": "Valencia", "villarreal": "Villarreal", "villarreal cf": "Villarreal",
    # Bundesliga
    "1. fc heidenheim": "Heidenheim", "1. fc heidenheim 1846": "Heidenheim",
    "1. fc koln": "FC Koln", "1. fc union berlin": "Union Berlin", "union berlin": "Union Berlin",
    "1. fsv mainz 05": "Mainz", "fsv mainz 05": "Mainz",
    "1899 hoffenheim": "Hoffenheim", "tsg 1899 hoffenheim": "Hoffenheim",
    "bv borussia 09 dortmund": "Dortmund", "borussia dortmund": "Dortmund",
    "bayer 04 leverkusen": "Leverkusen", "bayer leverkusen": "Leverkusen",
    "bayern munchen": "Bayern Munich", "fc bayern munchen": "Bayern Munich",
    "borussia monchengladbach": "M'gladbach", "eintracht frankfurt": "Ein Frankfurt",
    "fc augsburg": "Augsburg", "fc schalke 04": "Schalke 04",
    "fc st. pauli": "St Pauli", "fc st. pauli 1910": "St Pauli",
    "hamburger sv": "Hamburg", "paderborn": "Paderborn", "sc paderborn 07": "Paderborn",
    "rb leipzig": "RB Leipzig", "sc freiburg": "Freiburg", "sv 07 elversberg": "Elversberg",
    "sv werder bremen": "Werder Bremen", "werder bremen": "Werder Bremen",
    "vfb stuttgart": "Stuttgart", "vfl wolfsburg": "Wolfsburg", "wolfsburg": "Wolfsburg",
    # Ligue 1
    "aj auxerre": "Auxerre", "as monaco fc": "Monaco", "angers sco": "Angers",
    "es troyes ac": "Troyes", "fc lorient": "Lorient", "fc metz": "Metz",
    "fc nantes": "Nantes", "le havre ac": "Le Havre", "le mans fc": "Le Mans",
    "lille osc": "Lille", "nice": "Nice", "ogc nice": "Nice",
    "olympique lyonnais": "Lyon", "olympique de marseille": "Marseille",
    "paris fc": "Paris FC", "paris saint-germain fc": "Paris SG",
    "rc strasbourg alsace": "Strasbourg", "racing club de lens": "Lens",
    "saint-etienne": "St Etienne", "stade brestois 29": "Brest",
    "stade rennais fc 1901": "Rennes", "toulouse fc": "Toulouse",
}


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def team(name: str) -> str | None:
    return TEAM_ALIASES.get(normalize(name))


# ---------------------------------------------------------------- bookmaker side

@dataclass
class BookRow:
    league: str
    date: datetime          # local calendar date of the match (naive)
    home: str
    away: str
    result: str             # H | D | A
    # de-vigged, keys H/D/A; {} where the column is blank for that row
    avg_open: dict[str, float]      # market average, pre-weekend collection
    avg_close: dict[str, float]     # market average at kickoff
    b365_close: dict[str, float]    # Bet365 closing
    pin_close: dict[str, float]     # Pinnacle closing (blank for ~half of 2025-26 on)
    bfe_close: dict[str, float]     # Betfair Exchange closing


def _odds(row: dict, h: str, d: str, a: str) -> dict[str, float]:
    try:
        odds = {"H": float(row[h]), "D": float(row[d]), "A": float(row[a])}
    except (KeyError, TypeError, ValueError):
        return {}
    if any(v <= 1.0 for v in odds.values()):
        return {}
    return devig_proportional(odds)


def parse_book_csv(text: str, league: str) -> list[BookRow]:
    out = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        if not row.get("Date") or row.get("FTR") not in OUTCOMES:
            continue
        try:
            d = datetime.strptime(row["Date"], "%d/%m/%Y")
        except ValueError:
            try:
                d = datetime.strptime(row["Date"], "%d/%m/%y")
            except ValueError:
                continue
        out.append(BookRow(
            league=league, date=d, home=row["HomeTeam"], away=row["AwayTeam"],
            result=row["FTR"],
            avg_open=_odds(row, "AvgH", "AvgD", "AvgA"),
            avg_close=_odds(row, "AvgCH", "AvgCD", "AvgCA"),
            b365_close=_odds(row, "B365CH", "B365CD", "B365CA"),
            pin_close=_odds(row, "PSCH", "PSCD", "PSCA"),
            bfe_close=_odds(row, "BFECH", "BFECD", "BFECA"),
        ))
    return out


def fetch_book_rows(http, cache_dir: Path) -> list[BookRow]:
    rows = []
    cache_dir.mkdir(parents=True, exist_ok=True)
    for code in LEAGUES.values():
        for season in SEASONS:
            path = cache_dir / f"{season}_{code}.csv"
            if not path.exists():
                r = http.get(FOOTBALL_DATA.format(season=season, code=code),
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
                r.raise_for_status()
                path.write_text(r.text)
            rows.extend(parse_book_csv(path.read_text(), code))
    log.info("football-data: %d matches with results", len(rows))
    return rows


# ---------------------------------------------------------------- polymarket side

@dataclass
class PolyMatch:
    league: str
    slug: str
    home: str               # Polymarket's own names
    away: str
    kickoff: datetime       # UTC
    markets: dict[str, dict]     # outcome -> {condition_id, yes_token, resolved}
    result: str | None      # H | D | A from the resolved YES market


def _which_outcome(question: str, home: str, away: str) -> str | None:
    q = normalize(question)
    if "draw" in q:
        return "D"
    if q.startswith(f"will {normalize(home)} win"):
        return "H"
    if q.startswith(f"will {normalize(away)} win"):
        return "A"
    return None


def parse_event(ev: dict, league: str) -> PolyMatch | None:
    title = ev.get("title") or ""
    if " vs. " not in title:
        return None
    home, away = (s.strip() for s in title.split(" vs. ", 1))
    markets: dict[str, dict] = {}
    kickoff = None
    for m in ev.get("markets") or []:
        if m.get("sportsMarketType") != "moneyline" or not m.get("gameStartTime"):
            continue
        side = _which_outcome(m.get("question") or "", home, away)
        if side is None:
            continue
        try:
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            tokens = json.loads(m.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            continue
        if len(prices) != 2 or len(tokens) != 2:
            continue
        resolved = prices[0] >= 0.99 if prices[0] >= 0.99 or prices[0] <= 0.01 else None
        if resolved is None:
            continue
        markets[side] = {"condition_id": m.get("conditionId"), "yes_token": tokens[0],
                         "resolved": resolved, "volume": float(m.get("volumeNum") or 0)}
        ks = m["gameStartTime"].replace(" ", "T")
        ks = ks[:-3] + ":00" if ks.endswith("+00") else ks
        kickoff = datetime.fromisoformat(ks.replace("Z", "+00:00")).astimezone(timezone.utc)
    if len(markets) != 3 or kickoff is None:
        return None
    winners = [s for s, mk in markets.items() if mk["resolved"]]
    if len(winners) != 1:
        return None       # abandoned / voided / inconsistent resolution
    return PolyMatch(league=league, slug=ev.get("slug") or "", home=home, away=away,
                     kickoff=kickoff, markets=markets, result=winners[0])


def fetch_poly_matches(http, max_events: int = 3000) -> list[PolyMatch]:
    out = []
    for tag, league in LEAGUES.items():
        offset, n = 0, 0
        while offset < max_events:
            r = http.get(GAMMA_EVENTS, params={"tag_slug": tag, "closed": "true",
                                               "limit": 100, "offset": offset}, timeout=60)
            r.raise_for_status()
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            for ev in batch:
                pm = parse_event(ev, league)
                if pm:
                    out.append(pm)
                    n += 1
            if len(batch) < 100:
                break
            offset += 100
        log.info("polymarket %s: %d resolved matches", tag, n)
    return out


# ---------------------------------------------------------------- matching

def match(poly: list[PolyMatch], book: list[BookRow]) -> tuple[list[tuple[PolyMatch, BookRow]], set[str]]:
    """Pair on (league, home, away) with the match date within a day of the
    UTC kickoff. Returns pairs and the Polymarket names the alias table lacks."""
    by_key: dict[tuple[str, str, str], list[BookRow]] = defaultdict(list)
    for b in book:
        by_key[(b.league, b.home, b.away)].append(b)
    pairs, unmatched = [], set()
    for pm in poly:
        h, a = team(pm.home), team(pm.away)
        if h is None:
            unmatched.add(pm.home)
        if a is None:
            unmatched.add(pm.away)
        if h is None or a is None:
            continue
        kd = pm.kickoff.replace(tzinfo=None)
        cands = [b for b in by_key.get((pm.league, h, a), [])
                 if abs((b.date - kd.replace(hour=0, minute=0, second=0)).days) <= 1]
        if len(cands) != 1:
            continue
        b = cands[0]
        if b.result != pm.result:
            log.warning("result mismatch %s: book %s vs polymarket %s — skipped",
                        pm.slug, b.result, pm.result)
            continue
        pairs.append((pm, b))
    return pairs, unmatched


# ---------------------------------------------------------------- price sampling

def sample_pair(http, pm: PolyMatch, b: BookRow) -> list[dict]:
    rows = []
    t0 = int(pm.kickoff.timestamp())
    for side in OUTCOMES:
        mk = pm.markets[side]
        market = {"condition_id": mk["condition_id"], "yes_token": mk["yes_token"]}
        for h in HORIZONS_H:
            p = yes_price_before(http, market, t0 - h * 3600)
            if p is None or not (0 < p < 1):
                continue
            rows.append({
                "league": pm.league, "slug": pm.slug, "kickoff": pm.kickoff.isoformat(),
                "side": side, "horizon_h": h, "pm_price": round(p, 4),
                **{k: round(getattr(b, k).get(side, float("nan")), 4) for k in REFS},
                "outcome": 1 if b.result == side else 0,
                "volume": round(mk["volume"]),
            })
    return rows


# ---------------------------------------------------------------- analysis

def brier(rows: list[dict], key: str) -> float:
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows)


def paired_brier_diff(rows: list[dict], a: str, b: str, n_boot: int = 2000,
                      seed: int = 0) -> tuple[float, float, float]:
    """mean(Brier_a - Brier_b) with a bootstrap 95% CI, resampling matches
    (all three outcome rows of a match move together)."""
    by_match: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_match[r["slug"]].append(r)
    groups = list(by_match.values())
    def stat(gs):
        d = [(r[a] - r["outcome"]) ** 2 - (r[b] - r["outcome"]) ** 2 for g in gs for r in g]
        return sum(d) / len(d)
    point = stat(groups)
    rng = random.Random(seed)
    boots = sorted(stat([groups[rng.randrange(len(groups))] for _ in groups])
                   for _ in range(n_boot))
    return point, boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot)]


def ols(rows: list[dict], xs: tuple[str, ...]) -> dict[str, tuple[float, float]]:
    """Linear probability model outcome ~ 1 + xs. Returns {name: (beta, t)}."""
    n, k = len(rows), len(xs) + 1
    X = [[1.0] + [r[x] for x in xs] for r in rows]
    y = [float(r["outcome"]) for r in rows]
    xtx = [[sum(X[i][p] * X[i][q] for i in range(n)) for q in range(k)] for p in range(k)]
    xty = [sum(X[i][p] * y[i] for i in range(n)) for p in range(k)]
    inv = _invert(xtx)
    beta = [sum(inv[p][q] * xty[q] for q in range(k)) for p in range(k)]
    resid = [y[i] - sum(beta[p] * X[i][p] for p in range(k)) for i in range(n)]
    s2 = sum(e * e for e in resid) / max(1, n - k)
    out = {}
    for p, name in enumerate(("const",) + xs):
        se = math.sqrt(max(s2 * inv[p][p], 1e-18))
        out[name] = (beta[p], beta[p] / se)
    return out


def _invert(m: list[list[float]]) -> list[list[float]]:
    k = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(k)] for i, row in enumerate(m)]
    for i in range(k):
        piv = max(range(i, k), key=lambda r: abs(a[r][i]))
        a[i], a[piv] = a[piv], a[i]
        d = a[i][i]
        if abs(d) < 1e-12:
            raise ValueError("singular design matrix")
        a[i] = [v / d for v in a[i]]
        for r in range(k):
            if r != i and a[r][i]:
                f = a[r][i]
                a[r] = [rv - f * iv for rv, iv in zip(a[r], a[i])]
    return [row[k:] for row in a]


def strategy(rows: list[dict], ref: str, threshold: float, spread: float = 0.01) -> dict:
    """Buy the Polymarket outcome when the book's probability exceeds the
    Polymarket price by `threshold`, paying price + spread, hold to settlement.
    Return per $1 staked; standard error of the mean."""
    picks = [r for r in rows if r[ref] - r["pm_price"] >= threshold]
    if not picks:
        return {"n": 0}
    rets = [(r["outcome"] / (r["pm_price"] + spread)) - 1 for r in picks]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1)
    return {"n": len(picks), "hit": sum(r["outcome"] for r in picks) / len(picks),
            "mean_ret": mean, "se": math.sqrt(var / len(rets)),
            "mean_gap": sum(r[ref] - r["pm_price"] for r in picks) / len(picks)}


def analyse(rows: list[dict], out=sys.stdout) -> None:
    def clean(rs, keys):
        return [r for r in rs if all(not math.isnan(r[k]) for k in keys)]

    for h in HORIZONS_H:
        hr = [r for r in rows if r["horizon_h"] == h]
        if not hr:
            continue
        print(f"\n=== Polymarket {h}h before kickoff ===", file=out)
        for ref, label in REFS.items():
            rs = clean(hr, (ref,))
            if len(rs) < 30:
                continue
            n_matches = len({r["slug"] for r in rs})
            d, lo, hi = paired_brier_diff(rs, "pm_price", ref)
            reg = ols(rs, ("pm_price", ref))
            print(f"{label:<24} n={len(rs):5d} rows/{n_matches:4d} matches | Brier polymarket "
                  f"{brier(rs, 'pm_price'):.4f} vs {ref} {brier(rs, ref):.4f} | diff {d:+.4f} "
                  f"[{lo:+.4f},{hi:+.4f}] | beta_book {reg[ref][0]:+.2f} (t {reg[ref][1]:+.1f}), "
                  f"beta_pm {reg['pm_price'][0]:+.2f} (t {reg['pm_price'][1]:+.1f})", file=out)
            for thr in (0.02, 0.03, 0.05):
                s = strategy(rs, ref, thr)
                if s["n"]:
                    print(f"    buy when {ref} - price >= {thr:.2f}: n={s['n']:4d} hit {s['hit']:.3f} "
                          f"mean gap {s['mean_gap']:.3f} -> return {s['mean_ret']:+.3f} per $1 "
                          f"(se {s['se']:.3f}, t {s['mean_ret'] / s['se'] if s['se'] else 0:+.1f})",
                          file=out)
        gap = [abs(r["pm_price"] - r["avg_close"]) for r in clean(hr, ("avg_close",))]
        if gap:
            gap.sort()
            print(f"|polymarket - market-average close|: median {gap[len(gap) // 2]:.3f}, "
                  f"90th pct {gap[int(0.9 * len(gap))]:.3f}", file=out)


FIELDS = ["league", "slug", "kickoff", "side", "horizon_h", "pm_price", *REFS,
          "outcome", "volume"]


def load_rows(path: Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(path.open()):
        rows.append({k: (v if k in ("league", "slug", "kickoff", "side") else float(v))
                     for k, v in r.items()})
        rows[-1]["horizon_h"] = int(rows[-1]["horizon_h"])
        rows[-1]["outcome"] = int(rows[-1]["outcome"])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="sportsbook reference vs Polymarket, settled soccer")
    parser.add_argument("--analyse", action="store_true", help="skip collection, use the cached CSV")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--csv", default="docs/sportsbook_rows.csv")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(__file__).resolve().parent.parent
    csv_path = root / args.csv

    if not args.analyse:
        http = retrying_session()
        book = fetch_book_rows(http, root / "data" / "football-data")
        poly = fetch_poly_matches(http)
        pairs, unmatched = match(poly, book)
        log.info("matched %d of %d Polymarket matches; unmatched names: %s",
                 len(pairs), len(poly), sorted(unmatched))
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(sample_pair, http, pm, b) for pm, b in pairs]
            for i, f in enumerate(as_completed(futs), 1):
                try:
                    rows.extend(f.result())
                except Exception:  # noqa: BLE001 — one match failing must not sink the run
                    log.exception("sampling failed")
                if i % 100 == 0:
                    log.info("sampled %d/%d matches, %d rows", i, len(pairs), len(rows))
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        log.info("wrote %d rows to %s", len(rows), csv_path)

    rows = load_rows(csv_path)
    print(f"{len(rows)} rows, {len({r['slug'] for r in rows})} matches, leagues "
          f"{sorted({r['league'] for r in rows})}")
    analyse(rows)


if __name__ == "__main__":
    main()
