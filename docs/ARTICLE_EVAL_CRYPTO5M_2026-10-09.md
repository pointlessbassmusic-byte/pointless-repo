# Evaluation: "50+ Polymarket Bots, Each Making $100K+/Month" (@Dan1ro0, 2026-08-07)

Source: https://x.com/dan1ro0/status/2085766554407317704. Read in full.

## What it is
A mechanics tour of BTC "Up or Down 5m" bots: own fair value from the BTC
feed vs stale resting orders, tradable edge = fair − expected average entry
− costs − margin, "temporal arbitrage" (legging into Up + Down < $1 across
two market states), hedged directional inventory, cross-window inventory
management, near-resolution capture, and an inventory penalty
`q·λ·σ²·τ` on the working quote. The headline ($100K+/month each, 50+ bots)
has no P&L, no sample, no method; the post sells a Telegram channel.

## Already tested here
`docs/ARTICLES_CRYPTO_2026-10-05.md` pre-registered and ran the same family
on Polymarket's 15-minute markets with real tapes: the stale-quote taker,
the legged pair, and near-resolution capture. All three lost
(−2.2/−3.3¢, −1.6¢, −$31.6 per market). The article changes nothing about
those measurements; its examples assume the bot sees the BTC move before
the resting orders are pulled, which is the latency race our lag study put
at a few seconds and lost.

## Transferable, and what was done with it
| Idea | Status |
|---|---|
| Edge net of the walked average entry, fees, slippage, margin | Already in `strategy.walk_book`; nothing to add |
| Inventory penalty on the working quote (Avellaneda–Stoikov form) | Relevant only to a quoting engine, which the research ledger says we have no edge to run yet. Noted for the maker research in `maker/`; not wired |
| Correlated-position limits (BTC/ETH/SOL as one bet) | Sports analogue already exists: per-event caps (two tickers of one game = one position). No crypto trading here |
| Hard limits: per-market cap, max unhedged inventory, daily loss, kill switch on bad data | All present (`bot/risk.py`, `config/pilot*.yaml`) |
| Partial-fill handling: wait, widen, or close the first leg | The bot does not leg pairs; single-leg orders with TTL + exit logic |

## Verdict
No new strategy to test; no change to the research ledger. Not pursuing
crypto 5-minute markets: the measured versions lose, and the article's
edge is latency the repo has already shown it does not have.
