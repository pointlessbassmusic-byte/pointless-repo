# Exit-rule replay on real price paths — 2026-09-17

**What**: `sportsbot/backtest/exit_replay.py` replayed the production
position-management rules over **604 resolved Polymarket sports tokens**
(240 MLB, 212 table tennis, 152 tennis; 10 days of history, 5-minute
fidelity, via `polymarket-bot/v2/history_downloader.py`). Full grid in
`exit_replay_2026-09-17.json`.

**Method honesty** (baked into the tool, repeated here):

- Entries are a **no-skill proxy** (first in-band tick ≥10 min before
  game start; model term frozen at entry price). Deltas measure the exit
  layer's *insurance value* on unskilled positions, not trading edge.
- Two fill regimes: `grid` allows in-play exits at printed prices —
  **optimistic**, since in-play books jump discontinuously; `grid_prestart`
  (n=594) restricts exits to pre-start ticks — **fill-realistic**.
- The grid was pre-registered in code; every cell is reported.

## Findings (fill-realistic regime, per $1 staked)

| rule | Δ vs hold | exit rate | saves | whipsaws |
|---|---|---|---|---|
| **production: stop 0.5 + edge −0.05** | **+0.010** | 24% | 83 | 59 |
| stop 0.5 only | −0.023 | 13% | 62 | 17 |
| stop 0.3 only (deeper) | −0.003 | 9% | 49 | 6 |
| stop 0.7 only (tighter) | −0.055 | 24% | 93 | 51 |
| edge −0.05 only | +0.032 | 11% | 22 | 43 |

1. **The production combo is validated as cheap insurance**: ≈ +1% per $1
   staked in the fill-realistic regime (≈ breakeven within noise), with
   real tail protection (83 saves). It is *not* a profit engine and was
   never meant to be — the point is that the protection is roughly free.
2. **Pure stops cost their premium, as theory says** (−0.3% to −5.5%).
   Tighter stops cost more: 0.7 whipsaws 3× as often as 0.5 for little
   extra saving. A **deeper stop (0.3) is near-free insurance** (−0.3%,
   only 6 whipsaws) — worth considering if live whipsaw rates annoy, but
   this is one 10-day sample; do not retune on it alone.
3. **The edge rule's big in-play numbers (+0.12 all-ticks) are mostly a
   fill artifact** — exiting "at the printed price" during a score-driven
   collapse is optimistic. Pre-start it shrinks to +0.03, consistent with
   mild pre-game momentum plus noise. Treat the all-ticks grid as an upper
   bound only.
4. **The edge rule take-profits big winners** (documented in
   `evaluate_exit`): a favorable move ≈ +22 points closes the position at
   ~0.95 on the dollar. On this sample that behavior is roughly EV-neutral
   pre-start and is kept for its capital-velocity and late-risk benefits.

**Decision**: defaults unchanged (`stop_fraction 0.5`, `exit_edge −0.05`,
`min_hold 30m`). Revisit with the bot's own settled-position data once the
server accumulates live paper exits — that sample has real fills and real
(model-tilted) entries, which this replay cannot provide.

Reproduce:

```bash
python3 polymarket-bot/v2/history_downloader.py --days 10 --max-markets 120 \
    --fidelity 5 --out /tmp/exitdata
python3 -m sportsbot.backtest.exit_replay --data-dir /tmp/exitdata \
    --out report.json
```
