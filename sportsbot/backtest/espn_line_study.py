"""Does the US sportsbook line lead Polymarket on US sports?  READ-ONLY.

The repo measured this on soccer and the answer was no
(docs/SPORTSBOOK_VS_POLYMARKET_2026-09-23.md: Polymarket an hour before
kickoff matched Bet365/Betfair/the market average and beat Pinnacle's
close). CLAUDE.md leaves US sports open: "measure with the same script
before assuming". ESPN publishes DraftKings' opening and CLOSING moneyline
for every finished NFL, NBA, NHL and MLB game, keyless
(`sports.core.api.espn.com/.../competitions/{id}/odds`), so the question
can be answered without an odds-API key.

PRE-REGISTRATION (written 2026-10-20, committed before the first fetch):

* universe: resolved Polymarket moneylines (Gamma tags football, basketball,
  hockey, baseball; slugs nfl-/nba-/nhl-/mlb-) whose gameStartTime falls
  in the last `days`, matched to an ESPN event by both team nicknames and
  start within 6 h; the line is the first ESPN odds provider's (DraftKings)
  `close` moneyline for both teams, de-vigged proportionally;
* Polymarket price: minute trade prints (`prices-history`, fidelity 1) of
  outcomes[0]'s token; "at start" = the last print in the 15 minutes before
  gameStartTime; "T-15" = the last print at or before start - 15 min (and
  within 60 min of it); prints outside 0.03-0.97 are refused;
* (A) information, one row per game: Brier of the book close and of
  Polymarket at start, their paired difference (game-level t), and the
  regression of (outcome - pm) on (book - pm);
* (B) the trade: at T-15, buy Polymarket's side toward the book when the
  de-vigged book probability exceeds Polymarket's price on that side by
  >= `margin` (0.02); pay that price + a 0.5c half-spread + the taker fee
  (rate 0.03 NFL/NBA, 0.05 NHL/MLB, x p x (1-p)); hold to resolution.
  The book's close is stamped at kickoff, up to 15 minutes AFTER this
  decision, so (B) carries a look-ahead in the book's favour: it can KILL
  the idea, it cannot confirm it. A pass registers a forward recorder of
  live lines; it is not a trade.
* THE BAR, per sport and pooled: the book is a leading reference only if
  (A) book Brier < Polymarket Brier with t <= -2 AND (B) >= 100 trades with
  mean net > 0 and t >= 2. Anything else: not a member (eighth measured).

Run: `sportsbot espn-lines --days 400` (resumable cache under
data/cache/espn_lines/), then `sportsbot espn-lines --report`.
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
import time
from datetime import timedelta
from typing import Optional

log = logging.getLogger(__name__)

CORE = "https://sports.core.api.espn.com/v2/sports"
SITE = "https://site.api.espn.com/apis/site/v2/sports"
CLOB = "https://clob.polymarket.com"
CACHE = os.path.join("data", "cache", "espn_lines")
# our sport key -> (Gamma tag key, slug prefix, ESPN path, Polymarket taker fee rate)
SPORTS = {
    "nfl": ("football", "nfl-", "football/nfl", 0.03),
    "nba": ("basketball", "nba-", "basketball/nba", 0.03),
    "nhl": ("hockey", "nhl-", "hockey/nhl", 0.05),
    "mlb": ("baseball", "mlb-", "baseball/mlb", 0.05),
}
RAIL = 0.03
HALF_SPREAD = 0.005
MARGIN = 0.02


def american_to_prob(a) -> Optional[float]:
    try:
        a = float(str(a).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def devig(p_home: Optional[float], p_away: Optional[float]) -> Optional[float]:
    """Proportional de-vig: the home probability net of the overround."""
    if p_home is None or p_away is None or p_home <= 0 or p_away <= 0:
        return None
    return p_home / (p_home + p_away)


def book_close(odds: dict) -> Optional[dict]:
    """De-vigged home probabilities (close, open) from ESPN's odds items:
    the first provider carrying both teams' close moneylines."""
    for it in odds.get("items") or []:
        h, a = it.get("homeTeamOdds") or {}, it.get("awayTeamOdds") or {}

        def ml(t, k):
            return american_to_prob(((t.get(k) or {}).get("american")) or (t.get("moneyLine") if k == "close" else None))
        close = devig(ml(h, "close"), ml(a, "close"))
        if close is None:
            continue
        return {"provider": (it.get("provider") or {}).get("name"), "close": close,
                "open": devig(ml(h, "open"), ml(a, "open"))}
    return None


def last_print(hist: list[dict], lo: float, hi: float) -> Optional[float]:
    """The last print with lo < t <= hi, refused at the rails."""
    pts = [(int(p["t"]), float(p["p"])) for p in hist if p.get("t") is not None and p.get("p") is not None]
    pts = [tp for tp in pts if lo < tp[0] <= hi]
    if not pts:
        return None
    p = max(pts)[1]
    return p if RAIL < p < 1 - RAIL else None


def fee(price: float, rate: float) -> float:
    return rate * price * (1.0 - price)


def team_names(team: dict) -> list[str]:
    """Every form ESPN gives a team's name, lower-cased: Polymarket's
    outcomes use the nickname on NFL/NBA/NHL ("rams") and the full name on
    MLB ("los angeles dodgers")."""
    # location too: Polymarket lists Utah's NHL team as "utah" (ESPN: Mammoth);
    # a match still needs BOTH teams, so a shared city cannot pair the wrong game
    forms = [team.get(k) for k in ("name", "displayName", "shortDisplayName", "nickname", "abbreviation",
                                   "location")]
    if team.get("location") and team.get("name"):
        forms.append(f"{team['location']} {team['name']}")
    return sorted({str(f).strip().lower() for f in forms if f})


def is_match(ev: dict, home: str, away: str) -> Optional[bool]:
    """None when the ESPN event is not this game; else True when ESPN's home
    team is Polymarket's outcomes[0] (no flip needed), False when reversed."""
    if home in ev["home"] and away in ev["away"]:
        return True
    if home in ev["away"] and away in ev["home"]:
        return False
    return None


# ------------------------------------------------------------------ fetch
class Fetcher:
    def __init__(self, http=None, pause: float = 0.12, cache: str = CACHE, sleep=time.sleep):
        import httpx

        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        self.pause, self.cache, self.sleep = pause, cache, sleep
        os.makedirs(cache, exist_ok=True)

    def _get(self, url, params=None):
        for wait in (1.0, 2.0, 4.0, None):
            r = self.http.get(url, params=params)
            if r.status_code == 429 and wait is not None:
                self.sleep(wait)
                continue
            r.raise_for_status()
            self.sleep(self.pause)
            return r.json()

    def scoreboard(self, path: str, day) -> list[dict]:
        data = self._get(f"{SITE}/{path}/scoreboard", {"dates": day.strftime("%Y%m%d"), "limit": 100}) or {}
        out = []
        for e in data.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            teams = {c.get("homeAway"): team_names(c.get("team") or {}) for c in comp.get("competitors", [])}
            if teams.get("home") and teams.get("away"):
                from datetime import datetime
                ts = datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp()
                out.append({"id": e["id"], "ts": ts, "home": teams["home"], "away": teams["away"]})
        return out

    def odds(self, path: str, event_id: str) -> dict:
        league = path.split("/")
        return self._get(f"{CORE}/{league[0]}/leagues/{league[1]}/events/{event_id}/competitions/{event_id}/odds") or {}

    def history(self, token: str, start: int, end: int) -> list[dict]:
        data = self._get(f"{CLOB}/prices-history", {"market": token, "startTs": start, "endTs": end,
                                                    "fidelity": 1}) or {}
        return data.get("history") or []


def collect(sport: str, days: int = 400, fetcher: Optional[Fetcher] = None, max_games: int = 0) -> str:
    """One JSON line per matched game into <cache>/<sport>.jsonl (resumable)."""
    from sportsbot.backtest.polymarket_market import fetch_resolved

    tag, prefix, path, _ = SPORTS[sport]
    f = fetcher or Fetcher()
    out_path = os.path.join(f.cache, f"{sport}.jsonl")
    done = set()
    if os.path.exists(out_path):
        with open(out_path) as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["token"])
                except (ValueError, KeyError):
                    continue
    games = [g for g in fetch_resolved(tag, days=days) if g.slug.startswith(prefix)]
    if max_games:
        games = games[-max_games:]
    log.info("%s: %d resolved Polymarket games, %d already cached", sport, len(games), len(done))
    boards: dict[str, list[dict]] = {}
    with open(out_path, "a") as fh:
        for i, g in enumerate(games):
            if g.token in done:
                continue
            row = {"token": g.token, "slug": g.slug, "start": g.start_ts, "home": g.home, "away": g.away,
                   "home_won": g.home_won}
            evs = []
            for dd in (-1, 0, 1):
                day = g.date + timedelta(days=dd)
                k = day.strftime("%Y%m%d")
                if k not in boards:
                    try:
                        boards[k] = f.scoreboard(path, day)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("scoreboard %s %s: %s", sport, k, exc)
                        boards[k] = []
                evs += boards[k]
            cands = [e for e in evs if is_match(e, g.home, g.away) is not None
                     and abs(e["ts"] - g.start_ts) <= 6 * 3600]
            ev = min(cands, key=lambda e: abs(e["ts"] - g.start_ts)) if cands else None
            if ev is None:
                row["skip"] = "no espn match"
            else:
                try:
                    bk = book_close(f.odds(path, ev["id"]))
                    hist = f.history(g.token, g.start_ts - 3 * 3600, g.start_ts + 60)
                except Exception as exc:  # noqa: BLE001 — a missing game is a missing row (retried next run)
                    log.warning("%s %s: %s", sport, g.slug, exc)
                    continue
                if bk is None:
                    row["skip"] = "no book close"
                else:
                    # the book quotes ESPN's home team; orient to outcomes[0]
                    flip = not is_match(ev, g.home, g.away)
                    row.update(provider=bk["provider"],
                               book=(1 - bk["close"]) if flip else bk["close"],
                               book_open=None if bk["open"] is None else ((1 - bk["open"]) if flip else bk["open"]),
                               pm_start=last_print(hist, g.start_ts - 15 * 60, g.start_ts),
                               pm_t15=last_print(hist, g.start_ts - 75 * 60, g.start_ts - 15 * 60))
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if i % 100 == 0:
                log.info("%s: %d/%d", sport, i + 1, len(games))
    return out_path


# ------------------------------------------------------------------ analyse
def _t(vals: list[float]) -> tuple[Optional[float], Optional[float]]:
    if len(vals) < 2:
        return (statistics.mean(vals) if vals else None), None
    m = statistics.mean(vals)
    se = statistics.stdev(vals) / math.sqrt(len(vals))
    return m, se


def analyse(rows: list[dict], fee_rate: float, margin: float = MARGIN) -> dict:
    a = [r for r in rows if r.get("book") is not None and r.get("pm_start") is not None]
    out: dict = {"games": len(rows), "matched": sum(1 for r in rows if "skip" not in r), "rows_a": len(a)}
    if a:
        bb = [(r["book"] - r["home_won"]) ** 2 for r in a]
        bp = [(r["pm_start"] - r["home_won"]) ** 2 for r in a]
        d, se = _t([x - y for x, y in zip(bb, bp)])
        out.update(brier_book=statistics.mean(bb), brier_pm=statistics.mean(bp), diff=d, diff_se=se)
        gx = [r["book"] - r["pm_start"] for r in a]
        gy = [r["home_won"] - r["pm_start"] for r in a]
        sxx = sum(x * x for x in gx)
        if sxx > 0:
            beta = sum(x * y for x, y in zip(gx, gy)) / sxx
            res = [y - beta * x for x, y in zip(gx, gy)]
            out["beta"] = beta
            out["beta_se"] = math.sqrt(sum((x * e) ** 2 for x, e in zip(gx, res))) / sxx
        out["gap_median"] = statistics.median(abs(x) for x in gx)
    trades = []
    for r in rows:
        bk, pm = r.get("book"), r.get("pm_t15")
        if bk is None or pm is None:
            continue
        for p_book, price, won in ((bk, pm, r["home_won"]), (1 - bk, 1 - pm, not r["home_won"])):
            if p_book - price >= margin:
                paid = price + HALF_SPREAD
                trades.append((1.0 if won else 0.0) - paid - fee(paid, fee_rate))
    m, se = _t(trades)
    out.update(trades=len(trades), trade_mean=m, trade_se=se)
    out["verdict"] = verdict(out)
    return out


def verdict(a: dict) -> str:
    brier_ok = a.get("diff") is not None and a.get("diff_se") and a["diff"] / a["diff_se"] <= -2
    trade_ok = (a.get("trades", 0) >= 100 and a.get("trade_mean") is not None and a.get("trade_se")
                and a["trade_mean"] > 0 and a["trade_mean"] / a["trade_se"] >= 2)
    return "LEADS (register a forward recorder)" if brier_ok and trade_ok else "not a member"


def load(sport: str, cache: str = CACHE) -> list[dict]:
    path = os.path.join(cache, f"{sport}.jsonl")
    rows, seen = [], set()
    if not os.path.exists(path):
        return rows
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r["token"] not in seen:
                seen.add(r["token"])
                rows.append(r)
    return rows


def report(sports=tuple(SPORTS), cache: str = CACHE) -> dict:
    out, pooled_rows = {}, []
    for s in sports:
        rows = load(s, cache)
        if rows:
            out[s] = analyse(rows, SPORTS[s][3])
            pooled_rows += [dict(r, _fee=SPORTS[s][3]) for r in rows]
    if pooled_rows:
        # pooled: each sport's own fee; analyse per-row by splitting trades
        pooled = analyse(pooled_rows, 0.0)
        trades = []
        for r in pooled_rows:
            bk, pm = r.get("book"), r.get("pm_t15")
            if bk is None or pm is None:
                continue
            for p_book, price, won in ((bk, pm, r["home_won"]), (1 - bk, 1 - pm, not r["home_won"])):
                if p_book - price >= MARGIN:
                    paid = price + HALF_SPREAD
                    trades.append((1.0 if won else 0.0) - paid - fee(paid, r["_fee"]))
        pooled["trades"] = len(trades)
        pooled["trade_mean"], pooled["trade_se"] = _t(trades)
        pooled["verdict"] = verdict(pooled)
        out["pooled"] = pooled
    return out


def format_report(rep: dict) -> str:
    lines = ["sportsbook close (DraftKings via ESPN) vs Polymarket, US sports",
             f"{'':7s} games  rows  Brier book  Brier PM   diff ± se          beta ± se       |gap| med | "
             "trades  net per $1 ± se   verdict"]
    for s, a in rep.items():
        if not a.get("rows_a"):
            lines.append(f"{s:7s} {a.get('games', 0):5d}  0")
            continue
        se = a.get("diff_se") or 0.0
        beta = f"{a['beta']:+.3f} ± {a['beta_se']:.3f}" if "beta" in a else "—"
        tm = "—" if a.get("trade_mean") is None else f"{a['trade_mean'] * 100:+.2f} ± {(a['trade_se'] or 0) * 100:.2f}"
        lines.append(f"{s:7s} {a['games']:5d} {a['rows_a']:5d}  {a['brier_book']:.4f}     {a['brier_pm']:.4f}  "
                     f"{a['diff']:+.4f} ± {se:.4f}   {beta:16s} {a['gap_median'] * 100:5.2f}c   | "
                     f"{a['trades']:6d}  {tm:16s}  {a['verdict']}")
    return "\n".join(lines)
