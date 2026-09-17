#!/usr/bin/env python3
"""
Pull REAL Polymarket price history for recently closed Tennis/Baseball markets.

Run this on the VPS (needs internet), not locally:
    /root/sports-bot-optimal/.venv/bin/python fetch_history.py

Output: real_history_30d.csv — one row per (market, minute):
    timestamp_utc, sport, slug, question, token_id, price

Data source: Polymarket CLOB timeseries endpoint (prices-history) +
Gamma events API for market discovery. This is actual traded market data.

Honest limitations of this dataset (do not paper over them):
- prices-history returns a PRICE series (midpoint), not bid/ask. Historical
  spreads are NOT recoverable from this endpoint. Any replay against this data
  must add a spread assumption — parameter sweeps that are sensitive to spread
  should use the bot's own real_ticks.csv (which records true bid/ask) instead.
- Fidelity is minutes, not ticks. Fast in-play moves are smoothed.
Use this file for regime-level calibration (typical move sizes, volatility by
sport); use real_ticks.csv for execution-level replay.
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
PRICES_HISTORY_URL = "https://clob.polymarket.com/prices-history"

DAYS_BACK = 30
FIDELITY_MINUTES = 1
MAX_EVENTS_TO_SCAN = 3000
MAX_MARKETS = 400
OUTPUT = "real_history_30d.csv"

TENNIS_TERMS = ("tennis", " atp ", " wta ", "challenger", " itf ", "wimbledon")
BASEBALL_TERMS = ("baseball", " mlb ", "world series", "kbo", "npb")


def classify(text: str) -> str | None:
    t = f" {text.lower()} "
    if any(k in t for k in TENNIS_TERMS):
        return "Tennis"
    if any(k in t for k in BASEBALL_TERMS):
        return "Baseball"
    return None


def parse_token_ids(raw) -> list[str]:
    import json
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            return [str(x) for x in json.loads(raw)]
        except Exception:
            return []
    return []


def discover_closed_sports_markets(session: requests.Session) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    found: list[dict] = []
    offset = 0
    while offset < MAX_EVENTS_TO_SCAN and len(found) < MAX_MARKETS:
        resp = session.get(
            GAMMA_EVENTS_URL,
            params={"closed": "true", "limit": 100, "offset": offset,
                    "order": "endDate", "ascending": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json()
        if not events:
            break
        for event in events:
            sport = classify(str(event.get("title", "")) + " " + str(event.get("slug", "")))
            if not sport:
                continue
            end_raw = str(event.get("endDate", ""))
            try:
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if end_dt < cutoff:
                # Events are end-date descending; once past cutoff we can stop.
                return found
            for market in event.get("markets", []):
                tokens = parse_token_ids(market.get("clobTokenIds"))
                if len(tokens) != 2:
                    continue
                found.append({
                    "sport": sport,
                    "slug": str(market.get("slug", "")),
                    "question": str(market.get("question", ""))[:120],
                    "token_id": tokens[0],  # one side is enough; other = 1 - p
                    "end_ts": int(end_dt.timestamp()),
                })
                if len(found) >= MAX_MARKETS:
                    return found
        offset += 100
    return found


def fetch_price_history(session: requests.Session, token_id: str,
                        start_ts: int, end_ts: int) -> list[tuple[int, float]]:
    resp = session.get(
        PRICES_HISTORY_URL,
        params={"market": token_id, "startTs": start_ts, "endTs": end_ts,
                "fidelity": FIDELITY_MINUTES},
        timeout=20,
    )
    if resp.status_code != 200:
        return []
    payload = resp.json()
    points = payload.get("history", []) if isinstance(payload, dict) else []
    out = []
    for point in points:
        t, p = point.get("t"), point.get("p")
        if t is not None and p is not None:
            out.append((int(t), float(p)))
    return out


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "sports-bot-history-fetch/1.0"})

    print(f"Discovering Tennis/Baseball markets closed in the last {DAYS_BACK} days...")
    markets = discover_closed_sports_markets(session)
    print(f"Found {len(markets)} markets. Pulling minute-level price history...")
    if not markets:
        print("No markets found — check connectivity and Gamma API response shape.")
        sys.exit(1)

    rows_written = 0
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp_utc", "sport", "slug", "question", "token_id", "price"])
        for index, market in enumerate(markets, 1):
            # Pull from 12h before market end (covers pre-game + in-play).
            start_ts = market["end_ts"] - 12 * 3600
            history = fetch_price_history(session, market["token_id"], start_ts, market["end_ts"])
            for ts, price in history:
                writer.writerow([
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    market["sport"], market["slug"], market["question"],
                    market["token_id"], f"{price:.4f}",
                ])
            rows_written += len(history)
            if index % 20 == 0:
                print(f"  {index}/{len(markets)} markets, {rows_written} rows so far")
            time.sleep(0.25)  # be polite to the API

    print(f"Done. {rows_written} real price points -> {OUTPUT}")
    if rows_written == 0:
        print("WARNING: 0 rows. The prices-history endpoint or params may have "
              "changed — verify at docs.polymarket.com before relying on this script.")


if __name__ == "__main__":
    main()
