# Polymarket bot — generation archive

Superseded builds preserved for provenance and code reuse. The current strategy lineage is in
[`../PROJECT.md`](../PROJECT.md). Nothing here should run with capital.

## `optimal-jul2026/` — the "Optimal Build" (Jul 17, 2026)

The generation that produced the empirical verdicts everything since is built on
(full text in `CLAUDE_CODE_HANDOFF.md`):

- **Momentum-taker falsified** on 268,026 real minute-level price points: all 72 configs
  negative even at zero spread. Minute-scale jumps revert; buying them buys local tops.
- **Mean reversion exists but < trading costs** at realistic spreads.
- **The harvestable edge belongs to makers** — but touch-joining alone found no edge either
  (tennis books quoted tight; where spread existed, nothing crossed).
- **Data honesty:** `Polymarket_Historical_Data_30Days.xlsx` and `Simulated_Matches_Future.xlsx`
  were synthetic (45.9% of rows had ask < bid) — nothing calibrated from them counts. Earlier
  V2 dashboards overstated performance (BOOTSTRAP_TRADES + zero fees).

Files: `optimal_trading_bot.py` (consolidated bot + tick recorder), `replay_harness.py`
(48-combo sweep vs real ticks, fit/holdout), `fetch_history.py`, `allocation_advisor.py`,
`paper_quoter.py` (live maker-viability measurement, $0 at risk — the July "quoter" tmux
session). Deploy target was `/root/sports-bot-optimal`, tmux `optimalbot`.

## `dashboard-aug2026/` — dashboard-safe paper trader (Aug 14, 2026)

Tennis-only pre-match H2H paper trader against **SharpOracle external fair value** (fuzzy
match ≥0.92 + ambiguity margin), taker fees modeled, multiplier exits, rolling
negative-performance gate. No live-order code. This is the direct precursor of the
fair-value thesis that became `fv_bot.py`/`edge_model.py`. Deploy script prompts for a
**rotated** SharpOracle key (an earlier key leaked in chat/source and must not be reused);
no secrets are embedded in these files.

## `paste-era/` — Termius heredoc installers

The phone-era delivery mechanism: `PASTE_ALL_IN_ONE_v2.sh` (truncation-proofed base64
bundle installer for the optimal build — bundle contents extracted to `optimal-jul2026/`),
`PAPER_QUOTER_PASTE.sh` (same for `paper_quoter.py`), `install_optimal.sh` (idempotent
installer), `apply_tennis_final_dryrun.sh` (patches the `/root/sports-bot` V2-lineage
`trading_bot.py` to tennis-only dry run with entry-price bands).
