---
name: engine-health
description: Health check for the engines in this repo (polymarket-bot, kalshi-engine, arb-scanner). Use when the user asks how the engines are doing, whether signals look right, to review calibration, or to debug an engine that seems idle or broken.
---

# Engine Health

Run the standard diagnostics for each engine the user asks about (or all
three), interpret the results, and report what — if anything — needs fixing.

## Steps

1. **Tests first**: `python -m pytest tests -q` in the engine's directory. A
   red suite explains everything else; fix that before interpreting data.
2. **One dry cycle**: `python -m src.main --once --dry-run` (arb-scanner:
   `python -m src.main --once`). Healthy output shows a non-trivial market
   count after filters; zero markets means an API drift or filter bug — check
   the raw API response field names before touching thresholds (that has been
   the root cause before: fields renamed to `*_dollars`/`*_fp`).
3. **Reports**: `python -m src.report` (bot/engine). Read it as:
   - model Brier < market baseline → the models earn their keep;
   - `settled` growing over time → settlement tracking works;
   - orders all `dry_run` → still safe mode; any `blocked:` rows → the risk
     gate fired, find out why before anything else.
4. **Signal sanity**: a signal storm (dozens per cycle) usually means a stale
   feed or broken fair-value source, not sudden riches; near-zero signals with
   only the market-implied generator active is expected until price history
   accumulates.
5. **arb-scanner specifics**: `suspect_match` rows are wrong-question pairs —
   the quarantine working, not opportunities. Real candidates are the small
   (2-10%) net edges; each needs a human to confirm both questions are truly
   identical before anyone trades it.

## Output

A short status per engine: OK / needs attention, with the one most important
number (e.g. Brier vs baseline, or markets-after-filters) and the single next
action if unhealthy.
