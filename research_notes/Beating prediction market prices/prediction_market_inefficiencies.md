# Exploitable Inefficiencies in Prediction Markets (2020 – Oct 2026): Sourced Inventory

Scope: Polymarket, Kalshi, PredictIt, Manifold, Betfair. Every quantified claim carries a source; marketing/affiliate/vendor pages are flagged as UNVERIFIED. For each inefficiency the notes record: bias, size, sample, period, fee regime in force during the study, and whether it survives current fees / has been arbitraged away.

Research date: 2026-10-07. Roughly 45 tool calls; several primary PDFs (SSRN) were blocked (403/429) and are listed under Gaps.

---

## Fee regimes in force (needed to interpret every study below)

### Takeaway
Polymarket was fee-free from launch through 4 Jan 2026; it introduced taker-only fees in stages (crypto 5 Jan 2026, sports 18 Feb 2026, nearly everything else 30 Mar 2026). Kalshi has charged takers 0.07·P(1−P) since inception and added a maker fee after April 2025. Every pre-2026 Polymarket result is gross of fees; every Kalshi result already embeds taker fees.

### Cited Findings
- Polymarket fee formula is `fee = C × feeRate × p × (1−p)`, taker-only, makers pay 0 and receive rebates. Current official category rates: Crypto 0.07 (20% maker rebate), Sports 0.05 (15%), Finance 0.04 (25%), Politics 0.04 (25%), Economics 0.05, Culture 0.05, Weather 0.05, Other 0.05, Mentions 0.04, Tech 0.04, Geopolitics 0 — [Polymarket docs, fees](https://docs.polymarket.com/polymarket-learn/trading/fees). Note: the brief's "0.03 sports" conflicts with the official docs page (0.05); a third-party table gives Sports 3% — [River Markets](https://www.rivermarkets.com/insights/polymarket-fees.html) (UNVERIFIED third-party; possibly an earlier schedule).
- Rollout dates: Crypto fees 5 Jan 2026 (15-min contracts first, all crypto by 6 Mar); Sports 18 Feb 2026; nearly all other categories 30 Mar 2026. Peak effective rates at p=0.5 after 30 Mar: Crypto 1.80%, Economics 1.50%, Culture/Weather 1.25%, Finance/Politics/Tech 1.00%, Sports 0.75% (the article itself is inconsistent on sports, 0.44%→0.75%), Geopolitics 0 — [Pine Analytics, "Polymarket Fee Rollout"](https://pineanalytics.substack.com/p/polymarket-fee-rollout).
- Maker Rebate Program takes a category-specific share of taker fees: 20% Crypto, 25% most categories, 50% Finance, paid daily pro rata to filled maker volume; referrers get 30% of gross taker fees (direct) / 10% (indirect). Estimated protocol gross ≈ $1.2M/day, net after rebates+referrals ≈ $573K/day (~$209M annualized) — [Pine Analytics](https://pineanalytics.substack.com/p/polymarket-fee-rollout). (These rebate shares conflict with the official docs table above — treat the docs as authoritative.)
- Volume response to fees: sports daily volume rose from $100–150M to $150–250M after fees; crypto settled at $73–100M after a $100–120M spike — [Pine Analytics](https://pineanalytics.substack.com/p/polymarket-fee-rollout).
- Polymarket US (separate CFTC DCM) schedule effective 1 Jul 2026: politics 4%, sports 5%, geopolitics 0, crypto 7%, volume rebates above $250K monthly taker volume — [River Markets, Polymarket US fees](https://www.rivermarkets.com/insights/polymarket-us-fees.html) (UNVERIFIED third-party).
- Kalshi: "During the period corresponding to our sample, Kalshi charged a per-contract fee to people accepting offers (Takers) of $0.07P(1−P) … rounding the total up to the nearest cent. They did not charge fees [to Makers]… Kalshi began to charge fees on Makers after April 2025." The round-up makes the effective fee on a 50¢ contract 1.77% — [Bürgi, Deng & Whelan, "Makers and Takers" (UCD, Jan 2026)](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Third-party guides state the Kalshi maker fee is 25% of the taker fee (≈0.0175·P(1−P)); one says sports has its own lower rate, another says 7% applies to all categories — conflicting, UNVERIFIED — [Allium](https://allium.so/blog/kalshi-fees-why-the-cost-peaks-near-50-cents/); [botforkalshi](https://www.botforkalshi.com/blog/kalshi-fees-explained). A Kalshi fee schedule used in an academic backtest framework: taker 7%·P(1−P), maker 1.75%·P(1−P) — [PredictionMarketBench, arXiv 2602.00133](https://arxiv.org/pdf/2602.00133).
- PredictIt: 10% of profit on closed positions (losses pay no fee) + 5% withdrawal; Sethi shows this is why multi-outcome dutch books persisted — [Sethi, "Fee-Structure Distortions in Prediction Markets" (2016)](https://rajivsethi.substack.com/p/fee-structure-distortions-in-prediction-16-04-10).

### Inferences
- A taker edge on Polymarket must exceed feeRate·p(1−p) per share: at p=0.95 that is 0.33% (crypto 0.07), 0.19% (politics 0.04), 0.24% (sports 0.05); at p=0.5 it is 1.75%/1.00%/1.25%. Maker-side edges face zero fee plus a rebate.
- Kalshi taker drag is at most 1.75–1.77% (p=0.5) and 0.33% at p=0.95; the maker fee added after April 2025 is roughly a quarter of that.

### Gaps
- Could not reach Kalshi's official fee-schedule page (HTTP 429); the exact current maker rate and any category-specific rates are unconfirmed.
- No official Polymarket change-log of fee rates; the Sports 3% vs 5% discrepancy is unresolved.

---

## Key Q1: Favorite-longshot bias (FLB) on Polymarket and Kalshi — magnitude by price bucket and category; does it exceed fees?

### Takeaway
FLB is robustly documented on both venues: Kalshi sub-10¢ contracts lose >60% (post-taker-fee, 2021–Apr 2025) and Polymarket sub-10¢ purchases lose 19.35¢/$ dollar-weighted (2022–Mar 2026, pre-fee). The favorite side is small (+0.28% to +0.83% on Polymarket ≥90¢; Kalshi makers ≥50¢ earn +2.6%), which exceeds current taker fees at high prices in most categories but is marginal, noisy, and — crucially — absent in Sports on Polymarket (where longshots earn positive returns). There is weak evidence the Kalshi bias shrank in 2025; no clean pre/post-fee test exists for Polymarket.

### Cited Findings

**Kalshi (fee regime: taker 0.07·P(1−P), makers free)**
- Data: 46,282 Yes contracts in 12,403 events open ≥24h, 2021 through April 2025, 313,972 purchased-contract prices (Yes+No, up to 11 daily observations per contract); hourly crypto/index markets and spreads >20¢ excluded — [Bürgi, Deng & Whelan 2026](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- "Investors who buy contracts costing less than 10c lose over 60 percent of their money … contracts with prices above 50c earn a small positive rate of return … Overall, the average rate of return on Kalshi contracts is about minus 20%" (pre-fee average −20% arises from the asymmetric pattern, since pre-fee returns net to zero dollar-weighted) — [same](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Worked example in paper: a 5¢ contract winning 3% of the time → −40% pre-fee; a 95¢ contract winning 98% → +3.1% pre-fee. Statistically significant positive post-fee returns for contracts above 70¢ — [same](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Maker vs taker: average post-fee return Makers −9.64% vs Takers −31.46% (F-test rejects equality at very high significance). Makers buying ≥50¢ earn +2.6% post-fee with a 33% standard deviation of returns — [same](https://www.karlwhelan.com/Papers/Kalshi.pdf). The VoxEU summary states the same numbers (takers ≈ −32%, makers ≈ −10%) — [CEPR VoxEU column](https://cepr.org/voxeu/columns/economics-kalshi-prediction-market).
- Volume does NOT cure it: the Mincer-Zarnowitz unbiasedness null is rejected in all five volume quintiles; the lowest-volume quintile has the largest bias coefficient but otherwise higher-volume markets are not more efficient. Also rejected in all five transaction-size quintiles, with the largest-transaction quintile showing the largest bias — [same](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Category: bias rejected for all categories (Financials, Climate & Weather, Crypto, Politics, Entertainment, Economics, Other), with smaller bias coefficients for politics and entertainment — [same](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Time trend: null rejected each year 2021–2025, "some evidence of a weakening … the ψ coefficient for the 2025 data is smaller and less statistically significant" — [same](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Why not arbitraged away (authors' explanation): top-decile market average final volume only $526,245 and thin books; 33% return s.d. on the maker-favorite strategy; maker orders may not fill — [same](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Earlier Kalshi evidence cited in the paper: sports teams' prices in the final 15 minutes were too high relative to win frequency (longshot overpricing in late in-game trading) — [same, citing prior work](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- An independent non-peer-reviewed GitHub project also reports FLB in Kalshi prices (code/data private) — [amandipd/Kalshi-Favorite-Longshot-Bias-Study](https://github.com/amandipd/Kalshi-Favorite-Longshot-Bias-Study) (UNVERIFIED).

**Polymarket (fee regime: fee-free for ~all of the sample; fees only from Jan–Mar 2026)**
- Data: ~588M trades by 2.48M wallets, 11 Nov 2022 – 29 Mar 2026; return sample 560.9M purchases totalling $22.49B in markets resolved by 29 Mar 2026 — [Cardozo & Rivero-Wildemauwe, arXiv 2609.12878 (Sep 2026)](https://arxiv.org/html/2609.12878v1).
- Returns (hold-to-resolution, pre-fee), longshot = <10¢, favorite = ≥90¢:
  - Pooled dollar-weighted: longshots −19.35% (CI −46.99 to +8.30), favorites +0.83% (CI 0.60–1.07).
  - Equal weight per child market: longshots −6.30% (CI −8.38 to −4.22), favorites +0.277%.
  - Equal weight per parent event: longshots +4.09% (CI 1.29–6.90), favorites +0.392% — [same](https://arxiv.org/html/2609.12878v1).
- Purchase mix: longshots 18.0% of purchases but 1.2% of dollars ($273.6M); favorites 10.5% of purchases but 43.0% of dollars ($9.66B) — [same](https://arxiv.org/html/2609.12878v1).
- Shape: losses largest around 20–25¢, gains peak near 60¢, returns approach zero near $1 (Figure 1) — [same](https://arxiv.org/html/2609.12878v1).
- By category (Table 8; longshot/favorite, equal-child-weight | pooled):
  - Crypto: −14.84% / +0.643% | −12.63% / +0.537%
  - Politics: −16.34% / +1.044% | −46.15% / +1.419%
  - Sports: +2.43% / −0.230% | +18.11% / +0.137%  ← bias ABSENT; longshots positive
  - Finance: −2.62% / +0.085% | −72.40% / +1.826%
  - Weather: −25.15% / +0.502% | +24.74% / +0.031% (sign flips with weighting)
  - Culture: −26.49% / +0.735% | −18.78% / +0.829%
  - Tech: −20.68% / +0.816% | −18.91% / +0.846% — [same](https://arxiv.org/html/2609.12878v1).
- Maker vs taker (buyer action), longshots: posted-offer buyers −3.50% (equal) / −17.56% (pooled); accepted-offer buyers −31.17% / −22.17%. Favorites: makers +0.641% / +0.816%; takers −0.460% / +0.877% — [same](https://arxiv.org/html/2609.12878v1).
- Alternative cutoffs (pooled longshot | pooled favorite): 5/95 −17.94% | +0.560%; 10/90 −19.35% | +0.833%; 15/85 −15.24% | +0.924%; 20/80 −8.86% | +0.831% — [same, Table 12](https://arxiv.org/html/2609.12878v1).
- Time-to-resolution (pooled longshot): closes ≥30 days out −22.76%; ≥90 days −24.44%; closes ≤1 day after purchase −10.86%; ≤7 days −15.12%; ≤30 days −29.90%. Equal-weight signs flip (e.g. ≥90 days +30.66%) — [same, Table 14](https://arxiv.org/html/2609.12878v1).
- Concentration: dropping the largest 1/5/10 parent events moves pooled longshot return from −19.35% to −15.8%/−14.5%/−10.6% — [same, Appendix H](https://arxiv.org/html/2609.12878v1).
- Fee-flagged markets (cross-sectional, not before/after): longshots −13.03% equal / +10.03% pooled (CI −4.30 to +24.35); unflagged −5.20% / −20.78%. Authors: fee-flagged markets do not consistently show larger longshot losses; no direct after-fee profitability test — [same, Table 16](https://arxiv.org/html/2609.12878v1).
- Who buys longshots: top decile by past longshot demand supplies 26.6% of next-month longshot dollars; 68.5% remain top-decile after one month, 30.4% after six; recurrent longshot buyers earn −21.96% vs −20.33% others; all five experience groups lose on longshots (−21.55% to −44.13%); most-experienced group accounts for 58.2% of gross longshot losses — [same](https://arxiv.org/html/2609.12878v1).
- Calendar-quarter trend (Appendix B, Fig. 6) plotted but not tabulated; Q1-2026 figures explicitly provisional because <95% of dollars resolved — [same](https://arxiv.org/html/2609.12878v1).
- A separate Polymarket/Kalshi paper (SSRN 6322678) finds that in Mention Markets the apparent FLB is a statistical artifact of contract-lifecycle timing once modelled with penalized splines, and instead reports a "Yes Bias" (overpaying for YES vs NO) linked to volatility spikes near resolution; whales on average lose EV to smaller traders via adverse selection — [Deleep et al., "How Wise is the Crowd?" via Quantpedia](https://quantpedia.com/how-wise-is-the-crowd-in-prediction-markets/) (sample/period not stated on summary page).
- Blog calibration claim (UNVERIFIED, no methodology): 28,407 Polymarket markets Jan 2024–May 2026, mean absolute calibration error 2.1 pp; politics well-calibrated, sports slightly overconfident in favorites, crypto underconfident at extremes — [Poly Syncer](https://www.polysyncer.com/blog/polymarket-prediction-accuracy). This contradicts the arXiv paper's sports finding.

**Betfair / older venues**
- No study of FLB in Betfair 2020/2024 political markets was found; only anecdotal commentary (bettors taking 6–8/1 on Trump post-election 2020) — [PlayUSA](https://www.playusa.com/2020-election-betting-market-keeps-going/). Exchange FLB is documented in tennis (stronger in lower-ranked, later-round, high-profile matches) — [Universidad Pública de Navarra study](https://academica-e.unavarra.es/handle/2454/18748).

### Inferences
- Favorite side vs current Polymarket taker fees: pooled favorite return +0.83% (≥90¢) vs taker fee at p=0.95 of 0.19% (politics), 0.24% (sports), 0.33% (crypto) → survives as a taker in politics/crypto (+1.0–1.4% pooled there) but the equal-weight +0.28% is at or below the crypto taker fee. As a maker (0 fee + rebate) the edge is untouched. The edge is a tiny, high-variance carry, not a scalable strategy (Kalshi: 33% s.d.; Polymarket favorites are 43% of all dollars so it is already crowded).
- Shorting longshots = buying the complementary favorite, so the same fee arithmetic applies; the large longshot losses (−19%) are mostly captured by whoever sells those longshots, i.e. makers, consistent with both papers' maker/taker splits.
- Sports on Polymarket is the one category where the standard FLB trade loses (longshots +18% pooled); Kalshi sports (in-game last-15-min) shows longshot overpricing — the two venues/samples differ, so category edges are not transferable without re-testing.
- "Arbitraged away?": Kalshi's 2025 coefficient weakened; Polymarket's bias persisted through Mar 2026 but the paper cannot separate a post-fee period. No evidence it has disappeared.

### Gaps
- No full price-bucket return table for Polymarket (only Figure 1 shape and the <10¢/≥90¢ tails).
- No published before/after-fee comparison on Polymarket (fees only 2–3 months old at sample end).
- Kalshi per-category return magnitudes not extracted (only "null rejected, smaller for politics/entertainment").
- A newsletter claims Kalshi's CEO promoted an NBER-sourced FLB study on 5 Oct 2026 — [PM Pulse 060](https://pmpulse.substack.com/p/the-pm-pulse-060); I could not locate the paper (UNVERIFIED).

---

## Key Q2: Calibration of Polymarket and Kalshi by category, time-to-resolution, liquidity — where are they miscalibrated?

### Takeaway
Both venues are well calibrated at the extremes and improve monotonically toward resolution (Polymarket Brier 0.042 at 30 days → 0.025 at 24h). The most robust miscalibration is underconfidence (compression toward 50%) in political markets on both venues, amplified by large trades on Kalshi only; Polymarket's mid-range (35–75%) also resolves YES more often than priced in a 7,661-market sample. Liquidity does not fix FLB on Kalshi. A Vanderbilt study ranks Polymarket least "accurate" (67%) vs Kalshi 78% / PredictIt 93% on 2024 election dynamics, disputed by Kalshi.

### Cited Findings
- Polymarket, 7,661 resolved binary markets (Gamma API, collected 25 Apr 2026, voided markets excluded): calibration by bucket (actual YES rate vs midpoint): 0–5%: 0.1% (n=5,693); 5–15%: 13.4% (254); 15–25%: 25.0% (104); 25–35%: 32.9% (82); 35–45%: 48.6% (70); 45–55%: 60.0% (25); 55–65%: 74.4% (39); 65–75%: 96.5% (29); 75–85%: 86.4% (44); 85–95%: 92.3% (52); 95–100%: 100.0% (1,209). Brier: 30d 0.042, 7d 0.032, 24h 0.025. Author: "well-calibrated at the extremes and shows systematic underpricing in the mid-range"; a commenter notes the bucket-midpoint benchmark is biased — [LessWrong, "Empirical calibration of Polymarket"](https://www.lesswrong.com/posts/Hruc6Gwo3vBZFGb6v/empirical-calibration-of-polymarket-analysis-of-7-661).
- Kalshi + Polymarket, ~353M trades across ~429K binary contracts: "most robust finding is underconfidence in political markets, where prices are compressed toward 50%"; on Kalshi large political trades associate with further compression (calibration-slope gap ≈ one-half, survives market/event-clustered bootstraps), NOT robust on Polymarket; roughly half of raw slope variation is estimation noise; conclusion "Calibration is therefore conditional: a price's meaning depends on what, when and how much is traded" — [Nam Anh Le, arXiv 2602.19520 v2 (Aug 2026)](https://arxiv.org/abs/2602.19520).
- Kalshi: prices become more accurate as markets approach closing, but the unbiasedness null is rejected at every daily horizon from 10 days out to close, in every volume quintile, every transaction-size quintile, and every category — [Bürgi, Deng & Whelan](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Vanderbilt (Clinton & Huang), ~2,500 markets / ~$2.5B volume, political markets in final five weeks of 2024 election: "accuracy" Polymarket 67%, Kalshi 78%, PredictIt 93% (log-loss/Brier based); Polymarket daily moves barely correlated with Kalshi/PredictIt; 58% of Polymarket national presidential markets showed negative serial correlation (spikes reversed next day); inefficiency increased in the final two weeks; mutually exclusive contracts sometimes moved together. Kalshi's response: methodology flawed, calibration is "almost perfectly accurate" — [DL News, 5 Dec 2025](https://www.dlnews.com/articles/markets/polymarket-kalshi-prediction-markets-not-so-reliable-says-study).
- Cross-platform matched-market comparison (brier.fyi / Calibration City, >130K markets across Kalshi, Manifold, Metaculus, Polymarket): Manifold "doing pretty badly" on matched markets; Kalshi and Polymarket best at sports, Metaculus best at scientific topics; overall Brier 0.09 one month before close with 62% of markets already within 30% of the correct resolution. Builder deliberately avoided per-platform overall Brier because question mixes differ — [Calibration City on Manifund](https://manifund.org/projects/calibration-city); [Manifold user comment](https://ea.greaterwrong.com/posts/EaR9xFxspmYRkm3eo/manifold-markets-isn-t-very-good/comment/5TJJA6FfF8oFDyLT7).
- Manifold's own defence: early (ACX-era) calibration was poor; "starting from Jul 2023, Manifold's calibration looks great when using market midpoints" (anecdotal) — [same comment](https://ea.greaterwrong.com/posts/EaR9xFxspmYRkm3eo/manifold-markets-isn-t-very-good/comment/5TJJA6FfF8oFDyLT7).
- Good Judgment (vendor with an interest): Polymarket Fed-decision markets "simply tracked CME pricing, volatility and all, adding little to no value" — [Good Judgment](https://goodjudgment.com/testing-polymarkets-most-accurate-claim/).
- Models vs markets (Sethi & co-authors, 2025): statistical models could not beat the market on high-profile 2024 races (presidency) but outperformed in smaller, less-visible races with fewer participants, evaluated by a hypothetical trader betting on model beliefs — [Barnard news on Sethi's "Political Prediction and the Wisdom of Crowds"](https://barnard.edu/news/professor-rajiv-sethi-publishes-new-research-political-prediction-and-collective-intelligence).
- Dune/analyst accuracy claims (UNVERIFIED, no sample): Polymarket 90% "accurate" one month out, 94% four hours out; the dashboard author notes long-odds markets inflate overall accuracy and sports markets are less accurate (~80%) — [The Defiant](https://thedefiant.io/news/research-and-opinion/polymarket-is-up-to-94-accurate-in-predicting-outcomes-analysis); [Forklog summary](https://forklog.com/en/polymarkets-prediction-accuracy-rated-at-94-by-expert/).
- Blog claim (UNVERIFIED): Kalshi shows tighter calibration on macro/Fed markets; both venues show wider calibration error on weather and niche markets attributed to thin books — [Pillar Lab AI](https://pillarlabai.com/blog/prediction-market-accuracy-rates-kalshi-polymarket/).
- Sports horizon: NBA accuracy rises only from ~66% five days before tip-off to 68% one hour before (irreducible uncertainty) — [Laidlaw Scholars analysis](https://laidlawscholars.network/posts/polymarket-calibration-analysis-across-4-domains) (student analysis, method not shown).
- Kalshi vs DraftKings, 285 NFL games 2025-26: Kalshi slightly more accurate, gap small and marginally significant, and it disappears after controlling for the distribution of quoted probabilities — [Claremont thesis](https://scholarship.claremont.edu/cmc_theses/4214) (undergraduate thesis).

### Inferences
- The political underconfidence (compression to 50%) is the one calibration defect found independently on both venues; combined with the Polymarket favorite premium in Politics (+1.0–1.4% pooled) it suggests buying established political favorites as a maker is the most consistent documented edge — small, slow, and crowded.
- The Vanderbilt serial-correlation result (58% negative autocorrelation in Polymarket presidential markets) implies a mean-reversion/fade-the-spike strategy existed in Oct–Nov 2024 under zero fees; no one has published a post-fee replication.
- Liquidity-conditional edges: Sethi's result (models beat markets only in low-attention races) and Kalshi's lowest-volume-quintile having the largest FLB both point to thin, low-attention markets as where model-beatable mispricing lives — exactly where capacity is smallest.

### Gaps
- No peer-reviewed category × horizon × liquidity calibration table for Polymarket; the only bucket table is a blog with n<100 in mid-range buckets.
- Could not open the Vanderbilt paper itself; only the DL News summary.
- The "Reichenbach & Walther (2026)" calibration paper cited by the Polymarket-v1 database paper could not be located.
- Manifold's official calibration page was not fetched directly.

---

## Key Q3: Lead-lag between venues and vs sportsbooks/news — who leads, by how long, and is the follower's book hittable?

### Takeaway
Around the 2024 election Polymarket led Kalshi (strongest when Polymarket liquidity/large-trade flow was high); over 2023–2026 leadership is bidirectional — Kalshi leads sports and when it has higher relative volume, Polymarket leads politics/entertainment. Polymarket crypto quotes respond to Binance moves after a median 347 ms, but a 43-feature model could not beat Polymarket's own book out of sample and simulated trading lost −0.116 payoff units per trade after fees/slippage. No rigorous Polymarket-vs-Pinnacle lead-lag study exists; vendor blogs contradict each other.

### Cited Findings
- Ng, Peng, Tao & Zhou (SSRN 5331995, posted Jul 2025, revised Apr 2026): identical contracts on Polymarket, Kalshi, PredictIt, Robinhood pre-2024 election; "more liquid prediction markets substantially outperform polls … yet there are significant price disparities across platforms"; Polymarket leads Kalshi, lead largest when liquidity/activity high, implying "economically meaningful arbitrage opportunities"; "net order imbalance from large trades strongly predicts subsequent returns"; over 2023–2026 "leadership on Kalshi pronounced in sports markets and when the platform exhibits higher relative volume and large-trade activity. Polymarket leads in politics and entertainment, and when insider trading risk is high" — [SSRN abstract](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5331995) (full text 403; magnitudes in minutes not available).
- Cross-platform price gaps: >100,000 events across ten venues 2018–2025; ~6% of events listed concurrently on multiple platforms; "execution-aware price deviations of 2–4% on average" that persist, attributed to resolution-rule differences and institutional segmentation, not information — [Gebele & Matthes, arXiv 2601.01706](https://arxiv.org/abs/2601.01706).
- Polymarket–Binance: 727.1M deduplicated rows, 54 Polymarket days (12 Feb–15 May 2026), 2,936,031 lead-lag pairs; median apparent lag 16 ms on venue clocks (±99 ms constant-offset ambiguity); Polymarket quotes respond to large Binance moves after a median 347 ms (collector clock); walk-forward logistic model with 43 microstructure features does not beat Polymarket's book-implied probability OOS; simulated trading −0.116 normalized payoff units per attempted trade under stated fee/slippage; "did not produce a tradable edge" — [Young, OpenMarket, arXiv 2607.26245](https://arxiv.org/abs/2607.26245). Fee regime: crypto fees live (0.07) during this sample.
- Polymarket 2024 election microstructure: October Trump-market true turnover ≈ $391M vs $958M naive volume; moving the forecast 5 pp cost ≈ $9.1M (vs $15.6M under naive measure); naive overstatement holds across 249 markets, largest in thin young markets; capital entered both sides through October (heterogeneous beliefs, not one-sided manipulation) — [Tsang & Yang, arXiv 2603.03136 v3](https://arxiv.org/abs/2603.03136).
- Sportsbook direction, vendor claims (UNVERIFIED, conflicting): OddsPapi says Polymarket "reacts slower … sportsbooks adjust within seconds. Polymarket can take minutes or hours" — [OddsPapi](https://oddspapi.io/blog/polymarket-arbitrage-local-bookmakers/); LaikaLabs says informed Polymarket traders front-run sharp sportsbook moves — [LaikaLabs](https://laikalabs.ai/prediction-markets/polymarket-sports-odds). Neither provides timestamps.
- OddsPapi's own backtest tutorial for Polymarket vs Pinnacle reports no results; notes Pinnacle's public API closed 23 Jul 2025 — [OddsPapi backtest guide](https://oddspapi.io/blog/?p=2489).
- Price-level comparison (no lead-lag): Polymarket World Series prices for eight teams sum to ~99% vs FanDuel 111% — [rg.org daily odds](https://rg.org/news/prediction-markets/polymarket-daily-odds-october-7-2026) (trade-press, descriptive).
- Order-book measurement caveat: trade direction inferred from Polymarket's public feed matches on-chain truth only ~59–62% of the time (vs ~80% Lee-Ready on Nasdaq); effective half-spread sign flips in 50–67% of markets between feed and on-chain — [Dubach, arXiv 2604.24366 v2](https://arxiv.org/abs/2604.24366). Tick rule 49.83% / bulk volume 50.51% accuracy on 1.20B on-chain trades — [Qin & Yang, Polymarket-v1 Database, arXiv 2606.04217](https://arxiv.org/abs/2606.04217).

### Inferences
- The one rigorous attempt to monetize a known lead-lag (Binance→Polymarket crypto) failed after fees in the post-fee regime; Polymarket's one-tick top-of-book spreads plus the 0.07 crypto taker rate (1.75% at p=0.5) consume a sub-second informational edge.
- Cross-venue 2–4% execution-aware gaps and Polymarket→Kalshi leadership in politics are the best-documented venue-level inefficiencies, but Kalshi's 0.07 taker fee (up to 1.75%) and resolution-rule mismatch risk eat most of a 2–4% gap; nobody has published realized P&L from hitting the lagging venue.
- Sports lead-lag vs Pinnacle is untested in the literature; the CLAUDE.md engines' assumption that sportsbook consensus leads Polymarket is a hypothesis, not a measured fact.

### Gaps
- No minute-level lead magnitudes for Polymarket↔Kalshi (SSRN full text blocked).
- No study hitting the follower's book with realistic depth; no Polymarket/Kalshi vs Pinnacle CLV study.
- Kalshi vs sportsbook: only one undergraduate thesis (Kalshi vs DraftKings, 285 NFL games).

---

## Key Q4: Market-maker and rebate economics — who earns on Polymarket, and what do top wallets run?

### Takeaway
Profits are extremely concentrated (top 1% of users capture 76.5% of profits; top-10 arbitrage wallets took ~21% of $39.6M arb profit) and accrue to liquidity providers and bots: execution/timing, not forecasting accuracy, explains who earns (bots with 49.9% directional accuracy made ~$133M in aggregate; retail with 51.3% accuracy lost money). Sports generated 81% of top-user gains. Rebate farming post-2026 funnels 15–25% of taker fees to makers but no study yet quantifies rebate-only P&L; ~25% of historical volume was wash trading (airdrop farming under zero fees).

### Cited Findings
- "Who wins and who loses in prediction markets? Evidence from Polymarket" (SSRN 6443103; dataset: 588M trades, $67B volume): top 1% of users capture 76.5% of profits; successful traders provide liquidity with limit orders, unsuccessful ones take with market orders; monthly performance "weakly persistent" (possibly selection); most successful users traded frequently in sports, which accounted for 81% of gains; insider trading unlikely to explain the largest winners — [HN thread with author comments](https://news.ycombinator.com/item?id=48221877); dataset at [HuggingFace vgregoire/polymarket-users](https://huggingface.co/datasets/vgregoire/polymarket-users). (SSRN page not fetched; period not stated.)
- Della Vedova, "Who Profits from Prediction Markets? Execution, not Information" (SSRN 6191618): 222M trades with terminal payoffs on "the world's largest prediction market"; forecasting and execution skill share <1% variance; retail 51.3% directional accuracy yet lose; automated traders 49.9% accuracy, ≈$133M aggregate profit, "the only participants who make money"; no trader type beats price-implied accuracy; bots enter >8 days before resolution vs ~3 days for retail; ~70% of bot edge from timing, rest from spread capture/execution; VWAP benchmark understates execution persistence fivefold — [Quantpedia summary](https://quantpedia.com/who-profits-from-prediction-markets/).
- Saguillo, Ghafouri, Kiffer & Suarez-Tangil (IMDEA), arXiv 2508.03474: 8,659 single-condition + 1,578 NegRisk markets (17,218 conditions) resolved 1 Apr 2024–1 Apr 2025, ~86M bids; realized arbitrage profit $39,587,585 (single-condition $10.58M: buy-below-$1 $5.90M, sell-above-$1 $4.68M; within/between markets $29.02M: buy YES $11.09M, buy NO $17.31M, sell YES $0.61M, sell NO $4K); top 10 wallets ≈ $8.18M (~21%); top wallet $2,009,632 over 4,049 transactions; one wallet $768,566 from 211 transactions; one @Tutaaa91 trade earned $58,983 buying YES and NO both under $0.02; top players described as "bot-like"; only ~1% of estimated US-election single-condition opportunities exploited; all fee-free — [arXiv 2508.03474 HTML](https://arxiv.org/html/2508.03474v1).
- Leaderboard pattern study (Jan–Mar 2026, 40 wallets; non-academic): sports leader "kch123" ≈ $10.35M cumulative profit; a BTC 5/15-minute binary market maker with ~94% of trades buying both YES and NO simultaneously (spread capture), at least three addresses running the pattern; one MM address dominates Economics liquidity — [leolabs](https://leolabs.me/blog/pm-top20-strategy-patterns/en/) (UNVERIFIED).
- Share of profitable wallets: ~28% lifetime-profitable of ~3.2M wallets — [polyscalping](https://polyscalping.org/leaderboard) (UNVERIFIED vendor); ~17% ever net-profitable per a Dune-based analysis cited by a guide — [datawallet](https://www.datawallet.com/crypto/top-polymarket-trading-strategies) (UNVERIFIED).
- Wash trading (Columbia, SSRN, not peer-reviewed): ~25% of Polymarket historical volume consistent with wash trading; 14% of 1.26M wallets flagged; peaked near 60% of weekly volume Dec 2024, <5% by May 2025, ~20% early Oct 2025; drivers: no KYC, no fees, airdrop expectations — [Decrypt](https://decrypt.co/347842/columbia-study-25-polymarket-volume-wash-trading); [study PDF mirror](https://gamblingharm.org/wp-content/uploads/2025/11/Polymarket-Wash-Trading-Study.pdf). Order-book study finds self-counterparty wash share median 1%, upper tail 22% — [Dubach](https://arxiv.org/abs/2604.24366).
- Order-book facts relevant to MM: longshot spread premium (wider spreads at low prices); depth close to uniform across price levels; cross-sectional depth explained by duration, price level and volume with no residual time-to-close effect — [Dubach](https://arxiv.org/abs/2604.24366). Polymarket one-tick top-of-book spreads as a stylized fact — [OpenMarket](https://arxiv.org/abs/2607.26245).
- Rebate economics: maker rebates = 15–25% of taker fees by category (official docs) — [Polymarket docs](https://docs.polymarket.com/polymarket-learn/trading/fees); Pine Analytics' split (20/25/50%) and referral 30%/10% — [Pine](https://pineanalytics.substack.com/p/polymarket-fee-rollout).
- Kalshi: makers earn +2.6% post-fee on ≥50¢ contracts vs takers −31.46% overall — [Bürgi, Deng & Whelan](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Sethi notes volume inflation on Kalshi after its June 2026 perpetual-futures launch (no numbers in excerpt) — [Sethi, "Volume Inflation Without Wash Trading"](https://rajivsethi.substack.com/p/volume-inflation-without-wash-trading).
- Named-wallet insider case studies (trade press, on-chain timing evidence only): Maduro market Jan 2026 — new wallet staked $33,933 at ~7¢, profit $409,882 (1,108%), Lookonchain flagged three wallets with combined $630,484; funding traced to Coinbase-only wallets, WLFI link disputed by Bubblemaps — [PublicGaming](https://www.publicgaming.com/news-categories-m/igaming-mobile-sports-betting/15371-polymarket-user-nets-1108-return-after-maduro-capture-leads-to-insider-trading-questions); [4Pillars](https://research.4pillars.io/en/research/unmasking-the-maduro-insider-on-polymarket). Nobel 2025 — account "6741" spent $29K over five hours, profit $53,500; odds 5%→70%; three accounts ≈ $90K combined — [The Block](https://www.theblock.co/post/374199/officials-probe-polymarket-over-suspicious-trades-predicting-nobel-peace-prize-winner-reports). Iran strikes — fresh accounts netted ~$1M hours before (Bubblemaps) — [The Block](https://www.theblock.co/post/391650/fresh-accounts-netted-1-million-on-polymarket-hours-before-us-airstrikes-on-iran-bubblemaps). Biden pardons — ~$300K profit, two accounts cashing to the same Kraken wallet — [OPB/NPR](https://www.opb.org/article/2026/04/16/a-polymarket-trader-made-dollar300000-of-bidens-pardons/). One professor's estimate of $143M insider profits on Polymarket (UNVERIFIED, secondary) — [same search results, KUOW](https://www.kuow.org:443/stories/a-polymarket-trader-made-300-000-betting-on-biden-s-pardons-a-new-analysis-shows). Sethi's framing: "The Venezuela Windfall" / "Guessing Games" — [Sethi](https://rajivsethi.substack.com/p/the-venezuela-windfall).

### Inferences
- The earners are (a) passive makers capturing spread/FLB on the favorite side, (b) bots doing complete-set/NegRisk rebalancing (buy YES+NO < $1, buy all NO in a NegRisk event > $(n−1)), and (c) occasional informed/insider directional traders in thin geopolitics markets. All three were measured under zero fees; (b) now pays taker fees on the aggressive legs in all categories except geopolitics, and the paper's 2–5¢ thresholds imply many opportunities fall below a 1.0–1.75% fee at mid prices.
- Rebate farming is plausible but unmeasured: at 15–25% of taker fees, a maker's rebate on a 50¢ sports fill is ≈0.19¢/share (0.15 × 1.25¢); this is a small add-on to spread capture, not a standalone strategy.
- Leaderboard P&L is contaminated by wash trading (up to 60% of weekly volume at the Dec 2024 peak) and does not report risk; "copy the leaderboard" is not evidence of a repeatable edge.

### Gaps
- No published measurement of rebate-program P&L or of maker volume share post-fees.
- The 76.5% paper's sample period and maker/taker return numbers were not retrievable (SSRN blocked).
- No Dune dashboard with a reproducible market-maker classification was found.

---

## Key Q5: Resolution/settlement effects — late-print convergence, near-resolution capture, settlement lag, and whether they pay after fees

### Takeaway
Near-certain contracts trade below par because collateral is locked until oracle finalization (a maturity-dependent settlement discount that explains 48–88% of the raw near-certainty horizon gradient), so "resolution sniping" is mostly compensation for capital lock-up and tail risk, not free money. In short-horizon crypto contracts the final seconds are actively manipulated: 821 wallets earned ~$8.2M pushing settlement prints, flipping 34% of 90–100%-priced outcomes vs 1% baseline. No study documents positive after-fee returns from buying 95–99¢ contracts.

### Cited Findings
- Settlement discount: prices in collateralized markets are "discounted probabilities"; near-certain distant contracts trade below par; adjusting by recovered settlement-discount curves "reduces the near-certainty horizon gradient by roughly 48–88%"; NegRisk conversion compresses the discount, yield-bearing collateral flattens the term structure; UMA undisputed proposals finalize after a two-hour challenge window, disputes escalate to the DVM — [Gebele & Matthes, arXiv 2605.31431](https://arxiv.org/abs/2605.31431).
- Settlement manipulation on Polymarket BTC up/down (5-min launched 12 Feb 2026, 15-min Oct 2025, 4-hour Oct 2025; ~243K wallets): Binance order flow spikes ~50% in the final ten seconds (3.9× in near-even cycles, ~6% of cycles); ~1,600 cycles flagged as pushed (skewed overnight 56% vs 40%, weekend 44% vs 27%); push flipped winner 65% of the time in near-even cycles vs 41%; in cycles priced 90–100% a push reversed the outcome 34% vs 1% without; 821 wallets (~1 in 300) earned ~$8.2M in pushed cycles; 93% of non-MM losses fall on retail; prices revert within 10 s (¼ of the move in near-even cycles); signature largely absent at 15-minute horizon — [Dai, Jia & Yu, arXiv 2606.31675](https://arxiv.org/html/2606.31675v1). Fee regime: crypto 0.07 taker fee live.
- Dispute frequency/timing: only ~1.5% of UMA proposals disputed; disputes resolve within 48–96 hours; liveness 2 hours to 2 days — [arXiv 2605.30802](https://arxiv.org/pdf/2605.30802). WSJ (via Finance Magnates): in ~1 in 5 disputes at least one voter held a direct stake; >1,150 Polymarket markets triggered arbitration in 2026 — [Finance Magnates](https://www.financemagnates.com/fintech/polymarkets-arbitration-model-faces-conflict-of-interest-questions/).
- LLMs cannot predict which markets will be disputed but reach 89.58% agreement with UMA's final resolutions once a dispute starts — [arXiv 2604.15674](https://arxiv.org/pdf/2604.15674).
- Kalshi: statistically significant positive post-fee returns above 70¢ and +2.6% for makers ≥50¢ (not annualized; 33% s.d.) — [Bürgi, Deng & Whelan](https://www.karlwhelan.com/Papers/Kalshi.pdf). Polymarket ≥90¢ favorites +0.83% pooled, approaching zero near $1 — [arXiv 2609.12878](https://arxiv.org/html/2609.12878v1).
- Time-value illustration: 4¢ on a 96¢ outlay over 18 months ≈ 2.8%/yr before fees — [Quantcha](https://quantcha.com/news/?p=618) (blog). Polymarket introduced a 4% annualized holding reward on selected long-dated political markets in mid-2025 — [Fool.com](https://fool.com/investing/2026/04/12/should-you-be-using-polymarket-to-invest-in-crypto/) (secondary; current status unverified).
- "Resolution sniping" vendor pitch: contracts "often linger at 94¢, 95¢, 96¢" near resolution; concedes a 95¢ "lock" resolving NO loses the whole position — [pnlpro.fit](https://pnlpro.fit/blog/resolution-sniping-guide.html) (UNVERIFIED marketing). Several 2024–26 resolution-dispute cases produced total wipeouts on positions above $0.95 (illustrative, not measured) — [simplefunctions.dev](https://simplefunctions.dev/opinions/tail-end-trading-merger-arbitrage-polymarket) (UNVERIFIED).
- No residual time-to-close effect on order-book depth after controlling for duration and p(1−p) — [Dubach](https://arxiv.org/abs/2604.24366).

### Inferences
- Fee arithmetic at 97¢: Polymarket taker fee = 0.04–0.07 × 0.97 × 0.03 ≈ 0.12–0.20% of notional, versus a 3.1% gross gain if it resolves YES; the trade's EV is governed by (true win rate − 0.97) and the oracle/lock-up risk, not by fees. The settlement-discount paper implies most of the 3¢ is fair compensation; residual edge, if any, is in the un-explained 12–52% of the gradient.
- Short-horizon crypto "near-resolution capture" is adversarial: in the 5-minute series, whoever is on the far side of a settlement push bears a 34% reversal rate even at 90–100% prices. Longer horizons (15-min+) show far less of this.
- Settlement-lag arbitrage (buying post-event, pre-finalization) is structurally a carry trade on the UMA window; no public P&L exists.

### Gaps
- No empirical returns table for buys at 95–99¢ by horizon on either venue after fees.
- Full per-horizon settlement-wedge numbers (platform, sample, ASW values) are in the 2605.31431 full text, not fetched.
- No study of PredictIt/Betfair settlement effects in the 2020–2026 window.

---

## Key Q6: Low-liquidity and long-dated mispricings — time value, multi-outcome dutch books, combinatorial inconsistencies, realized profitability

### Takeaway
Under zero fees, Polymarket complete-set and NegRisk inconsistencies were large and frequent (7,051 of 17.2K conditions had ≥1 opportunity; 42% of NegRisk markets; ~$40M realized) but captured mostly by a few bot-like wallets, with only ~1% of US-election single-condition opportunities exploited and execution non-atomic. Cross-platform gaps of 2–4% persist structurally. PredictIt multi-outcome books summed to 108–208% because the 10%-of-profit fee made the all-NO bundle unprofitable. Long-dated prices embed a maturity discount that is mostly rational carry.

### Cited Findings
- Polymarket single-condition (YES+NO ≠ $1): 7,051 of 17.2K conditions had ≥1 opportunity above a 2¢ threshold (4,423 single-condition, 2,628 NegRisk); all observed single-condition opportunities were "long" (YES+NO < $1); median profit per dollar "well above our 2 cent cap"; NegRisk within-market: 662 of 1,578 markets (~42%) had ≥1 opportunity (5¢ threshold), ~100 opportunities per market on average, outliers in Sports, average maximum profit per dollar ≈ 40¢ for long and short; combinatorial cross-market: of 46,360 US-election pairs, 1,576 LLM-flagged, 11–13 confirmed dependent, extraction evidence in 5, total ≈ $95K; realized total $39.59M; ~1% of estimated US-election single-condition opportunities exploited; execution non-atomic; price averaging understates margins; one election-heavy year — [Saguillo et al., arXiv 2508.03474](https://arxiv.org/html/2508.03474v1). Fee regime: zero fees.
- Cross-platform: 2–4% execution-aware deviations on semantically equivalent markets, persistent, "structural frictions rather than informational disagreement"; ~6% of >100K events (2018–2025) are multi-listed — [arXiv 2601.01706](https://arxiv.org/abs/2601.01706).
- PredictIt: presidential-winner-by-party market implied 108%; buying NO on all three cost $1.96 for a sure $2.00 payout, unprofitable after the 10%-of-gains fee; 2015 Republican nomination market summed to 208% across ten contracts; would "vanish in an instant" under Intrade-style worst-case-loss margining — [Sethi 2016](https://rajivsethi.substack.com/p/fee-structure-distortions-in-prediction-16-04-10); [Sethi 2015](https://rajivsethi.substack.com/p/prediction-market-design-15-04-03). Forum worked example: a sure 98¢ YES loses money after 10% + 5% withdrawal — [EA Forum comment](https://ea.greaterwrong.com/posts/cJc3f4HmFqCZsgGJe/don-t-interpret-prediction-market-prices-as-probabilities/comment/9yQNGLFW8a9hehaug) (informal).
- Time value: settlement discount explains 48–88% of the near-certainty horizon gradient — [arXiv 2605.31431](https://arxiv.org/abs/2605.31431). Older evidence that traders ignore time value creating arbitrage (de los Reyes & Raifman 2008, Journal of Prediction Markets) — [IDEAS/RePEc](https://ideas.repec.org/a/buc/jpredm/v2y2008i3p1-13.html).
- Liquidity by horizon (secondary, UNVERIFIED): average liquidity >$450K for markets longer than 30 days vs ~$10K for sub-day markets — [cryptonews.net](https://cryptonews.net/news/analytics/33207856/).
- Thin-market manipulation cost: moving the 2024 Trump YES price 5 pp cost ≈ $9.1M in October (true turnover), i.e. markets shallower than naive volume suggests; overstatement largest in thin, young markets — [Tsang & Yang](https://arxiv.org/abs/2603.03136). A study reported that "small bets can shift US election prediction market odds" — [bonus.com summary](https://www.bonus.com/news/study-says-small-bets-can-shift-us-election-prediction-market-odds/) (secondary).
- Polymarket mid-range underpricing (35–75% buckets resolve YES more than priced, n=25–70 per bucket) — [LessWrong](https://www.lesswrong.com/posts/Hruc6Gwo3vBZFGb6v/empirical-calibration-of-polymarket-analysis-of-7-661).
- Kalshi: FLB largest in the lowest-volume quintile — [Bürgi, Deng & Whelan](https://www.karlwhelan.com/Papers/Kalshi.pdf). Sethi: models beat markets only in small, low-participation races — [Barnard](https://barnard.edu/news/professor-rajiv-sethi-publishes-new-research-political-prediction-and-collective-intelligence).

### Inferences
- Complete-set/NegRisk arbitrage after 30 Mar 2026: a buy-all-NO NegRisk leg pays feeRate·p(1−p) per share per leg; with n legs at mid prices the fee bill is n × (1.0–1.75%) of leg notional, so the 2–5¢ opportunities that dominated the Saguillo distribution are largely sub-fee now except in geopolitics (0%) and when resting as a maker. The ~40¢/$ "average maximum" opportunities were real but tiny in volume (<~2K tokens per pair; ~$100 per cross-market opportunity).
- The 2–4% cross-venue gap is roughly the size of one Kalshi round-trip plus resolution-rule risk; it is a structural rent for whoever can hold both legs to resolution and absorb a mis-resolution, not a riskless arb.
- Dutch-book summing to >100% is largely a fee artifact (PredictIt) or a NegRisk mechanics artifact (Polymarket); on Polymarket post-2026 the geopolitics category is the only place such books remain fee-free.

### Gaps
- No post-fee (2026) measurement of Polymarket complete-set/NegRisk arbitrage frequency or profit.
- No peer-reviewed quantification of PredictIt bundle profitability net of fees (2020–2026).
- Manifold/Betfair combinatorial inconsistencies: no sources found for 2020–2026.

---

## Key Q7: Which categories show persistent, model-beatable mispricing (sports vs politics vs crypto vs weather/economics)?

### Takeaway
Politics shows the most consistent documented mispricing (underconfidence/compression on both venues; Polymarket favorite premium +1.0–1.4% pooled; Kalshi "smaller" but present) and is the only area where an outside model (Sethi's) beat the market, in low-attention races. Sports is where the money is actually made on Polymarket (81% of top-user gains) but shows no standard FLB there and only tiny Kalshi-vs-book accuracy gaps. Crypto short-horizon markets are efficient against Binance after fees and dominated by settlement games and two-sided MMs. Weather/economics evidence is thin and vendor-driven; Kalshi macro markets track CME.

### Cited Findings
- Polymarket by category (pooled longshot / favorite): Politics −46.15% / +1.419%; Crypto −12.63% / +0.537%; Finance −72.40% / +1.826%; Sports +18.11% / +0.137% (no bias); Weather +24.74% / +0.031% (sign flips); Culture −18.78% / +0.829%; Tech −18.91% / +0.846% — [arXiv 2609.12878](https://arxiv.org/html/2609.12878v1).
- Political underconfidence on both venues; large-trade amplification only on Kalshi — [arXiv 2602.19520](https://arxiv.org/abs/2602.19520).
- Kalshi FLB in every category, smaller for politics and entertainment; sports addition (Jan 2025) does not change results — [Bürgi, Deng & Whelan](https://www.karlwhelan.com/Papers/Kalshi.pdf).
- Sports: 81% of top-user gains on Polymarket; top sports wallet ≈ $10.35M (Jan–Mar 2026) — [HN thread](https://news.ycombinator.com/item?id=48221877); [leolabs](https://leolabs.me/blog/pm-top20-strategy-patterns/en/) (latter UNVERIFIED). Kalshi vs DraftKings NFL: small, marginal accuracy edge for Kalshi that vanishes after distribution controls — [Claremont thesis](https://scholarship.claremont.edu/cmc_theses/4214). Kalshi/Polymarket best at sports in matched cross-platform comparison — [Calibration City](https://manifund.org/projects/calibration-city). Kalshi sports contracts ≈ 85% of its June 2026 $31B volume (Dune, via trade press) — [unlock-bc](https://www.unlock-bc.com/en/kalshi-and-polymarket-record-45-billion-combined-volume-in-june-as-world-cup-boosts-activity) (secondary).
- Crypto: no tradable edge vs Binance after fees (−0.116 units/trade) — [OpenMarket](https://arxiv.org/abs/2607.26245); settlement pushes earn $8.2M for 821 wallets in 5-min BTC — [arXiv 2606.31675](https://arxiv.org/html/2606.31675v1); two-sided MMs buying YES+NO in 94% of trades — [leolabs](https://leolabs.me/blog/pm-top20-strategy-patterns/en/) (UNVERIFIED). Crypto carries the highest taker fee (0.07).
- Economics/macro: Polymarket Fed markets "simply tracked CME pricing" (Good Judgment, interested vendor) — [Good Judgment](https://goodjudgment.com/testing-polymarkets-most-accurate-claim/). Blog claims Kalshi tighter on macro/Fed (UNVERIFIED) — [Pillar Lab AI](https://pillarlabai.com/blog/prediction-market-accuracy-rates-kalshi-polymarket/).
- Weather: only vendor material found. Claims: NWS gridpoint runs ~3°F high at MIA and ~1°F high at Central Park; a "67% verified win rate" product for $25/month; an open-source tool showing an 84.1% model probability vs $0.11 market price — all UNVERIFIED marketing — [weatheredge.it.com](https://weatheredge.it.com/); [Weather Edge MCP](https://mcpservers.org/pt-BR/servers/rjw34/weather-edge-mcp). Polymarket Weather longshot return flips sign across weightings (−25.15% equal / +24.74% pooled) — [arXiv 2609.12878](https://arxiv.org/html/2609.12878v1). Both venues show wider calibration error on weather/niche markets per a blog (UNVERIFIED) — [Pillar Lab AI](https://pillarlabai.com/blog/prediction-market-accuracy-rates-kalshi-polymarket/).
- Mention markets: "Yes Bias" rather than FLB; whales lose EV to small traders — [Deleep et al. via Quantpedia](https://quantpedia.com/how-wise-is-the-crowd-in-prediction-markets/).
- Geopolitics: fee-free on Polymarket; repeated informed-trading windfalls (Maduro $409,882 on $33,933; Iran ~$1M; Nobel ~$90K) — [PublicGaming](https://www.publicgaming.com/news-categories-m/igaming-mobile-sports-betting/15371-polymarket-user-nets-1108-return-after-maduro-capture-leads-to-insider-trading-questions); [The Block](https://www.theblock.co/post/391650/fresh-accounts-netted-1-million-on-polymarket-hours-before-us-airstrikes-on-iran-bubblemaps).

### Inferences
- Model-beatable, persistent, fee-surviving: (1) political favorites/underconfidence as a maker (edge ~1% pooled, fee 0 as maker, ~0.19% as taker at 95¢); (2) low-attention political races (Sethi) — tiny capacity. Both are slow, carry-like, and 2024-election-weighted.
- Not model-beatable after fees on current evidence: short-horizon crypto (Binance lead-lag), mention markets (artifact), high-liquidity presidential markets.
- Sports: the profits are real but come from liquidity provision/bots, not from the standard FLB; there is no published evidence that a sportsbook-consensus model beats Polymarket sports prices after the 0.05 taker fee, and the Polymarket sports longshot return is positive (+18% pooled), i.e. the opposite of the sportsbook FLB prior.
- Weather/economics: no credible evidence either way; all "edges" are vendor claims with no sample sizes.

### Gaps
- No academic study of weather or economics-category edge on Kalshi or Polymarket.
- No per-category after-fee backtest anywhere in the literature as of Oct 2026.
- Table tennis / niche sports markets (relevant to the repo) do not appear in any study found.
