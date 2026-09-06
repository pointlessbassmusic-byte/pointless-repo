# PROJECT: Polymarket Sports Betting Bot

_Master project file — everything about this engine lives here._

## Two generations in this directory

| | Where | Status |
|---|---|---|
| **v2 — maker-first bot** (authoritative) | [`v2/`](v2/) | Imported 2026-09-06 from the Terminus-era package (drop 1). This is the production strategy. |
| v1 — consensus scanner (experimental) | `src/` | Built fresh 2026-09-06; taker-style +EV scanner vs. de-vigged sportsbook consensus. Useful as a signal source, superseded as a strategy. |

## v2: the production bot (`v2/trading_bot.py`, 1,356 lines)

**Why maker-first:** Polymarket introduced taker fees on sports in 2026
(fee = shares × rate × p × (1−p); rate 0.03 in March → 0.05 since July). Near 50¢ a
round-trip taker scalp costs ~5% of notional before spread. Makers pay zero and get rebates.
Earlier backtests already showed taker scalping loses after spread — fees killed it dead.

**Strategies (tennis / table tennis / MLB slate):**
- `scalp` — rest a bid inside wide spreads; exit is a resting maker sell a few ticks up.
- `fade` — after a sharp drop, rest a maker bid at a discount (up-spikes covered via the NO side).
- `arb` — only taker strategy: if `YES.ask + NO.ask + fees < $1.00`, buy both; locked profit.
- Taker orders otherwise only as stop-loss insurance / timeout fallback.

**Regression loop:** `history_downloader.py` pulls ~1yr of real Polymarket data (resumable);
`analyze_history.py` builds `analysis_workbook.xlsx` (calibration, shock-reversion, momentum,
correlations, OLS) and writes `strategy_params.json`, which the bot loads to tune shock
thresholds and fade targets per sport.

**Architecture:** `PaperExecutor` (default) / `LiveExecutor` (`DRY_RUN=false` only),
`Portfolio` + `TradeRecorder`, per-sport `SportParams`, websocket book tracking.
See [`v2/README_TERMIUS.txt`](v2/README_TERMIUS.txt) for the original ops runbook —
now superseded by `deploy/` in this repo for server ops, but the strategy doc stands.

**v2 logs are also milestone-1 input** for the substrate engine's real-history backtest
(see [`../substrate/PROJECT.md`](../substrate/PROJECT.md)).

## v1: consensus scanner (`src/`)

Gamma discovery → The Odds API multi-book de-vigged median → fuzzy match → edge vs. CLOB ask →
quarter-Kelly → dry-run executor → SQLite. Fully working (tested against live APIs); keep as a
fair-value **signal source** — its consensus probability could feed v2's fade entries or the
substrate's baseline expert. Its taker-style execution should not be used as-is given the fee
model above.

## APIs

| Source | Base URL | Used by |
|---|---|---|
| Gamma API | `https://gamma-api.polymarket.com` | v1 discovery; v2 metadata |
| CLOB API | `https://clob.polymarket.com` | both: books/prices; orders via `py-clob-client` |
| The Odds API | `https://api.the-odds-api.com/v4` | v1 consensus feed (`ODDS_API_KEY`) |

**Jurisdiction note:** Polymarket blocks US persons from trading — confirm eligibility before
any live mode.

## Run

```bash
# v2 (production): see v2/README_TERMIUS.txt; paper mode is the default
cd v2 && pip install -r requirements.txt && python trading_bot.py

# v1 (signal scanner)
python -m src.main --once --dry-run
```

## TODO / next

- [ ] Re-tune v2 `strategy_params.json` with a fresh `history_downloader.py` run (overnight, tmux/systemd)
- [ ] Wire v1's consensus fair value into v2 as an entry filter for `fade`
- [ ] Export v2 trade/quote logs in `substrate/ingest.py` schema (substrate milestone 1)
- [ ] Unify config/secrets handling with the rest of the repo (.env)
