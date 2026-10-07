# Live Pilot Playbook: Moving an Automated Binary-Contract Strategy from Simulation to Small Real-Money Trading on Kalshi and Polymarket US (as of Oct 2026)

Scope note: US resident, legal venues only (Kalshi; Polymarket US, the CFTC-regulated US exchange, not the geoblocked international CLOB). Research was done 2026-10-07. Venue docs change often, so re-check the linked pages before you rely on any number.

## Q1. Account setup for API trading: KYC, funding, API keys, sandboxes, order types, rate limits, STP, cancel-on-disconnect

### Takeaway
Both venues require full KYC before API trading. Each signs every request with an asymmetric key and a millisecond timestamp. Kalshi has a public self-serve demo environment, but the demo uses mock funds and its prices are not representative. Polymarket US has two different APIs: a retail API with self-serve Ed25519 keys and a 25 req/s limit, and an institutional "exchange trader" API with Private Key JWT, a preprod environment and onboarding by email. The kill-switch-type primitives differ by venue. Kalshi has order groups, `cancel_order_on_pause`, STP modes and post-only. Polymarket US has `participateDontInitiate` (post-only), IOC/FOK/GTD/DAY time-in-force, and a 5-second stale-order stopgap.

### Cited Findings

**Kalshi: API keys and signing**
- Keys are created under the "API Keys" section of kalshi.com/account/profile. Ed25519 is the default and RSA 2048 is offered for clients that only support RSA-PSS. You can also generate a key locally with OpenSSL and paste in the public key, so the private key never leaves your machine. — [Kalshi docs: API keys](https://docs.kalshi.com/getting_started/api_keys)
- Kalshi does not store the private key, which is shown once. API alternatives: `POST /trade-api/v2/api_keys/generate` (defaults to RSA if `key_type` is omitted) and `POST /trade-api/v2/api_keys` to register your own public key. — [Kalshi docs: API keys](https://docs.kalshi.com/getting_started/api_keys)
- The headers are `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP` (ms) and `KALSHI-ACCESS-SIGNATURE`. The signed message is timestamp + HTTP method + path, with query parameters stripped. RSA uses RSA-PSS SHA-256 with MGF1-SHA256 and salt length equal to the digest length. Ed25519 signs the message directly. The signature is base64-encoded. The official Python and TypeScript SDKs accept both key types from v3.31.0. — [Kalshi docs: API keys](https://docs.kalshi.com/getting_started/api_keys)

**Kalshi: demo environment**
- Web: demo.kalshi.co. REST: `https://external-api.demo.kalshi.co/trade-api/v2`, with `demo-api.kalshi.co` also supported. WS: `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`. Credentials are not shared with production. — [Kalshi docs: demo env](https://docs.kalshi.com/getting_started/demo_env)
- The docs say "The price and behavior of markets in the demo environment may not be reflective of those in real markets." — [Kalshi docs: demo env](https://docs.kalshi.com/getting_started/demo_env)
- The demo account starts with no balance. You fund it with test debit cards, Google Pay test cards, or a Plaid sandbox (user_good / pass_good). Fake name, address and SSN are acceptable. Crypto deposits use testnets. The demo can go offline for maintenance. — [Kalshi Help: demo account](https://help.kalshi.com/en/articles/13823775-creating-and-using-a-demo-account)
- A third-party tool vendor says the demo has its own thin, separate book. This is not confirmed in Kalshi's own docs. — reported via search summary of [Kalshi demo help](https://help.kalshi.com/account/demo-account) and third-party guides such as [predictionhunt](https://www.predictionhunt.com/blog/kalshi-api-getting-started-guide)

**Kalshi: order entry**
- The V2 event-market create endpoint is `/portfolio/events/orders`, which uses single-book bid/ask and fixed-point dollar prices. The legacy `/portfolio/orders` is deprecated "no earlier than May 6, 2026". — [Kalshi docs: Create Order V2](https://docs.kalshi.com/api-reference/orders/create-order-v2.md)
- Order fields include `post_only`, `time_in_force` (the example uses `good_till_canceled`), `self_trade_prevention_type`, `cancel_order_on_pause`, `reduce_only`, `client_order_id` and `expiration_ts`/time. — [Kalshi docs: create order](https://docs.kalshi.com/api-reference/portfolio/create-order); [Create Order V2](https://docs.kalshi.com/api-reference/orders/create-order-v2.md)
- STP enum: `taker_at_cross` and `maker`. FIX reject/cancel texts include SELF_CROSS_ATTEMPT, TAKER_/MAKER_CANCEL_FOR_SELF_TRADE_PREVENTION and POST_ONLY_CROSS. — [Kalshi SDK: SelfTradePreventionType](https://docs.kalshi.com/python-sdk/models/SelfTradePreventionType); [Kalshi FIX order entry](https://docs.kalshi.com/fix-margin/order-entry)
- With `cancel_order_on_pause=true`, resting orders are cancelled when a trading or exchange pause begins. With the default `false`, they stay on the book. Scheduled maintenance runs every Thursday 3:00–5:00 AM ET. During a trading pause you can cancel but not place or amend orders. During an exchange pause you can do neither. — [Kalshi docs: maintenance & pauses](https://docs.kalshi.com/getting_started/maintenance_and_pauses)
- **Order groups (venue-side fill-rate kill switch):** you create a group with a contracts limit of 1–1,000,000 and attach orders to it. If filled contracts within a rolling 15-second window exceed the limit, the group triggers and every resting order in it is cancelled. New orders are rejected until you reset the group. You can also trigger the group manually, and deleting it cancels all its orders. — [Kalshi docs: order groups](https://docs.kalshi.com/getting_started/order_groups)
- I found no explicit Kalshi "cancel-on-disconnect" flag in these sources. The closest primitives are order groups and `cancel_order_on_pause`. See Gaps.

**Kalshi: rate limits**
- Limits are token buckets per second, and most requests cost 10 tokens. Basic tier: 200 read / 100 write, about 20 reads/s and 10 writes/s. Advanced: 300/300, obtained by calling the "Upgrade Account API Usage Level" endpoint. Expert through Prestige are earned from volume share. Batch requests are billed per item. Exceeding a limit returns HTTP 429, and Kalshi advises exponential backoff. — [Kalshi docs: rate limits](https://docs.kalshi.com/getting_started/rate_limits)

**Kalshi: funding and withdrawals**
- Kalshi applies security holds. Debit card deposits are withdrawable after 3 days. Withdrawals to the same bank you deposited from take about 7 days. Withdrawals to a different bank face a 90-day hold. — [Kalshi Help: security holds](https://help.kalshi.com/transfer-funds/withdraw-funds/security-holds)
- Third-party guides say ACH has no Kalshi fee and gives near-instant buying power while settling over 3–5 business days. Debit card deposits may carry about a 2% fee, though another guide says there is none. Credit cards are not accepted. Wire withdrawals are reported as either $5 or a receiving-bank fee. These sources conflict, so verify fees in the app. — [botforkalshi funding](https://www.botforkalshi.com/blog/kalshi-funding-methods); [botforkalshi withdrawal](https://www.botforkalshi.com/blog/kalshi-withdrawal-time); [pm.wiki](https://pm.wiki/fr/learn/kalshi-withdrawal-guide)

**Polymarket US: two distinct API surfaces**
- **Retail API:** you install the Polymarket US app and complete identity verification, which is required before trading or API use. You then sign in at polymarket.us/developer with the same sign-in method and create a key, which gives a Key ID and a Secret Key. The secret is shown once. The Python example loads the secret as an Ed25519 private key. — [Polymarket US docs: authentication](https://docs.polymarket.us/api-reference/authentication.md)
- Retail signing headers are `X-PM-Access-Key`, `X-PM-Timestamp` (ms) and `X-PM-Signature`, a base64 signature over timestamp + method + path. The timestamp must be within 30 seconds of server time. The docs warn that switching sign-in methods may break API key access. — [Polymarket US docs: authentication](https://docs.polymarket.us/api-reference/authentication.md)
- **Exchange trader (institutional) API:** uses "Private Key JWT authentication with RSA keys", signed with RS256. Keys and a Client ID come through an onboarding submission, and accounts need an `x-participant-id` header. — [Polymarket US docs: trader authentication](https://docs.polymarket.us/trader-guide/authentication.md)
- The exchange trader API has a preprod environment (`api.preprod.polymarketexchange.com`) and prod (`api.prod.polymarketexchange.com`). Access is requested via onboarding@polymarket.us, tokens must be refreshed every 3 minutes, and FIX requires AWS PrivateLink. The docs say nothing about preprod liquidity or data quality. — [Polymarket US docs: environments](https://docs.polymarket.us/trader-guide/environments.md)

**Polymarket US: orders, limits, fees, funding**
- `POST /v1/orders` fields: `type` LIMIT/MARKET, `price` (decimal string Amount), `quantity` (contracts; decimals allowed where `minimumTradeQty` < 1), and `tif` ∈ {DAY, GTC, GTD, IOC, FOK}. `participateDontInitiate` means "order must rest on the book prior to matching (maker only)", i.e. post-only. Other fields: `slippageTolerance` (bips/ticks), `synchronousExecution` + `maxBlockTime`, and `manualOrderIndicator` (MANUAL/AUTOMATIC). The reject reason `ORD_REJECT_REASON_INVALID_PRICE_INCREMENT` implies tick sizes. The unsolicited-cancel reasons include `CONNECTION_LOSS` and `LOGOUT`. — [Polymarket US docs: create order](https://docs.polymarket.us/api-reference/orders/create-order.md)
- Self-match prevention is documented only for FIX. — [Polymarket US docs: FIX self-match prevention](https://docs.polymarket.us/institutional/fix-api/fix-self-match-prevention.md) (page found in the docs index, not fetched)
- The retail rate limit is 25 req/s shared across endpoints, and RFQ/combos have their own limits. Limits apply per source IP and Cloudflare location, so different API keys on the same IP share counters. Exceeding them returns HTTP 429; wait at least 1 second, then back off. — [Polymarket US docs: rate limits](https://docs.polymarket.us/api-reference/rate-limits.md)
- **Stale-order stopgap:** when latency rises, new orders and cancel/replace orders not processed within 5 seconds are rejected with "Global Rate Limit Exceeded". This is not a true rate limit, and standalone cancels are never affected. — [Polymarket US docs: rate limits](https://docs.polymarket.us/api-reference/rate-limits.md)
- **Fees (effective Oct 1, 2026):** Fee = Θ × C × p × (1−p). The taker Θ is 0.0695, a maximum of $1.74 per 100 contracts at $0.50. Makers receive a rebate with Θ = 0.0125. Fees use banker's rounding to the nearest $0.01 and "can round to $0.00 on small trades". Maker rebates are computed per fill. As of Oct 7, 2026 the table tennis taker Θ is 0.10 and the combo maker rebate is removed. Taker volume rebates run 10/25/50% at $250k / $1M / $10M of prior-month taker volume. Cancelled, expired or rejected orders pay no fee. — [Polymarket US docs: fees](https://docs.polymarket.us/fees.md)
- Deposit methods: debit card, ACH, Apple Pay, PayPal, Venmo, USDC (iPhone, where available) and wire (minimum $5,000). Card and ACH settle in 3–4 business days with a $50k daily limit. Wire takes 1 business day. Withdrawals are allowed only after the deposit fully clears, and funds must come from accounts in your own name. — [Polymarket US docs: deposit methods](https://docs.polymarket.us/learn/deposits/deposit-methods/overview.md)

### Inferences
- **The key-type mismatch matters for this repo.** CLAUDE.md says `kalshi.py` uses RSA-PSS signing. That is still supported, but Ed25519 is now Kalshi's default, so pick RSA explicitly when generating keys or add Ed25519 support. For Polymarket US, the retail API (Ed25519 key/secret, `X-PM-*` headers, `api.polymarket.us`-style retail endpoints) is a different stack from the international CLOB that `polymarket-client` targets. A US-legal live path needs a separate Polymarket US client, not the existing CLOB client, which is geoblocked for US IPs.
- **Sandboxes test plumbing, not edge.** Use Kalshi demo to test signing, order-state handling, STP and post-only rejects, order-group triggers, and pause behavior. Do not use its fills, slippage or prices to estimate P&L. For retail Polymarket US I found no public sandbox. The first real-money stage there is effectively the integration test, which argues for the smallest possible sizes and a manual-confirm mode at first.
- **Recommended per-venue venue-side safety defaults for a pilot:**
  - Kalshi: every order goes in an order group whose contracts limit is a small multiple of the intended clip size. Set `cancel_order_on_pause=true`, `self_trade_prevention_type=taker_at_cross` (or `maker`), `post_only=true` for maker quotes, and a deterministic `client_order_id` for idempotency.
  - Polymarket US: `participateDontInitiate=true` for maker quotes, IOC/FOK for takes, `manualOrderIndicator=AUTOMATIC`, and an explicit `slippageTolerance`. Treat the 5-second stopgap reject as "unknown state → reconcile", not "retry".
- **Rate limits are per IP on Polymarket US.** Running the scanner and the trader on the same box shares the 25 req/s budget, so read traffic can starve order and cancel traffic. Cancels must keep priority.

### Gaps
- I did not confirm whether Kalshi offers a session-level cancel-on-disconnect, for example on WebSocket or FIX logout. Only order groups and pause-cancel were documented.
- Minimum order size and tick size on Polymarket US were not stated on the create-order page. The `minimumTradeQty` field suggests fractional contracts on some markets ([fractional shares page](https://docs.polymarket.us/learn/trading/basics/fractional-shares.md) exists but was not fetched). For Kalshi, the minimum is 1 contract under the legacy integer API, with fixed-point `_fp` fields now present. I did not verify whether fractional contracts are allowed on event markets.
- I could not verify whether the Polymarket US retail API supports STP or a post-only reject-vs-reprice choice beyond `participateDontInitiate`.
- Kalshi KYC specifics (documents, SSN, state restrictions) were not fetched. Polymarket US KYC is in-app identity verification; details are on the [signup page](https://docs.polymarket.us/learn/get-started/signup.md), which was not fetched.
- I did not confirm Polymarket US retail per-account position or trading limits. A [trading-limits page](https://docs.polymarket.us/learn/trading/access-and-limits/trading-limits.md) exists but was not fetched.

## Q2. Practical risk controls used by algorithmic traders

### Takeaway
The regulatory template is SEC Rule 15c3-5 (market access) together with FIA's principles. It calls for hard pre-trade blocks on credit/capital thresholds and erroneous (price/size/duplicate) orders, applied in aggregate across every entry point. You review those controls regularly and never delegate them to a third party. Venue kill switches are a backstop, not a substitute for your own controls. For a small binary-contract bot this becomes layered limits (per order, per market, per event, daily loss, total exposure), stale-data checks, a file or flag kill switch, and continuous reconciliation of venue fills and positions against the internal ledger.

### Cited Findings
- Rule 15c3-5 requires controls that reject orders exceeding pre-set credit or capital thresholds, aggregated per customer. It also requires rejecting erroneous orders that exceed price or size parameters, "order by order or over a short period", including duplicative orders. Firms must regularly review that the controls work. — summarized from enforcement orders, e.g. [SEC comment/filing](https://www.sec.gov/comments/s7-03-10/s70310-30.pdf), [FINRA CGMI action](https://WWW.FINRA.ORG/sites/default/files/CGMI_action_documents_072617.pdf), [Nasdaq disciplinary action](https://nasdaqtrader.com/content/marketregulation/NASDAQ/DisciplinaryActions/ITGI_NQ_2019.pdf)
- Enforcement has flagged several failures: limits not aggregated across a customer's multiple identifiers or entry points, no hard block at the firm's own thresholds, and reliance on third parties (OMS or clearing) for controls. — [FINRA CGMI action](https://WWW.FINRA.ORG/sites/default/files/CGMI_action_documents_072617.pdf); [NYSE Arca Maxim](https://www.nyse.com/publicdocs/nyse/markets/nyse-arca/disciplinary-actions/2019/Maxim%20Group%20LLC%20NYSE%20Arca%202016-12-00089%20(2019).pdf)
- FIA principles say pre-trade controls should apply to all electronic orders. Exchanges should provide tools to control orders the trading system has lost track of. Systems should be tested before accessing the exchange. Orders should identify whether they are automated or manual. — [FIA presentation to CFTC TAC (2019)](https://cftc.gov/media/2746/TAC100319_FIA/download)
- Exchange kill switches cancel resting orders and block new entry while still allowing cancels, and they can be reversed. Exchanges say they supplement, and do not replace, members' internal risk systems. — [Federal Register exchange kill-switch filing (2014)](https://www.govinfo.gov/content/pkg/FR-2014-02-24/pdf/2014-03798.pdf)
- Background on runaway-algo risk after Knight Capital's 2012 incident. — [Markets Media: Taming runaway algos in the wake of Knight](https://www.marketsmedia.com/taming-runaway-algos-in-the-wake-of-knight/)
- Venue primitives you can use as an external backstop: Kalshi order groups (rolling 15-second fill limit with auto-cancel and manual trigger) ([docs](https://docs.kalshi.com/getting_started/order_groups)), Kalshi `cancel_order_on_pause` ([docs](https://docs.kalshi.com/getting_started/maintenance_and_pauses)), and Polymarket US `slippageTolerance` plus the 5-second stale-order rejection ([create order](https://docs.polymarket.us/api-reference/orders/create-order.md), [rate limits](https://docs.polymarket.us/api-reference/rate-limits.md)).

### Inferences
These are a synthesis tailored to binary event contracts at pilot scale. They are not sourced as industry-standard numbers.
- **Limit hierarchy:** every order must pass all of these, and the checks fail closed on any exception or missing data.
  1. Per-order checks.
     - Max contracts and max notional per order. Pilot: $5–$25 risk per order.
     - Price band (fat finger): reject if the limit price is more than X cents through the current best opposite quote, or if it is outside [0.02, 0.98].
     - Reject if the model's fair value is more than about 15–20c from the market mid. That gap more often means a mapping or data error than real edge.
     - Duplicate-order guard keyed on (market, side, price, size) within N seconds, plus the client order id.
  2. Per-market and per-event caps. Correlated markets on the same game (moneyline, run line, totals) share one event-level cap.
  3. Total open exposure cap: the sum of max loss across open positions. On binaries, max loss is fully known: price × contracts for YES, (1−price) × contracts for NO.
  4. Daily realized-plus-marked loss stop.
  5. Weekly or pilot-total drawdown stop that requires manual review to resume.
- **Stale-data checks:** do not trade if the book snapshot is more than N seconds old, the WebSocket sequence has a gap, the odds or model inputs are older than their TTL, the clock drifts more than 1s from the venue (the signatures have 30-second windows), or the market is within M minutes of close or start. Polymarket US's own 5-second stopgap is a hint about what "stale" means at venue level.
- **Kill switches (three layers):**
  - Process-level: a file or flag, as in this repo's `data/KILL_SWITCH`, that cancels all resting orders and blocks new ones.
  - Venue-level: Kalshi order-group trigger, and cancel-all calls on both venues.
  - Human-level: API key revocation in the venue portal. Both venues support key revocation or deletion.
- **Reconciliation:** each cycle, pull venue positions, balances, open orders and fills, then diff them against the SQLite ledger.
  - Any unexplained difference, such as an unknown order, a position mismatch, or a cash mismatch greater than the fee-rounding tolerance, halts new orders.
  - Treat timeouts and 5xx on order POSTs as unknown state. Query by client_order_id, never blind-retry. This matches the repo rule that order placement never auto-retries.
- **Aggregation lesson from enforcement:** if you run two engines (e.g. sportsbot and kalshi-engine) on the same account, their limits must aggregate at the account level. Otherwise each engine's limit is meaningless.

### Gaps
- I found no published, venue-specific recommended limit values for retail algos on Kalshi or Polymarket US. The numbers above are inference.
- I did not fetch the 17 CFR 240.15c3-5 text directly; the requirements come from enforcement orders that quote it.

## Q3. Measuring edge with small samples: CLV, sample sizes, sequential testing, pre-registration, scale-up rules

### Takeaway
Realized P&L on binary contracts is extremely noisy: per-contract standard deviation is about 50c near 50/50. Detecting a 2–3c edge from P&L alone therefore takes thousands of trades. CLV (entry price versus the venue or sharp-book closing price) has far lower variance and converges much faster, but it is only evidence of profit when the CLV exceeds fees and spread. Whelan (Aug 2026) shows that positive-CLV bets still lost money in most deciles. A sound pilot pre-registers its metric, its threshold and a sequential stopping rule. It scales stakes only by fractional Kelly on a shrunk edge estimate.

### Cited Findings
- Whelan, "The Truth about Closing Line Value" (Aug 12, 2026), uses 3,670 NBA games from 2022/23–2024/25 with about 10 books per game, and takes the median book's last pre-tip quote as the closing line.
  - About half of bets had positive CLV. Three of five positive-CLV deciles still lost money on average.
  - The 9th decile averaged 5% CLV but only a 0.2% profit. Only the top decile produced meaningful profit (+11.4%).
  - Profit requires CLV to beat the margin, which he puts at about 4.5% for NBA.
  - For bets placed 24 hours ahead, results ranged from +16% (top decile) to −25% (bottom decile), which shows how large luck is.
  — [Karl Whelan](https://www.karlwhelan.com/?p=2595)
- Buchdahl's *Fixed Odds Sports Betting* covers betting records and significance testing, but I did not retrieve his exact formulas. — [Waterstones listing](https://www.waterstones.com/book/9781843440192)
- MacLean, Thorp & Ziemba (2010): errors in mean estimates matter far more than errors in variances (about 20:2:1 in mean-variance; about 100:3:1 for log utility). "The Elog maximizing bettor must be very careful not to over bet because of inaccurate mean estimates." Full or near-full Kelly is "very risky" over short horizons. — [MacLean, Thorp, Ziemba, "Good and bad properties of Kelly" (Berkeley copy)](https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf); summarized in [arXiv 2004.09368](https://arxiv.org/pdf/2004.09368)
- Practitioner rule of thumb: half Kelly cuts the long-run growth rate by about 25% and roughly halves expected drawdown. Quarter or half Kelly is common because overbetting is penalized much more than underbetting. — [LuxAlgo: Kelly criterion](https://www.luxalgo.com/library/concept/kelly-criterion/) (low-to-moderate authority)
- Polymarket US's fee is about 1.74c per contract at p = 0.50 for takers, and makers earn about 0.31c rebate at p = 0.50 ([fees](https://docs.polymarket.us/fees.md)). Kalshi's taker fee is 0.07·p·(1−p) and its maker fee 0.0175·p·(1−p) on applicable markets ([Kalshi fee schedule PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf)). These set the CLV hurdle.

### Inferences
Below is my own statistics, standard formulas, not taken from the sources above.
- **Per-trade P&L variance on binaries:** buying 1 contract at price p with true probability q gives mean q − p and variance q(1−q). At q ≈ 0.5 the standard deviation is about $0.50 per contract.
  - Required trades n ≈ ((z_α + z_β) · σ / δ)².
  - For a 3c edge with one-sided α = 0.05 and 80% power: n ≈ (2.49 × 0.5 / 0.03)² ≈ 1,700 trades.
  - For a 2c edge: about 3,900 trades. For a 5c edge: about 620 trades.
  - Just reaching t ≈ 2 at the true edge (50% power) needs about 1,100 trades at 3c.
  - Bets on correlated markets (same game, same day's weather) reduce the effective n.
- **CLV converges faster.** Define per-trade CLV as (closing mid − entry price) for YES buys, net of fees.
  - If the per-trade CLV standard deviation is around 3–5c (an assumption; measure it from paper data), detecting a 1.5c mean CLV at the same α/power needs roughly (2.49 × 4 / 1.5)² ≈ 45 trades.
  - That is one to two orders of magnitude faster than P&L. This is why CLV is the right primary pilot metric.
  - Following Whelan, the hypothesis must be "mean CLV > fees + half-spread paid", not "CLV > 0".
- **Closing-price definition for exchanges:** use the last two-sided mid before the market's trading close or the event start. Do not use the last trade price; the repo already forbids recording stale `last_price` mids. For thin markets, also cross-check against a sharp external reference (sportsbook no-vig consensus) to avoid measuring CLV against a manipulable close.
- **Calibration and Brier/log-loss** against settlements is a third, independent check. It needs settled outcomes, so it is as slow as P&L, but it detects model miscalibration that CLV can miss.
- **Sequential testing:** use Wald's SPRT or a mixture-SPRT/e-value ("always-valid") test on per-trade net CLV. This lets you check after every trade without inflating false positives, which a repeated t-test does.
  - Example pre-registration: H0 mean net CLV = 0 versus H1 = +1.5c, α = 0.05, β = 0.2. Stop for "edge" or "no edge" when the likelihood ratio crosses its boundary, with a hard cap of N_max trades.
  - (Standard method; no source was fetched in this session. See Gaps.)
- **Pre-registration checklist:** write these down and commit them to git before the first live trade.
  - Strategy version hash, market universe, entry rule, stake rule.
  - Primary metric (net CLV), secondary metrics (P&L, Brier), test, α, N_max.
  - Stop and scale rules.
  - Never change filters mid-pilot and then evaluate the combined sample. A change starts a new test.
  - Report all strategies and markets tried, which is a guard against multiple comparisons.
- **Scale-up ladder (suggested):**
  - Stage 0, plumbing: Kalshi demo, or 1-contract live orders on Polymarket US.
  - Stage 1, about $500 bankroll: fixed $5–10 risk per trade until the CLV test resolves or N ≈ 200.
  - Stage 2: move to 0.1–0.25 Kelly computed on a shrunk edge estimate (e.g. a lower confidence bound of net CLV), only after the CLV SPRT accepts H1 and realized P&L is not significantly below what CLV implies.
  - Stage 3: raise the cap by at most 2× per month.
  - Cut stakes by 50% at a pre-set drawdown (e.g. 20% of the pilot bankroll) and halt at 35–40%.
  - Stakes are never raised to recover losses. This is consistent with the repo's "loss response only reduces risk" rule.
- **Idealized drawdown math:** with known parameters in continuous time, the probability of ever halving the bankroll is about 1/2 at full Kelly and about 1/8 at half Kelly. Estimation error makes the real numbers worse. (Derived; the source summary attributed the full-Kelly 1/2 figure to practitioner literature.)

### Gaps
- I did not retrieve Buchdahl's specific CLV significance test, or published empirical estimates of per-trade CLV variance on Kalshi or Polymarket. Measure the variance from your own paper-trading log before fixing N.
- I did not fetch a primary source for SPRT or always-valid inference in a betting context; Wald (1945) is the canonical reference.
- I found no published evidence on how well exchange-closing CLV, as opposed to sportsbook CLV, predicts profit on Kalshi or Polymarket US.

## Q4. Execution realities sims miss

### Takeaway
The largest sim-to-live gaps on these venues come from six sources:
1. Fee rounding on small orders. Kalshi rounds up to the cent per order. Polymarket US uses banker's rounding.
2. Maker fill uncertainty: queue position and adverse selection.
3. Partial fills on thin books.
4. Venue-side rejects: 5-second stale-order rejects, Thursday maintenance pauses, post-only crosses.
5. Settlement lag that locks up capital.
6. Deposit holds that make withdrawals slow.

### Cited Findings
- Kalshi taker fee = round up(0.07 × C × P × (1−P)) and maker fee = round up(0.0175 × C × P × (1−P)), rounded up to the next cent. Users overcharged on maker fees by rounding are reimbursed in the following month only if the excess exceeds $10. — [Kalshi fee schedule PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf)
- Rounding applies to the order total, so small orders pay a higher effective rate. Maker fees apply only on a named set of series, not all markets. Kalshi changed its maker formula from a flat 0.25c/contract on July 1. — [whirligigbear: Maker/Taker math on Kalshi](https://whirligigbear.substack.com/p/makertaker-math-on-kalshi); [rivermarkets](https://www.rivermarkets.com/insights/kalshi-fees.html); [defirate](https://defirate.com/prediction-markets/fees/) (third-party; coverage claims vary)
- Polymarket US uses banker's rounding to $0.01. On multi-fill orders the total never exceeds the rounded cumulative exact fee, and fees can round to $0.00 on small trades. — [Polymarket US fees](https://docs.polymarket.us/fees.md)
- Polymarket US rejects new and cancel/replace orders not processed within 5 seconds during latency spikes. You can cancel an order before it is acknowledged. — [Polymarket US rate limits](https://docs.polymarket.us/api-reference/rate-limits.md)
- Kalshi runs a trading pause every Thursday 3–5 AM ET, during which you cannot place or amend orders. — [Kalshi maintenance](https://docs.kalshi.com/getting_started/maintenance_and_pauses)
- Kalshi settlement: most markets settle within a few hours of the outcome being known, "often within about 3 hours". Timing depends on the source agency, data revisions and manual review. Close time may differ from determination time. Winning contracts pay $1 to the cash balance. — [Kalshi docs: market settlement](https://docs.kalshi.com/getting_started/market_settlement); [Kalshi Help: market FAQs](https://help.kalshi.com/markets/market-faqs)
- Third-party sources cite 24–48 hour resolution windows for some markets. — [NexusFi settlement mechanics](https://nexusfi.com/a/prediction-markets/event-contract-settlement-mechanics) (secondary)
- Deposit holds: Kalshi same-bank withdrawals take about 7 days and different-bank withdrawals 90 days ([Kalshi Help](https://help.kalshi.com/transfer-funds/withdraw-funds/security-holds)). On Polymarket US, withdrawals wait until the deposit fully clears, which is 3–4 business days for card or ACH ([Polymarket US deposits](https://docs.polymarket.us/learn/deposits/deposit-methods/overview.md)).

### Inferences
- **Worked fee examples:**
  - Kalshi taker, 1 contract at 50c: exact fee 0.0175 → $0.02, which is 4% of a 50c stake or 2 percentage points of probability. At 10 contracts: 0.175 → $0.18 (1.8c/contract). At 100 contracts: $1.75 (1.75c/contract).
  - Kalshi maker on a fee-bearing series, 1 contract: $0.0044 → $0.01, about 2.3× the formula rate.
  - **Implication:** the sim must compute fees per order, with venue rounding, at the actual clip size. A sim that charges the per-contract formula understates Kalshi costs badly for 1–5 contract clips. Pilot clips should be at least about 10 contracts on Kalshi if the budget allows, or the edge threshold should include the rounding cost.
  - Polymarket US's banker's rounding can round small fees to $0, which partly favors tiny clips there.
- **Queue position and adverse selection:** a paper maker that assumes a fill whenever the price trades through (or touches) its level overstates fills and understates adverse selection. Real fills arrive disproportionately when the market is moving against you.
  - Model fills as requiring a trade-through by at least the queue ahead of you, using L2 capture as in `maker/`.
  - Measure live markouts: mid at +1, +5 and +30 minutes after each fill. These separate adverse selection from model error.
- **Partial fills:** the strategy and risk layers must handle partial fills as first-class states, for example remaining-quantity tracking and cancel-the-rest on stale data. The ledger must reconcile fills, not orders.
- **Latency:** at the pilot's request rates the limits are not binding: Kalshi Basic allows about 10 writes/s and Polymarket US 25 req/s per IP. Book-update latency still matters for stale quotes. Cancel-before-ack on Polymarket US means quote management should be cancel-first.
- **Capital lockup:** money is tied up from entry until settlement, which can be hours to days after the event, plus withdrawal holds. That lowers the annualized return on bankroll. Size the pilot bankroll as (max concurrent exposure + settlement-lag buffer), not as max exposure alone.

### Gaps
- I have no Polymarket US settlement-timing documentation; it was not fetched.
- I found no empirical source on queue dynamics or fill rates on Kalshi or Polymarket US books.
- Third-party claims about which Kalshi series carry maker fees conflict. Check the current fee page per series.

## Q5. Tax and recordkeeping for US residents trading event contracts

### Takeaway
As of mid/late 2026 the IRS has issued no guidance specific to prediction-market contracts, and practitioners disagree on treatment. The candidates are:
- Section 1256 60/40 mark-to-market: contested and likely weak for prepaid binary event contracts.
- Capital asset treatment (Form 8949 / Schedule D).
- Gambling income: the conservative view for sports or election contracts, and harsher from 2026 because only 90% of losses are deductible, limited to winnings, and only if you itemize.

Platform reporting is incomplete. Kalshi may issue only a 1099-INT or 1099-MISC for interest and rewards, not a trade-level 1099-B. Keep your own complete trade-level ledger regardless, and get a trader-tax CPA's view.

### Cited Findings
- Withum (Aug 20, 2026) says the IRS has issued no specific guidance.
  - Section 1256: event contracts likely are not regulated futures contracts, because they "are prepaid and not subject to a fluctuating collateral requirement". A nonequity-option argument is stronger for contracts referencing commodities or currency than for sports or political contracts.
  - Gambling: sports and election contracts may be viewed as wagers. Winnings are ordinary income. From 2026, only 90% of losses are deductible, limited to gains, and only if you itemize, which "can create taxable income even for a taxpayer who breaks even".
  - Capital treatment: §§1234/1234A capital treatment may not reach non-property events. Gain at expiration may be ordinary under the extinguishment doctrine, while a sale before expiry may be capital.
  — [Withum](https://www.withum.com/resources/prediction-markets-contracts-and-federal-income-tax-wagering-investing-or-something-else/)
- DCM registration alone does not confer §1256 status under §1256(g)(1). Dodd-Frank's §1256(b)(2)(B) swap exclusion may cut against 60/40 if event contracts are swaps or binary options. If §1256 did apply, year-end mark-to-market could tax open contracts in December. Net §1256 losses can be carried back 3 years. — [Seward & Kissel: "Twelve-Fifty-Kicks"](https://www.sewkis.com/publications/twelve-fifty-kicks-prediction-markets-1256-contracts-and-other-tax-issues-to-consider/); [Monaco CPA](https://www.monacocpa.cpa/prediction-market-tax)
- Kalshi users who reach IRS thresholds may receive Form 1099-INT (interest) or 1099-MISC (credits and rewards). Kalshi P&L reports do not determine tax character. Income is taxable under §61 whether or not a form is issued. — [NATP blog](https://www.natptax.com/news-insights/blog/prediction-market-contracts-are-showing-up-on-client-returns/); [Beancount guide (Sep 2026)](https://beancount.io/blog/2026/09/07/prediction-market-winnings-tax-kalshi-polymarket-guide)
- One source notes that as of May 29, 2026 there was no Revenue Ruling, Revenue Procedure, Notice, FAQ or PLR on prediction-market taxation. CFTC rulemaking (ANPRM) is still at proposal stage. — [Monaco CPA: CFTC ANPRM tax guide](https://www.monacocpa.cpa/post/cftc-prediction-market-anprm-tax-guide); [Awaken](https://awaken.tax/media/article/prediction-market-taxes)
- If taking a §1256 position, consider Form 8275 disclosure and get the position in writing from a trader-tax CPA. — [Monaco CPA](https://www.monacocpa.cpa/post/prediction-market-taxes-kalshi-polymarket-robinhood)

### Inferences
- **Recordkeeping for a bot (do it regardless of treatment):** the SQLite ledger should store, per fill: timestamp (UTC), venue, market ticker/slug, side and intent, contracts, price, fee or rebate (exact and as charged), and order id. Also store settlement value and timestamp, and every deposit and withdrawal.
  - Export an annual CSV in Form 8949 shape (description, date acquired, date sold or settled, proceeds, cost basis, gain or loss).
  - Also export a per-event gambling-style session summary, so the CPA can apply whichever treatment they choose.
  - Keep venue statements and monthly P&L downloads as corroboration.
- **The gambling-treatment downside is material for a high-turnover bot.** With the 90% loss-deduction cap, a bot that breaks even on large gross volume can owe tax. Gross winnings and losses per contract or session should be tracked, not just net. This is a reason to get CPA input before scaling volume, not just profits.
- **Polymarket US 1099 practice was not found.** Assume nothing is issued until confirmed.

### Gaps
- I found no authoritative statement on whether Polymarket US issues 1099-B, 1099-MISC or something else for event-contract trading.
- No IRS guidance exists, and treatment may change with CFTC or IRS action. These notes are not tax advice.
- I did not find state-level tax treatment.
