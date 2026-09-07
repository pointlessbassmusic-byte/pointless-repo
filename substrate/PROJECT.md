# PROJECT: Substrate / Echo Engine (Predictions Engine)

_Master project file. This is the authoritative predictions engine — imported from the
claude.ai chat handoff ("the informational substrate"), drop 1, 2026-09-06._

**Read [`CLAUDE.md`](CLAUDE.md) (the original handoff) and [`PROTOCOL_v1.md`](PROTOCOL_v1.md)
before touching code.** The epistemic frame and invariants there are non-negotiable:
frozen protocol v1.0, shadow mode (nothing stakes money), seal-before-resolve,
domain exclusions, WHY module audit-only.

## What this is

A two-engine prediction stack: the **Substrate Engine** (`engine.py`) is a certification
instrument — QRNG scheduler, commit-reveal SHA-256 ledger, triple-null scoring, anytime-valid
e-process gate (certify at E≥20), Hedge fusion — and the **Echo Engine** (`wwwwwh.py`) provides
six working category models (WHO/WHAT/WHEN/WHERE/WHY/HOW). Plus the ARV double-blind workflow
(`arv.py`) and the impact harness (`impact.py`).

## Verified on this machine (2026-09-06, laptop-era repo)

All three entry points were rerun here and reproduce the handoff's validation numbers:

| Command | Result |
|---|---|
| `python3 backtest.py` | d=0.0 → cert rate 3.3% (sports) / 1.7% (weather) — nulls stay dead. d=0.2 → **100%** cert, median 442 trials (sports); 70%, median 329 (weather). Matches handoff. |
| `python3 echo_demo.py` | All six Echo models + gate integration run clean. |
| `python3 impact.py` | Consistency-layer decay detected (p=0.022) vs. null p=0.17 / flat p=0.38 — as designed. |

Deps: numpy + matplotlib only. Use `MPLBACKEND=Agg` on headless boxes.

## Relationship to the other repo projects

- **`kalshi-engine/`** is the market-access layer. Its `KalshiClient` (API v2, RSA-PSS)
  is the natural base for **milestone 2** (decision-time snapshot service, read-only,
  shadow mode). Its generic ensemble/trading loop is NOT part of certification trials.
- **`polymarket-bot/`** logs are the input for **milestone 1** (real-history backtest
  through `ingest.py`). Adapter sources: the v3 fair-value bot's `edge_log.csv`/`fv_trades.csv`
  and the history downloader's `data/markets.csv` + minute prices (all on the VPS).

## Milestones (from the handoff, in order)

1. **Real-history backtest** — **BUILT** (`bot_backtest.py`, 2026-09-07): adapter from the
   history downloader's `markets.csv` + minute prices → `ingest.py` schema, plus the
   ScoreBook + Hedge-fusion report. Smoke-tested on 30 real resolved MLB markets
   (decision time = game start − 60 min, fit-on-prior longshot discipline; the tiny-sample
   longshot expert was correctly crushed to weight 0.005 by fusion). Remaining: run at
   season scale (`history_downloader.py --days 365`, overnight on the VPS — note the
   downloader needed an `interval=max` fallback, patched, because the CLOB stopped serving
   `startTs/endTs` windows for resolved markets ~Sep 2026), and swap the placeholder
   baseline for fv_bot's devigged sharp-book probabilities when those logs land.
2. **Kalshi decision-time snapshot service** — **BUILT** (`kalshi_snapshots.py`, stdlib-only,
   read-only, GET-only; every snapshot row SHA-256-sealed at write time). Verified live:
   84 weather-daily markets across KXHIGH{NY,CHI,MIA,AUS,DEN,LAX,PHIL} snapshotted in one
   cycle. Settlement pass records outcomes; `--export` emits ingest-schema rows using the
   earliest (max-lead) sealed snapshot. Runs on the server via
   `deploy/systemd/kalshi-snapshots.{service,timer}` (every 30 min). Remaining: let it
   accumulate weeks of snapshots + settlements; add a climatology baseline.
3. **ARV session runner** — real image pool, CLI/local web UI, sealed ledger in sqlite.
4. **Live dashboard** — e-process wealth curves, fusion weights, trial counts.
5. **Post-certification only** — impact-decay sweep + promotion logic.

## Files

| File | Role |
|---|---|
| `engine.py` | Substrate certification instrument (ledger, e-process, fusion) |
| `wwwwwh.py` | Echo Engine: six category models |
| `arv.py` | ARV double-blind workflow |
| `impact.py` | Impact/consistency harness (FP 5.0%, power 93% calibrated) |
| `ingest.py` | Real-data ingestion schema (milestone 1 target) |
| `backtest.py` | Full synthetic-year validation + power study |
| `echo_demo.py` | Echo models demo + gate integration |
| `PROTOCOL_v1.md` | Frozen protocol v1.0 + parameter hash |
| `CLAUDE.md` | Original handoff document (read first) |
| `*.png` | Validation figures from the original run |
| `archive/` | Pre-v1.0 lineage (v0.1–v0.3 zips, early modules, lookback-trap demo) |
