# Three X posts (2026-10-04/05): what they claim vs what the wallets show

| Post | Claim | Status |
|---|---|---|
| @0xNevsky | Grok memecoin desk, $100 → $4,216 overnight; "moonbag" exits (sell 60% at 2×, hold 40%) | Out of scope: memecoins, not prediction markets; one night, unverifiable; promotional (quoted setup article). The "free 40%" is mental accounting, because selling 60% at 2× does not change the remaining bag's expected value |
| @Dan1ro0 | A DMI/ADX trend system made +$809,704 in 214 days on Polymarket BTC Up/Down | Wallet 0xb27b…5b82: platform PnL **$487,434** (leaderboard rank 409), not $809k. Behaviour does not match the story (below) |
| @RetroValix | "mo-money": spot fair value plus dynamic hedging into complete sets, +$470,883 | Wallet 0x32ed…8ec3: platform PnL **$431,786** on **$23.66M volume (1.8%)**. Behaviour matches "complete sets" |

Both posts carry referral or affiliate links.

## Wallet behaviour (Polymarket data API, last 30,000 activities each)

| | 0xb27b… ("DMI/ADX") | mo-money |
|---|---|---|
| Window | 2026-09-24 → 25 (≈1 day) | 2026-09-30 → 10-05 |
| Trades | 27,886, **all BUY**, median $3.90 | 24,440, **all BUY**, median $6.50 |
| Prices paid (deciles) | 0.01 … 0.84, i.e. both outcomes at every price | 0.00 … 0.81, the same |
| MERGE (Up+Down → $1) | 701 merges, $313,907 | 1,334 merges, $321,177 |
| Maker / taker rebates | $2,112 / $309 | $2,382 / $725 |
| Markets | BTC Up or Down, 5–15 min | BTC Up or Down, 5–15 min |

How to read this:
- **Both wallets are two-sided liquidity providers.** They keep small bids resting
  on Up and Down and merge matched pairs into $1.
- **No sign of the DMI/ADX system.** Thousands of $4 buys of both outcomes in a
  single day, plus maker rebates, is not a trend-following taker.
- **The money comes from speed:** spread capture and rebates, which depend on
  re-quoting faster than spot moves (adverse-selection control). Those are not
  reproducible from public 1-minute data.

## Constraints for this repo
- **US access:** placing orders on Polymarket's main exchange is blocked for US
  IPs and is never circumvented here. Kalshi KXBTC15M / KXETH15M / KXSOL15M are
  the US-legal equivalent, with the same Up/Down structure and no maker fees.
- **Tests:** the testable parts (DMI/ADX entry, spot fair value, two-sided
  maker) are pre-registered in `docs/CRYPTO15_PREREG_2026-10-05.md`.
