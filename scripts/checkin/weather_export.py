"""Daily NWS weather cohort: export settled rows, validate, score complete dates.

    python3 scripts/checkin/weather_export.py HIST_CSV OUT_CSV DB COMPLETE_DATES

HIST_CSV is the latest `substrate/reports/weather_nws_cohort_<date>.csv`;
OUT_CSV the new dated one; DB the weather snapshot store
(`data/weather_snapshots.sqlite`); COMPLETE_DATES a comma list of target
dates whose every ticker has settled.

Export: every settled ticker in the store, scored as of its FIRST snapshot
(market = that snapshot's mid; NWS = the earliest forecast on record at it,
sigma by lead; climatology), lead measured from the target date's UTC
midnight — the cohort's pre-registered rule, kept on purpose (re-scoring it
on station-local time is the v2 question). History rows are kept verbatim
and never backfilled; only tickers not in it are appended.

Validation: rows the store can recompute are compared with the history and
any difference printed as MISMATCH (ticker, column, history, recomputed).

Scoring, complete dates only, rows with an NWS probability: per station-day
the mean Brier difference (climatology − NWS, market − NWS; > 0 means NWS
was better / worse respectively), then per date the mean of its
station-days; each reported as n, mean, sd, se, t and count > 0.

Rebuilt 2026-11-05 after a container reset lost the uncommitted original;
on the committed 2026-11-04 cohort it reproduces that day's published
numbers exactly.
"""
from __future__ import annotations

import csv
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

FIELDS = ["ticker", "series", "target_date", "lead_days", "market", "nws", "climatology", "outcome"]
TOL = 1e-6


def export_rows(db_path: str) -> dict:
    """ticker -> cohort row for every settled ticker the store can score."""
    from sportsbot.signals.nws import prob_from_high, sigma_for_lead
    from sportsbot.substrate_bridge.climatology import Climatology, parse_market

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    climo = Climatology()
    out = {}
    for r in conn.execute("""SELECT s.ticker, s.series, s.title, MIN(s.ts) AS first_ts, o.outcome
            FROM weather_snapshots s LEFT JOIN weather_outcomes o ON o.ticker = s.ticker
            GROUP BY s.ticker""").fetchall():
        if r["outcome"] is None:
            continue
        parsed = parse_market(r["title"])
        if parsed is None:
            continue
        snap = conn.execute("SELECT yes_bid, yes_ask FROM weather_snapshots WHERE ticker=? AND ts=?",
                            (r["ticker"], r["first_ts"])).fetchone()
        if snap is None or snap["yes_bid"] is None or snap["yes_ask"] is None:
            continue
        target = parsed[0]
        first_ts = float(r["first_ts"])
        lead = (datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc).timestamp()
                - first_ts) / 86400.0
        fc = conn.execute("SELECT forecast_high FROM weather_forecasts WHERE series=? AND target_date=?"
                          " AND ts <= ? ORDER BY ts ASC LIMIT 1",
                          (r["series"], target.isoformat(), first_ts + 1.0)).fetchone()
        nws = prob_from_high(r["title"], float(fc["forecast_high"]), sigma=sigma_for_lead(lead)) if fc else None
        out[r["ticker"]] = {"ticker": r["ticker"], "series": r["series"], "target_date": target.isoformat(),
                            "lead_days": round(lead, 2), "market": (snap["yes_bid"] + snap["yes_ask"]) / 2.0,
                            "nws": nws if nws is not None else "",
                            "climatology": climo.prob(r["series"], r["title"]),
                            "outcome": int(r["outcome"])}
    return out


def date_counts(db_path: str) -> dict:
    """Date token (e.g. 26NOV05) -> (settled, total) tickers in the store."""
    conn = sqlite3.connect(db_path)
    out = {}
    for token, total, settled in conn.execute("""SELECT substr(s.ticker, instr(s.ticker, '-') + 1, 7),
            COUNT(DISTINCT s.ticker), COUNT(DISTINCT o.ticker)
            FROM weather_snapshots s LEFT JOIN weather_outcomes o
              ON o.ticker = s.ticker AND o.outcome IS NOT NULL
            GROUP BY 1 ORDER BY 1"""):
        out[token] = (settled, total)
    return out


def validate(hist: list[dict], recomputed: dict) -> tuple[int, list[tuple]]:
    checked, bad = 0, []
    for h in hist:
        rc = recomputed.get(h["ticker"])
        if rc is None:
            continue
        checked += 1
        for col in ("market", "nws", "climatology", "outcome"):
            a, b = h[col], rc[col]
            if a == "" or b == "":
                if str(a) != str(b):
                    bad.append((h["ticker"], col, a, b))
            elif abs(float(a) - float(b)) > TOL:
                bad.append((h["ticker"], col, a, b))
    return checked, bad


def summary(vals: list[float]) -> dict:
    n = len(vals)
    if n < 2:
        return {"n": n}
    m, s = statistics.mean(vals), statistics.stdev(vals)
    se = s / math.sqrt(n)
    return {"n": n, "mean": round(m, 4), "sd": round(s, 4), "se": round(se, 4),
            "t": round(m / se, 2) if se else None, "pos": sum(v > 0 for v in vals)}


def score(rows: list[dict], complete: set) -> dict:
    """Station-day and per-date Brier differences on complete dates."""
    def brier(r, col):
        return (float(r[col]) - int(r["outcome"])) ** 2

    by_sd: dict = defaultdict(list)
    for r in rows:
        if r["target_date"] in complete and r["nws"] != "":
            by_sd[(r["target_date"], r["series"])].append(r)
    out = {"station_days": len(by_sd)}
    per_date_lines = defaultdict(dict)
    for col, key in (("climatology", "clim-NWS"), ("market", "mkt-NWS")):
        sd_vals, by_date = [], defaultdict(list)
        for (d, _), rs in sorted(by_sd.items()):
            v = statistics.mean(brier(r, col) for r in rs) - statistics.mean(brier(r, "nws") for r in rs)
            sd_vals.append(v)
            by_date[d].append(v)
        out[f"station-day {key}"] = summary(sd_vals)
        out[f"per-date {key}"] = summary([statistics.mean(v) for v in by_date.values()])
        for d, v in by_date.items():
            per_date_lines[d][key] = statistics.mean(v)
            per_date_lines[d]["stations"] = len(v)
    leads = defaultdict(list)
    for (d, _), rs in by_sd.items():
        leads[d] += [float(r["lead_days"]) for r in rs]
    for d in per_date_lines:
        per_date_lines[d]["lead"] = statistics.mean(leads[d])
    out["dates"] = dict(sorted(per_date_lines.items()))
    return out


def main(hist_path: str, out_path: str, db_path: str, complete_dates: str) -> None:
    with open(hist_path) as fh:
        hist = list(csv.DictReader(fh))
    have = {r["ticker"] for r in hist}
    recomputed = export_rows(db_path) if Path(db_path).exists() else {}
    checked, bad = validate(hist, recomputed)
    for t, col, a, b in bad:
        print("MISMATCH", t, col, a, b)
    new = [r for t, r in sorted(recomputed.items()) if t not in have]
    print(f"validated {checked} overlapping tickers, {len(bad)} mismatches; new rows {len(new)}")
    rows = hist + [{k: str(r[k]) for k in FIELDS} for r in new]
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out_path} {len(rows)} rows")
    complete = {d for d in complete_dates.split(",") if d}
    counts = date_counts(db_path) if Path(db_path).exists() else {}
    print("dates in store (settled/total):",
          ", ".join(f"{t} {a}/{b}" for t, (a, b) in counts.items()) or "none")
    s = score(rows, complete)
    print("complete dates:", sorted(complete))
    for key in ("station-day clim-NWS", "station-day mkt-NWS", "per-date clim-NWS", "per-date mkt-NWS"):
        print(key, s[key])
    for d, v in s["dates"].items():
        print(f"  {d} {v['stations']} clim-NWS {v['clim-NWS']:.4f} mkt-NWS {v['mkt-NWS']:.4f} lead {v['lead']:.2f}")
    covered = sum(1 for r in rows if r["nws"] != "")
    print(f"NWS-covered rows {covered} of {len(rows)}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
