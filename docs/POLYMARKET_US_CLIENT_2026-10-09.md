# Polymarket US client (2026-10-09)

`sportsbot/exchanges/polymarket_us.py` — the US-legal Polymarket path
(QCEX DCM). Same `ExchangeClient` interface as Kalshi, so `config/pilot_pmus.yaml`
is the Kalshi pilot's twin: identical limits, a different venue, and the two
books can be compared on real fills, markout and CLV.

## What is verified live (public reads, no key)
| Thing | How it was checked |
|---|---|
| Sports and league slugs | `GET gateway.polymarket.us/v2/sports`: tennis (atp, wta, itf…), baseball (mlb; lists the AWAY team first), table-tennis (setka*, czechligapro, wtt) |
| Event → market → side mapping | `GET /v2/sports/{sport}/events`: match-winner types `tennis_match_winner`, `table_tennis_match_winner`, `baseball_team_full_game_winner`; the `long` side is the first-listed competitor |
| Order book | `GET /v1/markets/{slug}/book`: `bids`/`offers`, `px.value` in dollars, `qty` in contracts, quoted in LONG (YES) terms (`lastPriceSample.shortPx == 1 − longPx`) |
| Settlement | `GET /v1/markets/{slug}/settlement` → `1`/`0` for the long side; 404 until settled |
| Tick / minimum | MLB tick 0.005, tennis 0.01; minimum 0.01 contracts (decimal sizes) |
| Fees | docs.polymarket.us/fees: taker Θ 0.0695, maker Θ −0.0125, banker's rounding per order; the worked examples reproduce in `tests/test_polymarket_us.py` |

## What is NOT verified (built from the API reference only)
Do these with 1 contract, on a funded account, before the first bot order.
Each is a single `sportsbot`-free Python call using the client:

1. **Short-side price terms.** The bot sends `ORDER_INTENT_BUY_SHORT` with
   the NO price (e.g. 0.41 when YES is at 0.59). Confirm with
   `POST /v1/order/preview` that the venue reads it the same way (the
   preview's `avgPx`/`price` must come back ≈ 0.41, not 0.59).
2. **Position sign.** Buy 1 short contract and read `/v1/portfolio/positions`:
   the client assumes `netPositionDecimal < 0` means a NO position.
3. **Signed path with a query string.** `GET /v1/orders/open?slugs=…` — the
   client signs the bare path (as the docs' example does). If the venue
   returns 401 on that call only, the query must be included in the signed
   string.
4. **Fill read-back.** Place a resting post-only order, cancel it, and confirm
   `GET /v1/order/{id}` reports `ORDER_STATE_CANCELED` and `cumQuantity 0`.
5. **Close-position.** With a 1-contract long, call `close_position` and
   confirm the returned `avg_price`/`fee` match the account activity.

Record the outcome of each in this file (date, result), then run
`sportsbot verify-fees --note "polymarket_us 1-contract checks done <date>"`.

## Behaviour that matters for real money
- **Maker orders are post-only.** `BetIntent.maker` → `Order.post_only` →
  `participateDontInitiate`. A maker order that would cross is rejected by
  the venue rather than filled as a taker at a price the edge math never
  approved. Kalshi gets the same via Create Order V2 `post_only`.
- **The maker rebate is not counted as edge.** Maker fee is modelled as 0;
  the −0.0125 rebate shows up in realized P&L only. Adverse selection is what
  the pilot measures; a rebate on a bad fill is still a bad fill.
- **Rejects are final.** A 429, a 400, or the venue's 5-second
  "Global Rate Limit Exceeded" stopgap all leave the order REJECTED and are
  never resubmitted. The next cycle re-evaluates from scratch.
- **Pacing.** 20 requests/s client-side against the venue's 25/s per-IP
  limit. Cancels share the budget: do not run the scanner for another
  venue from the same IP at full speed.
- **Pre-match only.** `include_live: false` drops events the venue flags
  `live`; `risk.min_minutes_before_start` applies on top.
- **Closing sells the whole position.** The venue's close-position order has
  no quantity; the client refuses when the exit side of the book is below
  `positions.min_exit_price`.

## Running it
```
pip install -e ".[polymarket_us]"        # cryptography, for Ed25519 signing
sportsbot scan -c config/pilot_pmus.yaml  # public reads only, safe anywhere
sportsbot doctor -c config/pilot_pmus.yaml
```
Keys: `POLYMARKET_US_KEY_ID` and `POLYMARKET_US_SECRET_KEY` in `.env`
(generated at polymarket.us/developer; the secret is shown once). Live needs
`mode: live` (already in the config) **and** `SPORTSBOT_LIVE=1`.

## Sources
- Authentication: https://docs.polymarket.us/api-reference/authentication
- Create order: https://docs.polymarket.us/api-reference/orders/create-order.md
- Fees: https://docs.polymarket.us/fees.md
- Rate limits: https://docs.polymarket.us/api-reference/rate-limits.md
- Official SDK (alternative to this client): https://github.com/Polymarket/polymarket-us-python
