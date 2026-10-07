# Proven Models and Trading Approaches with Out-of-Sample / CLV Evidence (Sports and Event Markets)

Evidence grades used below: **[A]** peer-reviewed or primary working paper read directly (full text or abstract); **[B]** primary preprint/working paper seen only via abstract or search summary; **[C]** secondary press/news summary of a primary source; **[V]** vendor, platform-produced, tout or affiliate content (treat as claims, not evidence). Research date: 2026-10-07. Two PDFs (Bürgi/Deng/Whelan Jan-2026 version; Elaad/Reade/Singleton) were read in full text; most other items are abstract-level.

## Q1. Which studies show models beating closing lines or exchange prices out of sample, with effect sizes, and do they survive vig/commission?

### Takeaway
Peer-reviewed evidence of a pure predictive model beating a sharp closing line after vig is thin and mostly small-sample or dated; the most credible "profitable" results exploit the market's own prices (cross-book outliers, decorrelation from the bookmaker) rather than out-predicting the close, and real-money winners at soft books get limited quickly.

### Cited Findings
- **Kaunitz et al. ("Beating the bookies with their own numbers", 2017) [C]:** strategy used the average of many bookmakers' odds as a "wisdom of crowd" fair price and bet outlier odds that paid better than that consensus; data ~10 years, ~half a million football matches, 32 bookmakers (Jan 2005–Jun 2015) — [MIT Technology Review](https://www.technologyreview.com/s/609168/the-secret-betting-strategy-that-beats-online-bookmakers/)
- Kaunitz results: theoretical 3.5% return over the 10-year backtest; real-money return of 8.5% over five months — [Cosmos](https://cosmosmagazine.com/mathematics/researchers-work-out-how-to-break-the-bookmakers). Bets were ~$50 about 30 times a week for up to five months — [Scottish Daily Express](https://www.scottishdailyexpress.co.uk/news/weird-news/boffins-devise-successful-bookie-bashing-27552631). Bookmakers stopped them betting once they noticed their success — [MIT Technology Review](https://www.technologyreview.com/s/609168/the-secret-betting-strategy-that-beats-online-bookmakers/); one outlet says multiple bookmakers banned them (unconfirmed from primary) — [Digit](https://www.digit.fyi/boffins-bookies-banhammer/)
- **Hubáček, Šourek & Železný (2019), International Journal of Forecasting 35(2):783–796 [B]:** CNN on player-level NBA stats, with the model deliberately trained to *reduce correlation with the bookmaker's published odds*, plus portfolio-theory bet allocation; produced systematically positive cumulative profit on NBA seasons 2007–2014 where alternative methods did not — [IDEAS/RePEc](https://ideas.repec.org/a/eee/intfor/v35y2019i2p783-796.html); [CTU page](https://ida.fel.cvut.cz/papers/hubacek2019exploiting.html)
- Hubáček's 2024 PhD thesis reiterates that decorrelation is an effective way to achieve profits even though the model "lags significantly behind bookmakers' predictions" in accuracy — [CTU thesis](https://dspace.cvut.cz/bitstream/handle/10467/114141/F3-D-2024-Hubacek-Ondrej-hubacek_thesis.pdf)
- **Walsh & Joshi (2024), Machine Learning with Applications [B]:** NBA; selecting models by calibration rather than accuracy gave average ROI +34.69% vs −35.17% (best case +36.93% vs +5.56%); betting test is a single (unnamed) season against "published odds" (abstract does not say closing odds; number of bets not stated) — [arXiv 2303.06021](https://arxiv.org/abs/2303.06021); [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S266682702400015X)
- NFL: opening vs closing lines showed no statistically significant difference in predictive value; a home-underdog strategy 2002–2011 won 53.5% vs 52.38% break-even, but effectiveness fell in later years — [arXiv 1211.4000](https://arxiv.org/pdf/1211.4000)
- A 2024 systematic review of ML in sports betting reports wide variation in out-of-sample accuracy; reported accuracies (e.g., 71.5% in one football study) are prediction accuracy, not betting profit — [arXiv 2410.21484](https://arxiv.org/pdf/2410.21484)
- **Tennis:** a survey of eleven pre-match tennis models found FiveThirtyEight-style Elo and the Bookmaker Consensus Model (BCM) performed best; aside from models drawing directly on betting-market information, no model had documented better log loss — [Harvard thesis](https://dash.harvard.edu/bitstreams/dc501d43-9be0-4c8a-8066-480bd5ff5be5/download)
- Weighted Elo (WElo; Angelini, Candila, De Angelis, EJOR) claims better Brier/log-loss and higher betting ROI than four competitor models across out-of-sample periods [B; ROI figures not verified] — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0377221721003234)
- A 2025 tennis GNN paper benchmarks against Elo, WElo, Bradley-Terry and Shin-de-vigged Pinnacle odds over an out-of-sample window from early 2022 to Oct 2025 (return results not seen) — [arXiv 2510.20454](https://arxiv.org/html/2510.20454v1)
- **In-play football:** a market-calibrated in-play model found Betfair "holds general dominance throughout the match", as the market incorporates information beyond goals and shots — [arXiv 2605.16066](https://arxiv.org/pdf/2605.16066)
- Practitioner consensus (non-peer-reviewed) is that CLV is the main live validation test and that a model that backtests well but fails to beat closing lines live is likely overfit [V] — [PropJuice blog](https://propjuice.ai/resources/blog/ai-sports-betting-guide); [SharkBetting](https://www.sharkbetting.com/blog/ai-vs-ml-sports-betting)

### Inferences
- Two replicable "edge" families have independent support: (1) **price-based** edges (bet stale/outlier prices relative to a sharp consensus — Kaunitz), and (2) **market-aware modelling** (Hubáček's decorrelation; Benter-style blending, Q5). Pure stand-alone predictive models rarely beat sharp closes in the published record.
- The Walsh & Joshi ROI numbers (+35%) are implausibly large for a liquid NBA moneyline market; with one season and unspecified odds type, treat as evidence that calibration matters for Kelly-style betting, not as an effect-size estimate.
- Kaunitz's real-money test is the best-documented case where vig-adjusted profit existed but was cut off by account limiting — directly relevant to why exchanges/DCMs matter.

### Gaps
- Could not verify in this session: Dixon-Coles / xG soccer models vs closing lines, Wunderlich & Memmert soccer studies, MLB/NFL totals and player-prop model studies with CLV. None surfaced with reliable OOS profit figures in searches; I did not include them from memory.
- No primary access to Kaunitz paper (figures are press-reported) or WElo ROI numbers.
- No peer-reviewed study found that reports CLV (rather than ROI) for a model across multiple seasons.

## Q2. Market efficiency by segment: main lines vs props vs niche/lower leagues vs early lines vs in-play — where are inefficiencies largest?

### Takeaway
At the aggregate level even lower-tier English football result markets cannot be shown inefficient (only margins are wider); individual bookmakers are inefficient relative to each other (the exploitable structure); props and niche markets are widely claimed softer but I found no peer-reviewed CLV study confirming it. In prediction markets, the clearest mispricings are at the extremes of price, at the start and end of a contract's life, and in multi-leg/product-type wrappers.

### Cited Findings
- **Elaad, Reade & Singleton (2020), Finance Research Letters [A, full text]:** 16,407 English matches (PL, Championship, League One, League Two), 2010/11–2017/18, 51 online bookmakers; no statistically significant evidence rejecting the EMH in any division; no favourite-longshot or outcome-type bias overall — [Reading DP 2019-10](https://www.reading.ac.uk/web/files/economics/emdp201910.pdf); [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1544612319306440)
- Same paper: mean overround rises down the pyramid — PL 4.8%, Championship 6.2%, League One 7.1%, League Two 7.2% — and outcomes are less predictable in lower divisions (bookmaker MSE 0.190 PL vs ~0.21 lower); implied commission fell 0.3–0.5 pts across divisions by 2017/18 — [Reading DP 2019-10](https://www.reading.ac.uk/web/files/economics/emdp201910.pdf)
- Same paper: individual bookmakers are *not* efficient — their odds do not fully use information in competitors' odds — but the magnitudes are "almost certainly tiny" — [Reading DP 2019-10](https://www.reading.ac.uk/web/files/economics/emdp201910.pdf)
- Pinnacle's closing line is the usual efficiency benchmark because of its "winners welcome", no-restriction policy — [Claremont thesis](https://scholarship.claremont.edu/cgi/viewcontent.cgi?article=5145&context=cmc_theses)
- Props are argued to be softer because books cap limits (less sharp money shapes the line) and pricing models are less refined, especially exotic props on secondary players [V; not peer-reviewed] — [Establish The Run](https://establishtherun.com/miller-why-prop-betting-is-profitable/); [Wizard of Odds](https://wizardofodds.com/article/player-props-understanding-the-math-behind-the-lines/)
- **Kalshi sports, Moshrefi (arXiv 2607.14430, Jul 2026) [B]:** 23 million Kalshi moneyline trades (NBA, NHL, MLB). Calibration is near-perfect mid-life but worst at both ends — pre-game and in the final minutes; in the last 10 minutes the calibration curve becomes step-like (Prelec curvature well above 1), interpreted as insurance demand by holders of losing positions — [arXiv abstract](https://arxiv.org/abs/2607.14430); [HTML](https://arxiv.org/html/2607.14430)
- Same paper: Kalshi cross-game parlays are priced above the product of leg prices, with overpricing growing with leg count, and it persists even when legs come from the well-calibrated TTE range (a parlay-stage markup) — [arXiv 2607.14430](https://arxiv.org/abs/2607.14430)
- Earlier "Yogi Berra bias" (Page 2012, InTrade): prices of losing teams in the final 15 minutes of sports events were too high — cited in [Bürgi, Deng & Whelan (UCD WP)](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- **Kalshi single-name vs broad markets, Bartlett & O'Hara (Apr 2026) [B]:** 41.6M Kalshi trades; single-name markets show larger informed price impact, spreads only modestly wider, yet market makers earn ~2x per contract, because traders overbet YES in markets that mostly settle NO — [Stanford Law](https://law.stanford.edu/publications/adverse-selection-in-prediction-markets-evidence-from-kalshi/); [NBER conf. version](https://conference.nber.org/conf_papers/f243677.pdf). Press summary: YES bought ~61% of the time in single-name markets but won ~32%; contracts at ~46% implied settled YES only 21% [C] — [Ingame](https://www.ingame.com/?p=49137)
- **Horse racing (Betfair):** a 2024 arXiv paper studies informational efficiency of UK horse racing via Betfair time series (results not extracted) — [arXiv 2402.02623](https://arxiv.org/pdf/2402.02623)

### Inferences
- Wider overrounds in lower leagues mean the hurdle rises at the same time as information quality falls; an edge there must exceed ~7% book margin (sportsbook) or be traded on an exchange.
- For Kalshi/Polymarket sports, the documented exploitable structures are (a) the pre-game and last-minutes calibration distortions, (b) parlay markups (sell/avoid parlays), and (c) YES-overbetting in single-name/novelty markets — all of which are *market-microstructure* edges rather than model edges.

### Gaps
- No peer-reviewed CLV study isolating player props, early-week lines, or newly listed markets was found.
- Elaad et al. tests aggregate efficiency of result markets only (not totals, Asian handicaps or props).
- Magnitudes (return per contract) for Moshrefi's near-expiry distortion and parlay markup were not available from the abstract.

## Q3. Prediction-market-specific research 2024–2026 (Kalshi, Polymarket): FLB, maker vs taker, near-expiry calibration, event types, LLM forecasting

### Takeaway
Kalshi has a robust favorite-longshot bias: cheap contracts lose >60%, contracts ≥70c earn small but significant positive post-fee returns, makers lose ~10% vs takers ~31%, and makers buying ≥50c earn ~+2.6% per contract (SD 33%); the bias appears to be diminishing over time. Polymarket evidence on FLB is mixed and category-dependent, with sports the clearest exception. LLM forecasters now roughly match superforecasters on general questions but do not beat liquid market prices on their own — though a model+market ensemble does.

### Cited Findings — Kalshi (Bürgi, Deng & Whelan, "Makers and Takers", UCD/GWU/CEPR, Jan 2026 version) [A, full text]
- Data: transaction-level Kalshi data from 2021 launch through ~April 2025; 313,972 Yes and No contract-price observations (156,986 traded Yes contracts, up to 11 daily observations from 10 days before close); 46,282 distinct Yes contracts; results not sensitive to cutting data at Dec 2024 or excluding sports — [GWU WP 2026-001](https://www2.gwu.edu/~forcpgm/2026-001.pdf); [SSRN 5502658](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5502658); [CEPR DP20631](https://cepr.org/publications/dp20631)
- Price distribution is barbell-shaped: 33.8% of observations in 1–10c and 33.8% in 90–99c — [GWU WP](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- Contracts costing <10c lose over 60% of money; statistically significant small positive returns above 50c, and significant positive *post-fee* returns above 70c. Average return on all Kalshi contracts ≈ −20% — [GWU WP](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- Worked illustration: a 5c contract winning 3% has −40% pre-fee return; a 95c contract winning 98% earns +3.1% pre-fee — [GWU WP](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- Maker vs taker: average return Makers −9.64%, Takers −31.46% (difference highly significant). Makers who buy contracts ≥50c earn +2.6% after commission (not annualized), with a return standard deviation of 33%. Contracts ≤10c are mainly bought by Takers (Makers' share 43.5% in 1–10c vs ~53% in 80–89c) — [GWU WP](https://www2.gwu.edu/~forcpgm/2026-001.pdf); Vox column rounds to takers −32%, makers −10% — [VoxEU](https://cepr.org/voxeu/columns/economics-kalshi-prediction-market)
- Taker fee imputed at ~1.77% of price for a 50c contract (vs 1.75% stated, due to round-up) — [GWU WP](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- FLB holds across categories (Financials, Climate & Weather, Crypto, etc.) and across volume and trade-size quintiles; the bias coefficient is smaller for politics and entertainment; "some evidence that the bias in prices is diminishing over time" — [GWU WP](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- Accuracy: mean absolute error of prices declines each day toward close, with a steep drop on the final day — [GWU WP](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- Model: modest disagreement plus a small tendency to overweight small probabilities (fitted overweighting parameter 0.06–0.12) reproduces the patterns; persistence attributed partly to riskiness (33% SD) — [GWU WP](https://www2.gwu.edu/~forcpgm/2026-001.pdf)

### Cited Findings — Kalshi microstructure and sports calibration
- Bartlett & O'Hara (2026): makers earn ~2x per contract in single-name vs broad-based markets; VPIN-measured one-sided order flow predicts maker losses in single-name markets (adverse-selection risk for makers) [B] — [Stanford Law](https://law.stanford.edu/publications/adverse-selection-in-prediction-markets-evidence-from-kalshi/); [NBER](https://conference.nber.org/conf_papers/f243677.pdf)
- Moshrefi (2026): near-expiry and pre-game miscalibration in NBA/NHL/MLB Kalshi moneylines; parlay overpricing (see Q2) [B] — [arXiv 2607.14430](https://arxiv.org/abs/2607.14430)
- Related 2026 preprints exist on domain-specific calibration dynamics ([arXiv 2602.19520](https://arxiv.org/pdf/2602.19520)) and Polymarket order-book microstructure ([arXiv 2604.24366](https://arxiv.org/pdf/2604.24366)) — not read.

### Cited Findings — Polymarket
- Reichenbach & Walther (SSRN, Dec 2025) [B]: prices closely track realized probabilities and slightly outperform bookmaker odds; overtrading of default/"Yes" options but no general longshot bias; fewer than one-third of traders profit; skill evidence — more profitable traders than chance, persistent profits; well-performing traders buy more favorites — [SSRN 5910522](https://papers.ssrn.com/sol3/Delivery.cfm/5910522.pdf?abstractid=5910522&mirid=1); [ResearchGate](https://www.researchgate.net/publication/398660802_Exploring_Decentralized_Prediction_Markets_Accuracy_Skill_and_Bias_on_Polymarket). (Trade counts differ across versions: 124M vs 478M.) ~70% of ~2M users have negative P&L; a separate study put losing traders at 84.1% [C] — [Casino.org](https://www.casino.org/news/84-of-polymarket-traders-arent-profitable/)
- Cardozo & Rivero-Wildemauwe (arXiv 2609.12878, 2026) [A-abstract+body]: ~588M trades, 2.48M wallets, Nov 2022–Mar 2026, $23.67B purchases. Pre-fee terminal returns: longshots (<10c) −6.30% equal child-market weight / −19.35% dollar-pooled (CI includes zero) / +4.09% equal parent-event weight; favorites (≥90c) +0.277% / +0.83% / +0.392% — [arXiv 2609.12878](https://arxiv.org/html/2609.12878v1)
- Same paper by category (equal-weight / pooled): Politics longshots −16.34% / −46.15%, favorites +1.04% / +1.42%; Crypto longshots −14.84% / −12.63%; **Sports is "the clearest counterexample": longshots +2.43% / +18.11%, favorites −0.23% / +0.14%**; Weather longshot sign flips (−25.15% / +24.74%). Heavy favorite-buyers earn *less* than other favorite buyers; no net-of-cost strategy tested — [arXiv 2609.12878](https://arxiv.org/html/2609.12878v1)
- Polymarket-v1 database paper reports a "favorite-longshot reversal" (low-probability tokens overpriced, high-probability underpriced in its framing) — [arXiv 2606.04217](https://arxiv.org/html/2606.04217v1)

### Cited Findings — event types (economics releases)
- Fed FEDS 2026-010 ("Kalshi and the Rise of Macro Markets"): Kalshi gives a statistically significant improvement over Bloomberg consensus for headline CPI; core CPI and unemployment errors are statistically similar to consensus; Kalshi median/mode had a perfect record the day before FOMC meetings, beating fed funds futures; caveats on risk premia and thin tails — [Federal Reserve FEDS 2026-010](https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf); [Gambling Insider summary](https://www.gamblinginsider.com/news/113897/fed-study-kalshi-economic-forecasting)
- Kalshi's own "Crisis Alpha" study claims 40.1% lower MAE than consensus for YoY CPI (Feb 2023–mid-2025) [V, platform-produced] — [Kalshi Research](https://kalshi.com/research/publications/crisis-alpha)

### Cited Findings — LLM forecasting vs markets
- AIA Forecaster (Bridgewater AIA Labs, arXiv 2511.07678): agentic news search + supervisor agent + statistical calibration. ForecastBench FB-7-21 Brier 0.1076 vs median superforecaster 0.1110 (statistically indistinguishable). FB-Market subset: AIA 0.0753 vs market alone 0.0965 vs superforecasters 0.0740. On the harder MarketLiquid benchmark AIA *underperforms* market consensus (0.1258 vs 0.1106), but an AIA + market ensemble outperforms consensus alone — [arXiv 2511.07678](https://arxiv.org/html/2511.07678v1)
- ForecastBench (Karger et al., ICLR 2025): dynamic benchmark incl. questions from Manifold, Metaculus, Polymarket, RAND — [arXiv 2409.19839](https://arxiv.org/pdf/2409.19839). FRI (Jul 2026) says several models are now statistically indistinguishable from superforecasters and one (Cassi AI) ranks above the superforecaster median on market questions — [FRI Substack](https://forecastingresearch.substack.com/p/ai-models-have-likely-reached-parity); Good Judgment (Apr 2026, commercially interested) says superforecasters lead on market questions 80.3 vs 75.8 for best AI — [Good Judgment](https://goodjudgment.substack.com/p/what-superforecasters-actually-said)
- KalshiBench (arXiv 2512.16030) evaluates LLM epistemic calibration on Kalshi questions (not read) — [arXiv 2512.16030](https://arxiv.org/pdf/2512.16030)

### Inferences
- The most robust DCM edge in the literature is **structural, not predictive**: provide liquidity (make) on the favorite side / sell overpriced longshots and YES-in-single-name markets, collect the behavioral surplus, and manage adverse selection (Bartlett & O'Hara's VPIN result) — net +2.6% per contract for Kalshi makers buying ≥50c over 2021–2025, with a decaying trend.
- Sports may be the *least* longshot-biased Polymarket category, so a generic "fade longshots" rule should not be applied to sports without category-specific testing; for Kalshi sports the actionable distortions are timing (pre-game, final minutes) and parlays.
- LLM forecasters should be used as a blend input with the market price, not as a stand-alone price-setter in liquid markets.

### Gaps
- Bürgi/Deng/Whelan sample ends ~April 2025; Kalshi's sports volume exploded after January 2025, so post-2025 maker returns are unknown.
- Bartlett & O'Hara: absolute per-contract maker returns not obtained.
- No study found on Kalshi weather or "mentions" markets specifically beyond category-level FLB regressions (weather included in Bürgi et al. Table 8). No reliable source found on Polymarket US (DCM) specifically.

## Q4. How professional syndicates and sharp bettors operate, and transfer to exchanges/DCMs

### Takeaway
Documented syndicate practice is "model + market": compute a fair price, blend it with the market, bet discrepancies at scale with Kelly-type sizing, and execute before prices move across many accounts; sportsbooks counter by tracking which accounts beat the closing line and limiting them. Exchanges and DCMs remove limiting but replace it with adverse selection and liquidity constraints.

### Cited Findings
- Benter (Hong Kong racing): his first model lost money until he realised true outcomes lay between his model and the public odds, and adjusted for it; model grew from 16 to 100+ factors; Kelly sizing; early drawdown of $120k of $150k [C] — [Casino.org](https://www.casino.org/blog/bill-benter/); [CDC Gaming](https://cdcgaming.com/commentary/syndicates-algorithms-and-beating-the-horses-in-hong-kong/)
- Hong Kong pools became "efficient" as more syndicates entered; tote rebates help the largest syndicates [C] — [CDC Gaming](https://cdcgaming.com/commentary/syndicates-algorithms-and-beating-the-horses-in-hong-kong/)
- Starlizard (Tony Bloom): aims to produce football odds more accurate than bookmakers and bets large on discrepancies, in liquid Asian markets where a 1–2% edge is scaled by turnover [C; commentary, partly "alleged"] — [The ESK analysis](https://theesk.org/2026/04/09/anthony-grant-bloom-analysis-of-starlizard-the-brighton-model-and-the-legal-challenges-to-professional-gambling-integrity/)
- Books track whether accounts consistently beat closing lines and co-ordinated bet timing; "the most important thing in the short term isn't winning a bet, but it's beating the number" [C/V] — [BetSmart](https://betsmart.beehiiv.com/p/sportsbooks-limit-winning-bettors); automated systems cut max stakes to a fraction of advertised limits [V] — [DeucesCracked](https://www.deucescracked.com/blog/sportsbook-account-limits-2026-why-winners-get-limited)
- Massachusetts Gaming Commission moved to draft a rule requiring operators to notify bettors when limited — [SBC Americas](https://sbcamericas.com/2025/09/30/mgc-data-limiting-bettors-ma/)
- Kaunitz researchers were stopped by bookmakers after real-money success — [MIT Technology Review](https://www.technologyreview.com/s/609168/the-secret-betting-strategy-that-beats-online-bookmakers/)
- Kalshi is licensed without stake limits (unlike PredictIt/IEM) — [Bürgi, Deng & Whelan](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- On Kalshi, informed one-sided flow predicts maker losses in single-name markets (adverse selection replaces limiting as the cost of being sharp's counterparty) — [Stanford Law](https://law.stanford.edu/publications/adverse-selection-in-prediction-markets-evidence-from-kalshi/)

### Inferences
- Transfer to DCMs: the sharp's toolkit (fair price, compare to market, size by Kelly) carries over, but with no limits the binding constraints become book depth, fees (~1.75% of price at 50c on Kalshi for takers), and being picked off when quoting. Sportsbook CLV remains a useful *validation* signal for a model even where accounts would be limited.
- "Market-steaming" (copying sharp-book moves into slower books) has an exchange analogue: trading Kalshi/Polymarket prices that lag a sharp sportsbook consensus — the Kaunitz/Elaad finding that individual books fail to incorporate competitors' prices is the academic basis.

### Gaps
- No peer-reviewed study of syndicate returns, Starlizard, or sportsbook limiting thresholds was found; Benter's own 1994 paper (primary) was not retrieved.
- Pinnacle / Joseph Buchdahl / Unabated material on CLV-to-ROI conversion was not retrieved in this session.

## Q5. Ensemble methods combining model with market price — evidence that blending helps

### Takeaway
The best-documented successes all combine model and market: Benter's model-plus-public-odds adjustment, Hubáček's decorrelated model, and AIA Forecaster's ensemble that beats market consensus where the model alone does not. No peer-reviewed tennis/soccer study testing a log-odds blend against exchange prices with OOS returns was found.

### Cited Findings
- Benter's profitable version combined his model with public odds after the stand-alone model lost money [C] — [Casino.org](https://www.casino.org/blog/bill-benter/)
- AIA Forecaster alone loses to market consensus on MarketLiquid (Brier 0.1258 vs 0.1106) but AIA + market ensemble beats consensus alone — [arXiv 2511.07678](https://arxiv.org/html/2511.07678v1)
- Hubáček et al.: profit came from a model regularised to *differ* from bookmaker odds, despite lower accuracy than the bookmaker — [IDEAS](https://ideas.repec.org/a/eee/intfor/v35y2019i2p783-796.html); [CTU thesis](https://dspace.cvut.cz/bitstream/handle/10467/114141/F3-D-2024-Hubacek-Ondrej-hubacek_thesis.pdf)
- Tennis: the Bookmaker Consensus Model is among the two best of eleven models; no non-market model has documented better log loss — [Harvard thesis](https://dash.harvard.edu/bitstreams/dc501d43-9be0-4c8a-8066-480bd5ff5be5/download)
- Kaunitz: consensus of many bookmakers used as the fair-probability estimate — [MIT Technology Review](https://www.technologyreview.com/s/609168/the-secret-betting-strategy-that-beats-online-bookmakers/)
- Moshrefi: using PM prices as probabilities requires conditioning on time-to-expiry and product type, not price alone — [arXiv 2607.14430](https://arxiv.org/abs/2607.14430)
- Fed paper caveat: risk premia distort implied probabilities, especially in thin tails — [Federal Reserve](https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf)

### Inferences
- A defensible design is: de-vig a sharp consensus, recalibrate the venue price by price bucket *and* time-to-expiry (FLB + Moshrefi), then blend with the model in logit space with weights fit only on past data; bet only when the blended price clears fees plus a buffer. The literature supports each component, though no single study tests this full pipeline.
- Expect a stand-alone model to be worse than the market; value comes from the model's *decorrelated* information.

### Gaps
- No peer-reviewed paper found that reports out-of-sample *betting returns* for an explicit log-odds blend of a sports model with Betfair/Kalshi/Polymarket prices.
- Optimal blend weights by sport/segment, and their decay over time, are undocumented in what I found.
