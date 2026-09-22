# cryptoarb — arbitrage scanner + paper desk

A $100 paper-money arbitrage desk running against **live** books on
Coinbase, Kraken, KuCoin and Polymarket. It prices every candidate edge net
of fees and executes only what clears them.

## What it measured on day one (2026-09-22, live)

| Strategy | Best net edge | Verdict |
|---|---|---|
| Polymarket bundle (YES+NO < $1) | **−10 bps** | closest to viable — missed by $0.001/pair |
| KuCoin triangular (3×10 bps hurdle) | −22 bps | 0 of 16 cycles cleared |
| Cross-exchange spot | −49 bps | gross dislocation 0–3 bps vs ~50 bps fees |

Cross-exchange spot arbitrage **does not exist at retail fee tiers**: the
fee wall is ~15× the price dislocation. That is not a bug in the scanner,
it is the finding — and it matches this repo's own July falsification
record (`polymarket-bot/archive/`).

## Why the dashboard leads with "distance to breakeven"

An equity curve with no trades is a flat line that teaches nothing. The
headline chart is each strategy's best net edge per cycle against a
breakeven rule, so you can see how far the market is from paying, and
whether your fee tier is what stands in the way.

## Honesty rules baked in (and tested)

- **No fill on a negative edge** — ever. `tests/test_cryptoarb.py` pins it.
- **Fees always charged**; an unknown venue prices at the WORST known rate,
  never free. Defaults are base retail tiers, the worst case.
- **Size capped by observed depth** and by cash — no assumed liquidity.
- **No seeded/bootstrap trades.** The archive's warning about dashboards
  that "overstated performance (BOOTSTRAP_TRADES + zero fees)" is the exact
  failure this design exists to avoid.
- **Losses only shrink allocation** (never a martingale), consistent with
  the root `CLAUDE.md` rule.

## Allocation of the $100

Caps come from a **structural risk prior**, then shrink with measured
losses and never grow past the prior:

| Strategy | Cap | Why that rank |
|---|---|---|
| bundle | $50 | both legs one venue, settles at exactly $1, no direction |
| triangular | $30 | one venue, no transfers, but 3 legs of execution risk |
| cross_exchange | $20 | needs funded inventory on TWO venues + USD/USDT depeg risk |

Capital is committed only when an edge clears costs, so 100% uncommitted
cash is the correct state while nothing does.

## Run it

```bash
cd cryptoarb
python3 engine.py --once                      # one cycle + dashboard
python3 engine.py --interval 60 --out data/dashboard.html   # continuous
```

View `data/dashboard.html` (SSH-tunnel it like the substrate dashboard —
do not open a web port).

## Going real

The SIM/REAL toggle is wired; the trigger is not. `engine.live_gate()`
requires ALL of: `mode: real`, `CRYPTOARB_LIVE=1`, venue API keys, and a
live executor that **is deliberately not implemented**. The browser can
never arm it. When you are ready, send keys via `.env` only (gitignored) —
never in config, chat, or logs — and the first thing to change is
`fees.py`: your real tier decides whether any of this is profitable.
