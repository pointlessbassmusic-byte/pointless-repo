# Evaluation: "Megabrain (Jev) & Six Grok Bots" (@savipww, 2026-09-23) + related research

Source: https://x.com/savipww/status/2102720919185617314 (article text read in full
via the fxtwitter JSON mirror).

## 1. What the article actually is

A setup guide for a **memecoin sniping desk** on the FOMO app (Solana / BSC /
Robinhood chain). It is not about sports or prediction markets.
- **Pipeline:** scrape new token pools (GeckoTerminal, DexScreener, chain RPC, the
  project's X account) → cheap filters → ask "Jev" (TypeSafe's typed-answer
  model) structured questions → Jev picks one token → Grok bots size, fill and exit.
- **Evidence of profit: none.** "Six figures" is asserted with no P&L, no track
  record and no sample. The post carries referral links for FOMO and a Telegram
  group. The author says the thresholds were tuned on **one week** of their own trades.
- **Jev is real:** TypeSafe AI launched it 2026-09-15, $40M seed led by DCVC. It
  returns Choice / Score / Boolean answers with probabilities at $0.042 per million
  input tokens. Its speed and cost claims are self-published and not independently
  reproduced ([remio](https://www.remio.ai/post/typesafe-ai-jev-funding-puts-a-445-cost-claim-under-scrutiny),
  [startuphub](https://startuphub.ai/ai-news/artificial-intelligence/2026/typesafe-jev-model-kills-chat)).
  There is no published forecasting accuracy for it.

### Transferable ideas, and where we already stand

| Article idea | Status in this repo | Action |
|---|---|---|
| "Code fetches, the model judges, code decides": the model returns a typed probability, never prose | Not present. No LLM anywhere in the engines | **Build as a shadow signal** (§3, item 2) |
| Cost-ordered kill funnel: free checks first, paid calls only on survivors | Partly present (scanner skips unmatched/illiquid before pricing) | Adopt for the LLM signal: only markets that pass the liquidity, spread and band filters get a paid call |
| Log every rejection with the check that fired | Partly present (scan-drop reasons) | Keep. Add LLM-call rejections |
| Rejection "bench" with reason-specific cooldowns | Not present | Cheap. Add with the LLM signal to cap spend |
| Shadow mode for a week; read only the rows where it disagrees with current logic | Paper mode is the default; the substrate runs shadow | Same discipline, but a week is far too short as an evidence bar (see §3 pass bars) |
| One position at a time; exit is a single comparison | Our risk layer is stricter (fail-closed, per-sport caps, kill switches) | Nothing to add |
| Thresholds tuned on one week of one person's trades | — | **Reject.** This is overfitting. Our rule stays pre-registration plus out-of-sample confirmation |

## 2. What the wider evidence says

**LLM judgement vs markets.** LLMs now match superforecasters on forecasting
benchmarks but still trail liquid markets on their own. Combining an LLM with the
market price is where they help:
- ForecastBench: AI reached superforecaster parity on dataset questions in
  mid-2026 ([FRI](https://forecastingresearch.substack.com/p/ai-models-have-likely-reached-parity),
  [paper](https://arxiv.org/pdf/2409.19839)).
- AIA Forecaster: underperforms market consensus alone, but **market + AIA beats
  market alone**. The model adds information when blended, not as a replacement
  ([arXiv 2511.07678](https://arxiv.org/abs/2511.07678)).
- FIFA World Cup 2026, 104 matches, a contamination-free test: none of four
  frontier models (Claude, GPT, Gemini, Grok) beat the bookmaker on Brier. Betting
  ROI ranged from −18% to +10%, and a flat stake on the market favourite beat all
  four ([arXiv 2607.17765](https://arxiv.org/abs/2607.17765)).
- Hindcast: **backtesting LLMs on past events is contaminated.** Retrieval surfaces
  post-event text, and training data contains the outcomes. Only forward
  (shadow) evaluation is valid ([arXiv 2607.14051](https://arxiv.org/abs/2607.14051)).

**Arbitrage and bias on Polymarket.**
- Single-market arbitrage in NBA games is rare: 7 episodes in 173 games, median
  life 3.6 s. Moneyline↔spread combinations are more frequent but tiny, averaging
  14.8 shares ([arXiv 2605.00864](https://arxiv.org/pdf/2605.00864)).
- Across all of Polymarket, about $40M of arbitrage was extracted, mostly by fast
  bots ([arXiv 2508.03474](https://arxiv.org/pdf/2508.03474)).
- The favourite–longshot bias exists. It matches our Kalshi calibration table: real,
  but smaller than the toll for takers.

**Sharp-book anchoring.**
- Pinnacle devig is the standard fair-value anchor
  ([SharpAPI CLV](https://docs.sharpapi.io/en/api-reference/historical-clv/)).
- The reported +3.8% ROI over 31k bets comes from betting *soft bookmakers* against
  Pinnacle, not from exchanges.
- `polymarket-edge/` already does the exchange version. Our earlier measurements
  found exchange prices within about one tick of fair.

**Weather.**
- Kalshi settles on the NWS CLI report: integer °F over the local-standard-time
  day, with QC. That is not the raw METAR max, and the gap needs a per-city,
  per-season bias correction
  ([weather skill notes](https://claudeskills.info/skills/agiprolabs/claude-trading-skills/kalshi-weather-markets/)).
- By mid-morning the running high is public, so a forecast alone cannot compete
  on same-day markets
  ([trading guide](https://www.botforkalshi.com/blog/kalshi-weather-trading-strategy)).
- **Neither of our weather models conditions on observations so far today.**

**Paid liquidity (the strongest lead, measured live on 2026-10-02).**
- **Kalshi Liquidity Incentive Program** pays for resting orders, *filled or not*.
  It is open to US members through 2027-01-01. Snapshots are taken every second.
  An order at or better than the reference price gets full credit; the credit
  halves for each tick behind it (discount 0.5)
  ([Kalshi help](https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program)).
  - The public `/incentive_programs` API lists **9,089 active programs**.
  - Rough pool size, assuming `period_reward` is in 1/10,000 USD: Sports ≈ $100k/day,
    Economics ≈ $71k, Financials ≈ $45k, Entertainment ≈ $38k,
    **Climate and Weather ≈ $15k/day** (KXRAIN alone ≈ $3k).
- **Polymarket liquidity rewards** pay quadratically by distance from mid, sampled
  once a minute ([docs](https://docs.polymarket.com/market-makers/liquidity-rewards.md)).
  - **18,834 rewarded markets, ≈ $221k/day** in total: weather ≈ $20k/day,
    sports ≈ $13k/day.
  - A one-off book snapshot of the top 58 pools suggested about $27–42/day median
    for a 100-share two-sided quote 1–2¢ from mid. **That is an upper bound:**
    a single snapshot, and the competition varies through the day.
  - Placing orders on Polymarket's main exchange is **blocked for US IP addresses**,
    and we never route around that. Polymarket US is the legal route.
- Maker rebates proper are small. Polymarket sports: taker fee 0.05·p(1−p), with
  15% of it rebated to makers. At p = 0.5 that is about 0.19¢ per share, far below
  the adverse selection we measured.

## 3. Plan, ranked by expected value per unit of work

Every item is data-only or shadow until it clears a pre-registered bar. Live trading
keeps the existing double switches and the `pre-live-gate`.

1. **Incentive-aware quoting, Kalshi first** (US-legal; weather fits the venue split).
   - Why it can work when plain maker quoting failed: the survey showed filled maker
     orders lose 1–7¢ per contract in weather, commodities and crypto. A reward
     stream paid on *unfilled* time changes the sign only if
     `reward share > adverse-selection loss × fill rate`.
     Nothing public answers that; it has to be measured.
   - Build a shadow recorder:
     - Poll `/incentive_programs`.
     - For each rewarded market, snapshot the book at a random second every minute.
     - Compute what our hypothetical quote would score under Kalshi's formula,
       and our share of the pool.
     - Simulate fills from `/markets/trades`, using the "through" rule from
       `maker_fill.py`.
     - Settle at resolution.
   - Variants to log: full-credit quotes at the reference price vs one tick behind
     (half credit, fewer fills); weather vs economics vs sports series.
   - Pass bar: net (rewards − fill losses), clustered by day, mean > 0 with t ≥ 2
     over ≥ 14 days. Then a small live pilot, minimum size, under `pre-live-gate`.
   - Polymarket weather and sports pools: same recorder, but live only via
     Polymarket US or from a non-US jurisdiction.
2. **Weather nowcast: condition on today's observations.**
   - Model P(CLI high ≥ k | running ASOS max so far, hours left, latest forecast),
     with a per-station CLI−METAR bias.
   - Backtestable without contamination: IEM ASOS archive + CLI reports + the Kalshi
     candles already collected for this survey. Same pre-registered split as the
     survey.
3. **Typed LLM judgement as a shadow signal** (the article's core idea, done properly).
   - For each market that survives the funnel, send structured state (teams or
     players, the model's probability, the market mid, recent news snippets with
     timestamps) and ask typed questions:
     - P(home wins)
     - "Does news in the last 6 h change the outlook?" (yes/no, with probability)
   - Implement with Claude structured outputs / tool use; log the model id with
     every call. A Jev adapter is optional later.
   - Blend with the market as the prior, the same way as `blend.model_weight`.
   - Forward-only evaluation (Hindcast): pass if the blended Brier beats the market
     Brier and CLV is positive over ≥ 300 events, pre-registered.
   - Priority targets: slow, thinly traded markets (entertainment, mentions,
     economics), where an LLM reading the news can beat a stale book. Not fast
     sports in-play, where books react in seconds.
4. **MLB / tennis news-timing recorder** (the step 2 already recommended):
   timestamp lineup, scratch and retirement news against the first Polymarket price
   move. This tells us whether item 3 can ever be early enough in sports.
5. **Not pursuing:**
   - Memecoin sniping (out of scope, not a prediction market).
   - Single-market Polymarket arbitrage (seconds-long, sub-$15 size).
   - Copying the article's thresholds.
   - Any courtside or insider data (prohibited by venue rules).

## 4. Data snapshot files (scratchpad, not committed)
`pm_rewards.json` (Polymarket reward configs), `pm_rew_joined.json` (+ Gamma
metadata), `kalshi_lip_active.json` (active Kalshi incentive programs).
