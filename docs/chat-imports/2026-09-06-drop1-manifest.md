# Drop 1 — 2026-09-06 — Substrate handoff + Polymarket bot v2

- Date: 2026-09-06
- Engine: both
- Key decisions: substrate = authoritative predictions engine (shadow mode, PROTOCOL v1.0 frozen);
  Polymarket v2 = maker-first (taker fees made scalping -EV)

## What was uploaded (5 files)

| Upload | Contents | Landed in repo at |
|---|---|---|
| `CLAUDE.md` | Substrate handoff doc (invariants, milestones) | `substrate/CLAUDE.md` |
| `substrate_engine_handoff.zip` | v1.0 code: engine, wwwwwh, arv, impact, backtest, echo_demo, ingest + PROTOCOL_v1.md, README, validation PNGs | `substrate/` |
| `files_2.zip` | PROTOCOL_v1.md, validation PNGs, version zips v0.1–v1.0 | v0.1–v0.3 zips → `substrate/archive/` (v1.0 = handoff, dupes dropped) |
| `files_3.zip` | `lookback_trap.py`, `parallel_engines.py` (v0.1-era), `wwwwwh_models.py` (v0.2-era) | `substrate/archive/` |
| `files_1.zip` | Polymarket_Sports_Bot_v2.zip + README_TERMIUS + history_downloader | `polymarket-bot/v2/` |

## Verification performed on import

- `substrate/backtest.py`, `echo_demo.py`, `impact.py` all rerun on this machine and
  reproduce the handoff's validation numbers (see `substrate/PROJECT.md`).
- `polymarket-bot/v2/*.py` compile clean; not executed (needs history download + params).
- Duplicates checked: handoff == substrate_engine_v1.0.zip; the two history_downloader.py
  copies were identical.

_More drops expected — append manifests here as they land._
