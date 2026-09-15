# Consolidated engine — first live validation pass (2026-09-15)

Run from a Claude Code web session on the post-merge tree
(branch `claude/sports-betting-predictions-models-p1d4iv`, merge commit f906440).
All read-only / paper-safe operations; no orders placed.

## Static checks

- `pytest -q`: **71/71 pass**
- `ruff check sportsbot tests`: clean
- `substrate/arv_cli.py --self-test`, `maker/l2_replay.py --self-test`: pass

## Live pipeline

| step | result |
|---|---|
| `sportsbot fit baseball` | 9,690 games from MLB Stats API → 31 team ratings |
| `sportsbot fit table_tennis` | 1,988 resolved Polymarket matches → 473 player ratings |
| `sportsbot fit tennis` | **blocked by the sandbox**, not the code: third-party `raw.githubusercontent.com` is scoped out of web sessions, so Sackmann CSVs 404 → 0 matches. Works on laptop/VPS. |
| `sportsbot scan` | live Setka Cup slate rendered, model vs market, **zero intents** at sub-threshold edges — TT's stricter `min_edge_override` behaving as designed |
| `sportsbot backtest table_tennis` | n=414: log loss **0.6779** (coin 0.6931), Brier 0.2424, 56.0% acc; calibration bins monotone |
| `sportsbot backtest baseball` | n=11,661: log loss **0.6809** (always-home 0.689; good-model band 0.66–0.68), Brier 0.2440, 55.7% acc; well-calibrated bins (e.g. 0.6–0.7 pred 0.634 vs obs 0.637) |

## Reading

- The engine's honesty holds up: numbers land where the README's "honest
  expectations" section says they should — modest real skill, no miracle.
  MLB sits just above the good-model band; the next log-loss basis points live
  in the starting-pitcher overlay and early-line timing, not in threshold tuning.
- Table tennis beats the coin on 414 walk-forward matches but the slate scans
  near-efficient intraday; patience + the deliberately high TT threshold is
  correct.
- Nothing here changes the go-live gate (200+ paper bets, positive mean CLV,
  Brier < 0.25, per `deploy/DEPLOYMENT.md`). Next operational step is simply
  running the paper loop to start accruing CLV.
