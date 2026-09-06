#!/usr/bin/env python3
"""Download historical Polymarket sports data (run on a machine with internet).

Pulls resolved tennis / table tennis / MLB markets from the Gamma API, then
minute-level price history per outcome token from the CLOB API. Resume-safe:
re-running skips tokens already on disk.

Usage:
    python history_downloader.py --days 365 --sports tennis,table_tennis,mlb
    python history_downloader.py --days 90 --max-markets 400 --fidelity 1

Output:
    data/markets.csv           one row per outcome token (with resolution)
    data/prices/<token>.csv    ts,price
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
PRICES_URL = "https://clob.polymarket.com/prices-history"

TABLE_TENNIS_KEYWORDS = ("table tennis", "table-tennis", "setka", "wtt ", "wtt-",
                         "wtt:", "tt cup", "ttcup", "tt elite", "liga pro",
                         "ping pong", "wttms", "wttws")
TENNIS_KEYWORDS = ("tennis", "atp", "wta", "itf", "challenger", "grand slam",
                   "wimbledon", "roland garros", "us open", "australian open")
BASEBALL_KEYWORDS = ("mlb", "baseball", "american league", "national league",
                     "world series")


def classify(text: str) -> str | None:
    t = text.lower()
    if any(k in t for k in TABLE_TENNIS_KEYWORDS):
        return "table_tennis"
    if any(k in t for k in TENNIS_KEYWORDS):
        return "tennis"
    if any(k in t for k in BASEBALL_KEYWORDS):
        return "mlb"
    return None


def parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def jlist(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            out = json.loads(v)
            return out if isinstance(out, list) else []
        except json.JSONDecodeError:
            return []
    return []


def get(url, params, retries=4):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == retries - 1:
                print(f"  ! giving up on {url}: {exc}", file=sys.stderr)
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def _extract(ev, cutoff, sports, token_rows):
    added = 0
    etext = " ".join(str(ev.get(k, "")) for k in
                     ("title", "description", "slug", "ticker", "tags"))
    e_end = parse_dt(ev.get("endDate"))
    for m in ev.get("markets") or []:
        if not isinstance(m, dict):
            continue
        text = " ".join([etext, str(m.get("question", "")), str(m.get("slug", ""))])
        sport = classify(text)
        if sport not in sports:
            continue
        tokens = [str(t) for t in jlist(m.get("clobTokenIds"))]
        outcomes = jlist(m.get("outcomes"))
        prices = [float(p) if p not in (None, "") else None
                  for p in jlist(m.get("outcomePrices"))]
        m_end = parse_dt(m.get("endDate")) or e_end
        if not tokens or (m_end and m_end < cutoff):
            continue
        for i, tok in enumerate(tokens):
            if tok in token_rows:
                continue
            token_rows[tok] = {
                "token_id": tok,
                "sport": sport,
                "condition_id": m.get("conditionId") or m.get("id"),
                "slug": m.get("slug"),
                "question": m.get("question"),
                "outcome": outcomes[i] if i < len(outcomes) else None,
                "resolved_price": prices[i] if i < len(prices) else None,
                "volume": m.get("volumeNum") or m.get("volume"),
                "game_start": m.get("gameStartTime"),
                "end_date": (m_end or e_end).isoformat() if (m_end or e_end) else None,
            }
            added += 1
    return added


def discover(days: int, sports: set[str], max_markets: int,
             window_days: int = 7, legacy: bool = False):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    token_rows: dict[str, dict] = {}
    print(f"Scanning closed events back to {cutoff:%Y-%m-%d} "
          f"({'legacy offset' if legacy else f'{window_days}-day windows'}) ...")

    if legacy:
        offset, page_size = 0, 100
        while True:
            page = get(GAMMA_EVENTS_URL, {
                "closed": "true", "order": "endDate", "ascending": "false",
                "limit": page_size, "offset": offset,
            })
            if not isinstance(page, list) or not page:
                break
            oldest = None
            for ev in page:
                if isinstance(ev, dict):
                    e_end = parse_dt(ev.get("endDate"))
                    if e_end:
                        oldest = e_end if oldest is None else min(oldest, e_end)
                    _extract(ev, cutoff, sports, token_rows)
            offset += page_size
            print(f"  offset={offset} kept={len(token_rows)}", end="\r")
            if oldest and oldest < cutoff:
                break
            time.sleep(0.15)
    else:
        win_end = now + timedelta(days=1)
        empty_streak = 0
        while win_end > cutoff:
            win_start = max(cutoff, win_end - timedelta(days=window_days))
            offset, new_here = 0, 0
            while True:
                page = get(GAMMA_EVENTS_URL, {
                    "closed": "true",
                    "end_date_min": win_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_date_max": win_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "limit": 100, "offset": offset,
                })
                if not isinstance(page, list) or not page:
                    break
                for ev in page:
                    if isinstance(ev, dict):
                        new_here += _extract(ev, cutoff, sports, token_rows)
                if len(page) < 100:
                    break
                offset += 100
                time.sleep(0.1)
            print(f"  window {win_start:%Y-%m-%d}..{win_end:%Y-%m-%d} "
                  f"new={new_here} total={len(token_rows)}")
            empty_streak = empty_streak + 1 if new_here == 0 else 0
            if empty_streak >= 8 and token_rows:
                print("  (8 empty windows in a row — likely reached the start "
                      "of this sport's history on Polymarket)")
            win_end = win_start
            time.sleep(0.1)
    print()
    rows = list(token_rows.values())
    ends = [parse_dt(r["end_date"]) for r in rows if r.get("end_date")]
    if ends:
        print(f"Coverage: {min(ends):%Y-%m-%d} -> {max(ends):%Y-%m-%d} "
              f"({len(rows)} outcome tokens before ranking)")
    # keep highest-volume markets per sport, both outcome tokens of each market
    by_market = {}
    for r in rows:
        by_market.setdefault((r["sport"], r["condition_id"]), []).append(r)
    ranked = sorted(by_market.items(),
                    key=lambda kv: float(kv[1][0].get("volume") or 0), reverse=True)
    per_sport, kept = {}, []
    for (sport, _), toks in ranked:
        if per_sport.get(sport, 0) >= max_markets:
            continue
        per_sport[sport] = per_sport.get(sport, 0) + 1
        kept.extend(toks)
    print("Markets kept per sport:", per_sport)
    return kept


def download_prices(rows, out_dir: Path, fidelity: int):
    price_dir = out_dir / "prices"
    price_dir.mkdir(parents=True, exist_ok=True)
    done = skipped = empty = 0
    for i, r in enumerate(rows, 1):
        tok = r["token_id"]
        path = price_dir / f"{tok}.csv"
        if path.exists():
            skipped += 1
            continue
        end = parse_dt(r.get("end_date")) or datetime.now(timezone.utc)
        start = end - timedelta(days=3)   # sports markets live ~hours-days
        data = get(PRICES_URL, {
            "market": tok,
            "startTs": int(start.timestamp()),
            "endTs": int(end.timestamp()) + 3600,
            "fidelity": fidelity,
        })
        hist = (data or {}).get("history") if isinstance(data, dict) else None
        if not hist:
            empty += 1
            path.write_text("ts,price\n")   # marker so we don't retry forever
            continue
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ts", "price"])
            for pt in hist:
                t, p = pt.get("t"), pt.get("p")
                if t is not None and p is not None:
                    w.writerow([t, p])
        done += 1
        if i % 25 == 0:
            print(f"  prices {i}/{len(rows)} (new={done} cached={skipped} empty={empty})",
                  end="\r")
        time.sleep(0.12)
    print(f"\nPrice files: new={done} cached={skipped} empty={empty}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--sports", default="tennis,table_tennis,mlb")
    ap.add_argument("--max-markets", type=int, default=600,
                    help="highest-volume markets kept per sport")
    ap.add_argument("--fidelity", type=int, default=1, help="minutes per sample")
    ap.add_argument("--window-days", type=int, default=7,
                    help="event scan window size")
    ap.add_argument("--legacy-scan", action="store_true",
                    help="old deep-offset scan (fallback if windows return nothing)")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sports = {s.strip() for s in args.sports.split(",") if s.strip()}

    rows = discover(args.days, sports, args.max_markets,
                    window_days=args.window_days, legacy=args.legacy_scan)
    if not rows:
        print("No markets found — check connectivity / keywords.")
        return
    mpath = out_dir / "markets.csv"
    with mpath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {mpath} ({len(rows)} outcome tokens)")
    download_prices(rows, out_dir, args.fidelity)
    print("Done. Next: python analyze_history.py")


if __name__ == "__main__":
    main()
