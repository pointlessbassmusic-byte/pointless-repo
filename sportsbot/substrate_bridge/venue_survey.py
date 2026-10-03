"""Venue survey: which sports both Kalshi and Polymarket list, what each
charges there, and whether a live cross-venue gap survives those costs.
DATA ONLY — one snapshot of both books per paired game; nothing here
trades or feeds the strategy.

Pairing. Kalshi labels a side by city ("Los Angeles R"), Polymarket by
nickname ("Rams"); both carry league team codes (Kalshi event ticker tail
BUFLAR = BUF + LAR; Polymarket slug nfl-la-phi-...). Teams are joined
through a per-league code table, UFC fighters through normalised names.
Soccer is excluded (Polymarket lists Yes/No per team, Kalshi three-way).

Fees. Kalshi: 0.07 x series multiplier x p(1-p) per contract taker;
series with fee_type quadratic_with_maker_fees charge makers a quarter of
that, others nothing. Polymarket: per-market feeSchedule rate x p(1-p),
taker only (NFL/NBA/MLB 0.03, tennis/NHL/UFC 0.05 as of 2026-10-02).
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_RATE = 0.07
KALSHI_MAKER_SHARE = 0.25

# code -> Polymarket nickname (lower-case). Kalshi codes from the ticker tail.
NFL = {"ARI": "cardinals", "ATL": "falcons", "BAL": "ravens", "BUF": "bills", "CAR": "panthers",
       "CHI": "bears", "CIN": "bengals", "CLE": "browns", "DAL": "cowboys", "DEN": "broncos",
       "DET": "lions", "GB": "packers", "HOU": "texans", "IND": "colts", "JAX": "jaguars",
       "KC": "chiefs", "LAC": "chargers", "LAR": "rams", "LV": "raiders", "MIA": "dolphins",
       "MIN": "vikings", "NE": "patriots", "NO": "saints", "NYG": "giants", "NYJ": "jets",
       "PHI": "eagles", "PIT": "steelers", "SEA": "seahawks", "SF": "49ers", "TB": "buccaneers",
       "TEN": "titans", "WAS": "commanders"}
NBA = {"ATL": "hawks", "BOS": "celtics", "BKN": "nets", "CHA": "hornets", "CHI": "bulls",
       "CLE": "cavaliers", "DAL": "mavericks", "DEN": "nuggets", "DET": "pistons", "GSW": "warriors",
       "GS": "warriors", "HOU": "rockets", "IND": "pacers", "LAC": "clippers", "LAL": "lakers",
       "MEM": "grizzlies", "MIA": "heat", "MIL": "bucks", "MIN": "timberwolves", "NOP": "pelicans",
       "NO": "pelicans", "NYK": "knicks", "NY": "knicks", "OKC": "thunder", "ORL": "magic",
       "PHI": "76ers", "PHX": "suns", "POR": "trail blazers", "SAC": "kings", "SAS": "spurs",
       "SA": "spurs", "TOR": "raptors", "UTA": "jazz", "WAS": "wizards"}
NHL = {"ANA": "ducks", "BOS": "bruins", "BUF": "sabres", "CGY": "flames", "CAR": "hurricanes",
       "CHI": "blackhawks", "COL": "avalanche", "CBJ": "blue jackets", "DAL": "stars", "DET": "red wings",
       "EDM": "oilers", "FLA": "panthers", "LAK": "kings", "LA": "kings", "MIN": "wild", "MTL": "canadiens",
       "NSH": "predators", "NJD": "devils", "NJ": "devils", "NYI": "islanders", "NYR": "rangers",
       "OTT": "senators", "PHI": "flyers", "PIT": "penguins", "SJS": "sharks", "SJ": "sharks",
       "SEA": "kraken", "STL": "blues", "TBL": "lightning", "TB": "lightning", "TOR": "maple leafs",
       "UTA": "mammoth", "UTAH": "mammoth", "VAN": "canucks", "VGK": "golden knights", "WSH": "capitals",
       "WPG": "jets"}

LEAGUES = {
    # sport: (kalshi series, polymarket tag, code table or None for name matching)
    "nfl": ("KXNFLGAME", 450, NFL),
    "nba": ("KXNBAGAME", 745, NBA),
    "nhl": ("KXNHLGAME", 899, NHL),
    "ufc": ("KXUFCFIGHT", 279, None),
    "tennis": (("KXATPMATCH", "KXWTAMATCH"), 864, None),
    "mlb": ("KXMLBGAME", 100381, "mlb"),
}

_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def kalshi_event_date(event_ticker: str):
    m = _DATE.search(event_ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    return datetime(2000 + int(yy), _MONTHS[mon], int(dd)).date()


def split_codes(tail: str, codes: set[str]) -> Optional[tuple[str, str]]:
    """AWAY+HOME codes from an event-ticker tail such as 26OCT12BUFLAR;
    the date prefix stays in the tail, so match the end of it."""
    hits = [(a, b) for a in codes for b in codes if a != b and tail.endswith(a + b)]
    return hits[0] if len(hits) == 1 else None


@dataclass
class Side:
    venue: str
    market_id: str
    label: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    fee_rate: float = 0.0          # Polymarket schedule rate, or Kalshi 0.07 x multiplier
    maker_share: float = 0.0       # fraction of taker fee a maker pays


@dataclass
class PairedGame:
    sport: str
    date: str
    kalshi: Side                   # Kalshi YES side = `label`
    polymarket: Side               # Polymarket YES side, same competitor as kalshi.label
    extra: dict = field(default_factory=dict)


def _taker_fee(p: float, rate: float) -> float:
    return rate * p * (1 - p)


# ------------------------------------------------------------------ fetch
def kalshi_markets(http, series: str) -> list[dict]:
    out, cursor = [], None
    while True:
        params = {"series_ticker": series, "status": "open", "limit": 200, "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor
        d = http.get(f"{KALSHI}/markets", params=params).json()
        out.extend(d.get("markets", []))
        cursor = d.get("cursor")
        if not cursor or not d.get("markets"):
            break
        time.sleep(0.2)
    return out


def kalshi_series_meta(http, series: str) -> dict:
    d = http.get(f"{KALSHI}/series/{series}").json().get("series") or {}
    return {"fee_multiplier": float(d.get("fee_multiplier", 1.0) or 0.0),
            "maker_share": KALSHI_MAKER_SHARE if d.get("fee_type") == "quadratic_with_maker_fees" else 0.0}


def polymarket_moneylines(http, tag: int, pages: int = 3) -> list[dict]:
    out = []
    for page in range(pages):
        evs = http.get(f"{GAMMA}/events", params={"tag_id": tag, "active": "true", "closed": "false",
                                                  "order": "volume24hr", "ascending": "false",
                                                  "limit": 100, "offset": page * 100}).json()
        if not evs:
            break
        for ev in evs:
            for m in ev.get("markets") or []:
                if m.get("sportsMarketType") != "moneyline" or m.get("closed") or not m.get("acceptingOrders", True):
                    continue
                outcomes = m.get("outcomes")
                tokens = m.get("clobTokenIds")
                outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
                tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
                if not outcomes or not tokens or len(outcomes) != 2:
                    continue
                fs = m.get("feeSchedule")
                fs = json.loads(fs) if isinstance(fs, str) else (fs or {})
                rate = float(fs.get("rate", 0.0) or 0.0) if m.get("feesEnabled") else 0.0
                out.append({"slug": ev.get("slug", ""), "condition": m.get("conditionId"), "outcomes": outcomes,
                            "tokens": tokens, "start": m.get("gameStartTime"), "rate": rate,
                            "vol24": float(m.get("volume24hr") or 0.0)})
        if len(evs) < 100:
            break
        time.sleep(0.2)
    return out


def _pm_date_et(start: str):
    if not start:
        return None
    s = start.replace(" ", "T")
    if s.endswith("+00"):
        s = s[:-3] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return (dt - timedelta(hours=4)).date()        # Kalshi tickers carry the Eastern date


# ------------------------------------------------------------------ pairing
def pair_sport(http, sport: str) -> list[PairedGame]:
    series, tag, table = LEAGUES[sport]
    series_list = series if isinstance(series, tuple) else (series,)
    pms = polymarket_moneylines(http, tag)
    pairs: list[PairedGame] = []
    for ser in series_list:
        meta = kalshi_series_meta(http, ser)
        kms = kalshi_markets(http, ser)
        by_event: dict[str, list[dict]] = defaultdict(list)
        for m in kms:
            by_event[m["event_ticker"]].append(m)
        if table is None or table == "mlb":
            # name matching (UFC, tennis) or MLB full names via the repo's table
            if table == "mlb":
                from sportsbot.exchanges.kalshi import KALSHI_MLB_TEAMS
                def kname(m):
                    return KALSHI_MLB_TEAMS.get(m["ticker"].rsplit("-", 1)[-1], "")
            else:
                def kname(m):
                    return m.get("yes_sub_title") or m.get("title", "").replace(" wins", "")
            pm_by: dict[tuple, dict] = {}
            for p in pms:
                d = _pm_date_et(p["start"])
                for o in p["outcomes"]:
                    pm_by[(d, _norm(o))] = p
            for ev, ms in by_event.items():
                d = kalshi_event_date(ev)
                for m in ms:
                    key = (d, _norm(kname(m)))
                    p = pm_by.get(key)
                    if p is None:
                        for dd in (-1, 1):
                            p = pm_by.get((d + timedelta(days=dd), key[1])) if d else None
                            if p:
                                break
                    if p is None:
                        continue
                    idx = 0 if _norm(p["outcomes"][0]) == key[1] else 1
                    pairs.append(PairedGame(sport, str(d), Side("kalshi", m["ticker"], kname(m), fee_rate=KALSHI_RATE * meta["fee_multiplier"], maker_share=meta["maker_share"]),
                                            Side("polymarket", p["tokens"][idx], p["outcomes"][idx], fee_rate=p["rate"]),
                                            {"event": ev, "slug": p["slug"], "vol24": p["vol24"]}))
                    break          # one market per event
        else:
            codes = set(table)
            nick_to_pm: dict[tuple, tuple] = {}
            for p in pms:
                d = _pm_date_et(p["start"])
                for i, o in enumerate(p["outcomes"]):
                    nick_to_pm[(d, _norm(o))] = (p, i)
            for ev, ms in by_event.items():
                d = kalshi_event_date(ev)
                tail = ev.rsplit("-", 1)[-1]
                sp = split_codes(tail, codes)
                if sp is None or d is None:
                    continue
                away, home = sp
                m = next((x for x in ms if x["ticker"].endswith("-" + home)), None)
                if m is None:
                    continue
                nick = table[home]
                hit = None
                for dd in (0, -1, 1):
                    hit = nick_to_pm.get((d + timedelta(days=dd), nick))
                    if hit:
                        break
                if hit is None:
                    continue
                p, idx = hit
                pairs.append(PairedGame(sport, str(d), Side("kalshi", m["ticker"], m.get("yes_sub_title") or home, fee_rate=KALSHI_RATE * meta["fee_multiplier"], maker_share=meta["maker_share"]),
                                        Side("polymarket", p["tokens"][idx], p["outcomes"][idx], fee_rate=p["rate"]),
                                        {"event": ev, "slug": p["slug"], "vol24": p["vol24"], "home": nick, "away": table.get(away)}))
    return pairs


# ------------------------------------------------------------------ books
def fill_books(http, pairs: list[PairedGame], pause: float = 0.15) -> None:
    for pg in pairs:
        try:
            d = http.get(f"{KALSHI}/markets/{pg.kalshi.market_id}/orderbook", params={"depth": 5}).json()
            ob = d.get("orderbook_fp") or d.get("orderbook") or {}
            yes = sorted(((float(p), float(s)) for p, s in (ob.get("yes_dollars") or [])), key=lambda x: -x[0])
            no = sorted(((float(p), float(s)) for p, s in (ob.get("no_dollars") or [])), key=lambda x: -x[0])
            if yes:
                pg.kalshi.bid, pg.kalshi.bid_size = yes[0]
            if no:
                pg.kalshi.ask, pg.kalshi.ask_size = round(1 - no[0][0], 4), no[0][1]
        except Exception as exc:  # noqa: BLE001
            log.debug("kalshi book %s: %s", pg.kalshi.market_id, exc)
        try:
            b = http.get(f"{CLOB}/book", params={"token_id": pg.polymarket.market_id}).json()
            bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), key=lambda x: -x[0])
            asks = sorted(((float(x["price"]), float(x["size"])) for x in b.get("asks", [])), key=lambda x: x[0])
            if bids:
                pg.polymarket.bid, pg.polymarket.bid_size = bids[0]
            if asks:
                pg.polymarket.ask, pg.polymarket.ask_size = asks[0]
        except Exception as exc:  # noqa: BLE001
            log.debug("pm book %s: %s", pg.polymarket.market_id, exc)
        time.sleep(pause)


# ------------------------------------------------------------------ report
def summarise(pairs: list[PairedGame]) -> dict:
    out: dict = {}
    by: dict[str, list[PairedGame]] = defaultdict(list)
    for pg in pairs:
        if None not in (pg.kalshi.bid, pg.kalshi.ask, pg.polymarket.bid, pg.polymarket.ask):
            by[pg.sport].append(pg)
    for sport, ps in by.items():
        k_spread = [p.kalshi.ask - p.kalshi.bid for p in ps]
        p_spread = [p.polymarket.ask - p.polymarket.bid for p in ps]
        k_mid = [(p.kalshi.ask + p.kalshi.bid) / 2 for p in ps]
        p_mid = [(p.polymarket.ask + p.polymarket.bid) / 2 for p in ps]
        gap = [k - m for k, m in zip(k_mid, p_mid)]
        k_fee = [_taker_fee(m, p.kalshi.fee_rate) for m, p in zip(k_mid, ps)]
        p_fee = [_taker_fee(m, p.polymarket.fee_rate) for m, p in zip(p_mid, ps)]
        # round-trip cost to take on each venue: spread + 2 x taker fee at mid
        k_cost = [s + 2 * f for s, f in zip(k_spread, k_fee)]
        p_cost = [s + 2 * f for s, f in zip(p_spread, p_fee)]
        boxes = []
        for p in ps:
            ka, kb, pa, pb = p.kalshi.ask, p.kalshi.bid, p.polymarket.ask, p.polymarket.bid
            c1 = ka + _taker_fee(ka, p.kalshi.fee_rate) + (1 - pb) + _taker_fee(1 - pb, p.polymarket.fee_rate)   # YES@K + NO@PM
            c2 = pa + _taker_fee(pa, p.polymarket.fee_rate) + (1 - kb) + _taker_fee(1 - kb, p.kalshi.fee_rate)   # YES@PM + NO@K
            boxes.append(1 - min(c1, c2))
        out[sport] = {
            "pairs": len(ps),
            "k_spread_med": statistics.median(k_spread), "p_spread_med": statistics.median(p_spread),
            "k_fee_rate": ps[0].kalshi.fee_rate, "p_fee_rate": statistics.median(p.polymarket.fee_rate for p in ps),
            "k_maker_share": ps[0].kalshi.maker_share,
            "k_taker_fee_mid": statistics.median(k_fee), "p_taker_fee_mid": statistics.median(p_fee),
            "k_roundtrip_med": statistics.median(k_cost), "p_roundtrip_med": statistics.median(p_cost),
            "gap_mean": statistics.mean(gap), "gap_sd": statistics.pstdev(gap) if len(gap) > 1 else 0.0,
            "gap_abs_max": max(abs(g) for g in gap),
            "gap_over_2pts": sum(1 for g in gap if abs(g) > 0.02) / len(gap),
            "box_positive": sum(1 for b in boxes if b > 0), "box_best": max(boxes),
            "k_depth_med": statistics.median(min(p.kalshi.bid_size, p.kalshi.ask_size) for p in ps),
            "p_depth_med": statistics.median(min(p.polymarket.bid_size, p.polymarket.ask_size) for p in ps),
            "pm_vol24_med": statistics.median(p.extra.get("vol24", 0.0) for p in ps),
        }
    return out


def format_summary(s: dict) -> str:
    lines = [f"{'sport':>7} {'pairs':>5} | {'K spread':>8} {'PM spread':>9} | {'K fee@mid':>9} {'PM fee@mid':>10} | "
             f"{'K rt cost':>9} {'PM rt cost':>10} | {'gap sd':>6} {'|gap|>2':>7} {'max':>6} | {'box>0':>5} {'best':>6} | {'K depth':>7} {'PM depth':>8}"]
    for sport, r in s.items():
        lines.append(f"{sport:>7} {r['pairs']:>5} | {r['k_spread_med']:>8.4f} {r['p_spread_med']:>9.4f} | "
                     f"{r['k_taker_fee_mid']:>9.4f} {r['p_taker_fee_mid']:>10.4f} | {r['k_roundtrip_med']:>9.4f} {r['p_roundtrip_med']:>10.4f} | "
                     f"{r['gap_sd']:>6.4f} {r['gap_over_2pts']:>7.3f} {r['gap_abs_max']:>6.4f} | {r['box_positive']:>5} {r['box_best']:>+6.4f} | "
                     f"{r['k_depth_med']:>7.0f} {r['p_depth_med']:>8.0f}")
    return "\n".join(lines)


def run(sports=("nfl", "nba", "nhl", "ufc", "tennis", "mlb"), http=None) -> tuple[list[PairedGame], dict]:
    import httpx
    http = http or httpx.Client(timeout=40)
    pairs: list[PairedGame] = []
    for sport in sports:
        try:
            ps = pair_sport(http, sport)
        except Exception as exc:  # noqa: BLE001
            log.warning("pairing failed for %s: %s", sport, exc)
            continue
        log.info("%s: %d pairs", sport, len(ps))
        pairs.extend(ps)
    fill_books(http, pairs)
    return pairs, summarise(pairs)
