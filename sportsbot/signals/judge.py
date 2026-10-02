"""Typed LLM judgement as a SHADOW signal. DATA ONLY — never wired into trading.

The pattern taken from the @savipww "typed judge" desk, done with evidence
discipline (docs/ARTICLE_EVAL_2026-10-02.md §3 item 3):
  code fetches  -> public Kalshi market state (title, rules, quotes, close time)
  model judges  -> one structured answer: P(YES), whether recent news moved it
  code decides  -> nothing here; we only record, then score forward vs the market

Why forward-only: an LLM's training data and retrieval can contain the outcome of
past events (Hindcast, arXiv 2607.14051), so backtests on settled markets are
contaminated. Judgements are recorded before settlement and scored afterwards.

Funnel (cheapest kills first): liquidity/spread/price-band filter (free) ->
per-market cooldown bench -> daily call budget -> model call.

Requires `pip install anthropic` and ANTHROPIC_API_KEY (or another credential the
SDK resolves) in the environment / .env. The SDK is imported lazily.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx

log = logging.getLogger(__name__)

API = "https://api.elections.kalshi.com/trade-api/v2"
MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """You are a calibrated forecaster for binary prediction-market contracts.
You receive one contract's resolution rules and its current order-book quotes.
Return your own probability that the contract resolves YES.

Rules:
- The market mid is a strong prior from people with money at stake. Move away from it
  only for a concrete reason you can name (a rule detail, base rates, known facts).
- Use only information available before the timestamp given. If you do not know
  anything beyond the market, return a probability close to the mid.
- Be calibrated: across many contracts, events you call 70% should happen ~70% of the time.
- reason: one short sentence naming the specific reason, or "no edge over market"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "p_yes": {"type": "number", "description": "probability the contract resolves YES, 0 to 1"},
        "differs_from_market": {"type": "boolean",
                                "description": "true only if you have a concrete reason to disagree with the mid"},
        "reason": {"type": "string"},
    },
    "required": ["p_yes", "differs_from_market", "reason"],
    "additionalProperties": False,
}

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS judgements (
  ts INTEGER, ticker TEXT, category TEXT, close_ts INTEGER,
  bid REAL, ask REAL, mid REAL, p_yes REAL, differs INTEGER, reason TEXT,
  model TEXT, effort TEXT, input_tokens INTEGER, output_tokens INTEGER,
  state_hash TEXT, refused INTEGER);
CREATE INDEX IF NOT EXISTS j_ticker ON judgements(ticker, ts);
CREATE TABLE IF NOT EXISTS bench (ticker TEXT PRIMARY KEY, until_ts INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS results (ticker TEXT PRIMARY KEY, settlement_value REAL, ts INTEGER);
"""

# reason-specific cooldowns: a market judged recently does not need a new call soon
COOLDOWN = {"judged": 6 * 3600, "refused": 24 * 3600, "error": 3600}


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


# ---------------------------------------------------------------- funnel (pure)

def free_kill(m: dict, now: int, max_spread: float = 0.10, band=(0.05, 0.95),
              min_volume: float = 100.0, min_hours: float = 2.0) -> Optional[str]:
    """Return the name of the check that rejects this market, or None."""
    try:
        bid, ask = float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 1)
    except (TypeError, ValueError):
        return "unparseable_quote"
    if not (0 < bid < ask < 1):
        return "one_sided"
    if ask - bid > max_spread:
        return "wide_spread"
    mid = (bid + ask) / 2
    if not band[0] <= mid <= band[1]:
        return "price_band"
    if float(m.get("volume_fp") or 0) < min_volume:
        return "low_volume"
    close = m.get("close_time")
    if not close or _ts(close) - now < min_hours * 3600:
        return "closing_soon"
    return None


def build_state(m: dict, category: str, now: int) -> dict:
    bid, ask = float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"])
    return {
        "as_of_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="minutes"),
        "category": category,
        "title": m.get("title"),
        "yes_means": m.get("yes_sub_title"),
        "rules": (m.get("rules_primary") or "")[:1500],
        "closes_utc": m.get("close_time"),
        "best_bid": bid, "best_ask": ask, "mid": round((bid + ask) / 2, 4),
    }


def parse_answer(text: str) -> tuple[float, bool, str]:
    d = json.loads(text)
    p = float(d["p_yes"])
    if not math.isfinite(p):
        raise ValueError("non-finite probability")
    return min(1.0, max(0.0, p)), bool(d["differs_from_market"]), str(d["reason"])[:300]


# ---------------------------------------------------------------- model call

def make_judge(effort: str = "high") -> Callable[[dict], dict]:
    import anthropic  # lazy: the rest of the package runs without it

    client = anthropic.Anthropic()

    def judge(state: dict) -> dict:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",          # re-run on a fallback model if declined
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(state, sort_keys=True)}],
        )
        out = {"model": resp.model, "input_tokens": resp.usage.input_tokens,
               "output_tokens": resp.usage.output_tokens, "refused": resp.stop_reason == "refusal"}
        if not out["refused"]:
            out["text"] = next(b.text for b in resp.content if b.type == "text")
        return out

    return judge


# ---------------------------------------------------------------- recorder

class JudgeRecorder:
    def __init__(self, db_path: str, judge: Callable[[dict], dict], effort: str = "high",
                 max_calls_per_day: int = 100, client: Optional[httpx.Client] = None):
        self.db = sqlite3.connect(db_path)
        self.db.executescript(DB_SCHEMA)
        self.judge, self.effort, self.max_calls = judge, effort, max_calls_per_day
        self.http = client or httpx.Client(timeout=30, headers={"User-Agent": "sportsbot-judge"})
        self.rejections: dict[str, int] = {}

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        r = self.http.get(API + path, params=params)
        r.raise_for_status()
        return r.json()

    def calls_today(self, now: int) -> int:
        day0 = now - now % 86400
        return self.db.execute("SELECT COUNT(*) FROM judgements WHERE ts>=?", (day0,)).fetchone()[0]

    def benched(self, ticker: str, now: int) -> bool:
        row = self.db.execute("SELECT until_ts FROM bench WHERE ticker=?", (ticker,)).fetchone()
        return bool(row and row[0] > now)

    def bench(self, ticker: str, now: int, reason: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO bench VALUES (?,?,?)", (ticker, now + COOLDOWN[reason], reason))

    def candidates(self, categories: set[str], max_events: int = 2000) -> list[tuple[str, dict]]:
        out, cur, seen = [], None, 0
        while seen < max_events:
            p = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cur:
                p["cursor"] = cur
            d = self._get("/events", p)
            evs = d.get("events", [])
            seen += len(evs)
            for ev in evs:
                if ev.get("category") in categories:
                    out += [(ev["category"], m) for m in ev.get("markets") or []]
            cur = d.get("cursor")
            if not cur or not evs:
                break
        return out

    def cycle(self, categories: set[str], now: Optional[int] = None) -> int:
        now = now or int(time.time())
        made = 0
        for cat, m in self.candidates(categories):
            if self.calls_today(now) >= self.max_calls:
                self.rejections["daily_budget"] = self.rejections.get("daily_budget", 0) + 1
                break
            why = free_kill(m, now) or ("benched" if self.benched(m["ticker"], now) else None)
            if why:
                self.rejections[why] = self.rejections.get(why, 0) + 1
                continue
            state = build_state(m, cat, now)
            h = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]
            try:
                out = self.judge(state)
            except Exception:  # noqa: BLE001 — a failed call benches the market, never crashes
                log.warning("judge: call failed for %s", m["ticker"], exc_info=True)
                self.bench(m["ticker"], now, "error")
                continue
            p = differs = reason = None
            if not out.get("refused"):
                try:
                    p, differs, reason = parse_answer(out["text"])
                except (ValueError, KeyError, json.JSONDecodeError):
                    self.bench(m["ticker"], now, "error")
                    continue
            self.db.execute(
                "INSERT INTO judgements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, m["ticker"], cat, _ts(m["close_time"]), state["best_bid"], state["best_ask"],
                 state["mid"], p, None if differs is None else int(differs), reason, out.get("model"),
                 self.effort, out.get("input_tokens"), out.get("output_tokens"), h, int(bool(out.get("refused")))))
            self.bench(m["ticker"], now, "refused" if out.get("refused") else "judged")
            made += 1
        self.db.commit()
        log.info("judge: %d calls; rejections %s", made, self.rejections)
        return made

    def pull_results(self) -> int:
        todo = [r[0] for r in self.db.execute(
            "SELECT DISTINCT j.ticker FROM judgements j LEFT JOIN results r ON r.ticker=j.ticker "
            "WHERE r.ticker IS NULL AND j.close_ts < ?", (int(time.time()),))]
        n = 0
        for t in todo:
            mk = self._get(f"/markets/{t}").get("market") or {}
            if mk.get("settlement_value_dollars") is not None and mk.get("status") in ("settled", "finalized"):
                self.db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?)",
                                (t, float(mk["settlement_value_dollars"]), int(time.time())))
                n += 1
        self.db.commit()
        return n


def evaluate(db: sqlite3.Connection, weight: float = 0.3) -> dict:
    """Brier of market mid vs judge vs blend (w*judge + (1-w)*mid), first
    judgement per settled market. Pass bar (pre-registered in the eval doc):
    blend Brier < market Brier and positive CLV over >= 300 settled markets."""
    rows = db.execute(
        "SELECT j.mid, j.p_yes, r.settlement_value FROM judgements j JOIN results r ON r.ticker=j.ticker "
        "WHERE j.p_yes IS NOT NULL AND j.ts = (SELECT MIN(ts) FROM judgements WHERE ticker=j.ticker)").fetchall()
    n = len(rows)
    if not n:
        return {"n": 0}
    b = lambda f: sum((f(m, p) - y) ** 2 for m, p, y in rows) / n  # noqa: E731
    return {"n": n, "brier_market": b(lambda m, p: m), "brier_judge": b(lambda m, p: p),
            "brier_blend": b(lambda m, p: weight * p + (1 - weight) * m)}


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Shadow LLM judgement recorder (data only)")
    ap.add_argument("--db", default="data/judge.sqlite")
    ap.add_argument("--categories", default="Entertainment,Mentions,Economics,Politics")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--max-calls-per-day", type=int, default=100)
    ap.add_argument("--interval", type=int, default=3600)
    ap.add_argument("--evaluate", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if a.evaluate:
        rec = JudgeRecorder(a.db, judge=lambda s: {}, effort=a.effort)
        rec.pull_results()
        print(json.dumps(evaluate(rec.db), indent=2))
        return
    rec = JudgeRecorder(a.db, make_judge(a.effort), a.effort, a.max_calls_per_day)
    cats = {c.strip() for c in a.categories.split(",") if c.strip()}
    while True:
        try:
            rec.cycle(cats)
            rec.pull_results()
        except Exception:  # noqa: BLE001
            log.exception("judge: cycle failed")
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
