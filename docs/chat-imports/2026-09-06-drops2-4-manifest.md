# Drops 2–4 — 2026-09-06 — Polymarket bot full generation history

- Date: 2026-09-06 (three uploads in quick succession, same session)
- Engine: polymarket-bot (+ one substrate duplicate)
- Key decisions: fair-value system (fv_bot/edge_model, Aug 19) is current production and
  supersedes the v2 websocket bot; momentum-taker and touch-joining falsified Jul 17; the
  synthetic-xlsx data-honesty findings invalidate all pre-Jul calibrations.

## Drop 2

| Upload | Contents | Landed at |
|---|---|---|
| `CLAUDE_2.md` | Fair-value bot handoff (2026-08-19): fv_bot/edge_model system on VPS `~/sports-bot-v2`, hard-won conclusions, go-live gates, 10-item task queue | `polymarket-bot/CLAUDE.md` (verbatim + provenance note); durable content folded into `polymarket-bot/PROJECT.md` |

## Drop 3

| Upload | Contents | Landed at |
|---|---|---|
| `CLAUDE_CODE_HANDOFF.md` | Optimal-build handoff (Jul 17): momentum falsified on 268k real points, maker verdict, data-honesty findings | `polymarket-bot/archive/optimal-jul2026/` |
| `optimal_trading_bot.py` | Consolidated bot (1,415 lines) — identical to the copy in drop 4's paste bundle | `polymarket-bot/archive/optimal-jul2026/` |
| `paper_quoter.py` | Live maker-viability measurement ($0 at risk; the July "quoter" tmux session) | `polymarket-bot/archive/optimal-jul2026/` |
| `install_optimal.sh`, `apply_tennis_final_dryrun.sh` | Installers / config patches | `polymarket-bot/archive/paste-era/` |

## Drop 4

| Upload | Contents | Landed at |
|---|---|---|
| `CLAUDE_2.md` (again) | Byte-identical duplicate of drop 2's | dropped |
| `CLAUDE_1.md` | Byte-identical duplicate of the substrate handoff (drop 1) | dropped |
| `Final_Dashboard_Safe_Paper_Trader_Multiplier_Exit_1.zip` | Aug 14 dashboard-safe tennis paper trader vs SharpOracle fair value | `polymarket-bot/archive/dashboard-aug2026/` |
| `PASTE_ALL_IN_ONE_v2.sh` | Truncation-proofed base64 installer; its bundle decoded to optimal_trading_bot.py + **replay_harness.py + fetch_history.py + allocation_advisor.py** (the files the optimal handoff lists but drop 3 lacked) | script → `paste-era/`; decoded modules → `optimal-jul2026/` |
| `PAPER_QUOTER_PASTE.sh` | base64 installer of the same paper_quoter.py | `polymarket-bot/archive/paste-era/` |

## Verification performed on import

- All imported Python compiles clean (`py_compile`).
- Secret scan across every file: no embedded keys (dashboard build reads its key from env;
  its deploy script demands a rotated key — the old SharpOracle key that leaked in chat must
  never be reused).
- Bundle-decoded `optimal_trading_bot.py` verified identical to the standalone upload.

## Still missing (not in any drop so far)

- **v3 code**: `fv_bot.py`, `edge_model.py`, current `analyze_history.py`/`history_downloader.py`
  and `strategy_params.json` from VPS `~/sports-bot-v2` → retrieve with
  `scripts/pull_from_server.sh` or upload as a future drop.
- From the optimal handoff's manifest: `TERMIUS_RUNBOOK.md`, `polymarket_bot_handoff_spec.md`.
- Any recorded data: `edge_log.csv`, `fv_trades.csv`, `real_ticks.csv`, `paper_fills.csv`
  (needed for substrate milestone 1 and for tuning).
