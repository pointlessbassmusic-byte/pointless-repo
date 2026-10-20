# Professional Market Making & Large-Capital US-Legal Operations in Prediction Markets / Sports Event Contracts (as of Oct 7, 2026)

Scope note: research conducted 2026-10-07 via web search/fetch (~20 tool calls). Many sources are trade press, vendor blogs or secondary summaries; primary CFTC filings (PDFs) for Polymarket US's LP program and Kalshi's perp rebate could not be text-extracted in this environment, so their detailed numeric terms are listed as gaps. "Facts" below are what a cited source says; "Inferences" are my reasoning and should be treated as speculation.

## 1. Which firms are market makers, and what is publicly known about their volumes, programs, agreements, fee/rebate terms and exemptions?

### Takeaway
Publicly confirmed institutional liquidity providers are Susquehanna (Kalshi's first designated MM since April 2024, and co-owner of Robinhood's Rothera exchange), Jump Trading (equity-for-liquidity with both Kalshi and Polymarket, Feb 2026), and, increasingly, sportsbook operators (Flutter/FanDuel, Crypto.com in-house, DraftKings affiliates) acting as affiliated market makers. DRW, Flow Traders and others are building desks. Kalshi has said institutional MMs are only about 7% or less of volume on its most liquid markets. Specific fee, rebate and limit terms in MM agreements are not public. I found NO evidence of any MM exemption from in-play delays.

### Cited Findings
**Susquehanna (SIG)**
- Kalshi announced Susquehanna Government Products as its "first dedicated institutional market maker" in April 2024 — [BusinessWire, Apr 3 2024](https://www.businesswire.com/news/home/20240403664852/en/Kalshi-Onboards-Its-First-Dedicated-Institutional-Market-Maker)
- In exchange for providing liquidity, Susquehanna received reduced fees and higher position limits on Kalshi (the only publicly described terms) — [Finance Magnates, Jan 14 2026 (citing FT)](https://www.financemagnates.com/fintech/wall-street-quants-move-into-prediction-markets-to-hunt-for-arbitrage-not-to-bet/)
- SIG is recruiting staff to "detect incorrect fair values" and find inefficiencies. Tradermath credits SIG with the first prediction-markets desk (2023) — [Finance Magnates](https://www.financemagnates.com/fintech/wall-street-quants-move-into-prediction-markets-to-hunt-for-arbitrage-not-to-bet/); [Tradermath](https://www.tradermath.org/articles/prediction-markets-trading-at-quant-firms)
- **Rothera JV (Robinhood + SIG).** Robinhood's 10-K says Rothera was established Nov 19, 2025 to operate "an independent and institutional-grade futures and derivatives exchange and clearinghouse." Rothera acquired 90% of Rothera E&C (formerly MIAXdx / LedgerX), a CFTC-licensed DCM, DCO and SEF, in Jan 2026. Robinhood says it does "not wholly own or operationally control Rothera" — [Robinhood 10-K/A FY2025](https://www.sec.gov/Archives/edgar/data/1783879/000178387926000029/hood-20251231.htm); [Robinhood 10-Q Q1 2026](https://www.sec.gov/Archives/edgar/data/0001783879/000178387926000062/hood-20260331.htm); [Yahoo Finance](https://finance.yahoo.com/markets/options/articles/rothera-robinhood-prediction-market-empire-125229625.html)
- Robinhood Derivatives LLC routes event contracts to KalshiEX, ForecastEx and Rothera. At a May 2026 conference the CFO said to expect "in the near to medium term, that we migrate our flow over there" (to Rothera) — [Yahoo Finance](https://finance.yahoo.com/markets/options/articles/robinhoods-prediction-market-push-why-130400265.html); [Next Event Horizon](https://nexteventhorizon.substack.com/p/robinhood-cfo-says-most-prediction-flow-moving-to-rothera)
- Robinhood traded 13.6B event contracts in Q2 2026, about $156M of revenue from the segment — [Yahoo Finance](https://finance.yahoo.com/markets/options/articles/rothera-robinhood-prediction-market-empire-125229625.html)
- Rothera passed 5B contracts traded in September 2026, less than four months after launch, and added college basketball — [PMP Weekly Oct 5 2026](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- The SIG/Robinhood ownership split inside the JV is not reported (see Gaps).

**Jump Trading**
- Bloomberg (Feb 9 2026) reported that Jump will provide market-making services to Kalshi and Polymarket in exchange for equity. It receives "a set amount of equity" in Kalshi, and its Polymarket stake can grow over time. Terms were "vague" and sourced anonymously. Valuations at the time were Kalshi $11B and Polymarket $9B — [The Block](https://www.theblock.co/post/389086/jump-trading-to-serve-as-polymarket-and-kalshi-market-maker-in-exchange-for-stake-bloomberg); [DeFi Rate](https://defirate.com/news/equity-for-liquidity-jump-trading-set-to-take-stakes-in-kalshi-and-polymarket/)
- Jump reportedly roughly doubled its prediction-markets team to about 20 people in 2026. Crypto Briefing links Jump to the first institutional block-trade liquidity on Kalshi (May 2026). Both are secondary sources — [Tradermath](https://www.tradermath.org/articles/prediction-markets-trading-at-quant-firms); [Crypto Briefing](https://cryptobriefing.com/jump-trading-prediction-markets-wall-street/)

**Other prop and quant firms**
- DRW is hiring for a dedicated prediction-markets desk, with base salaries up to $200k. Flow Traders, Kirin, Anti Capital and Sfermion are increasing activity. Saba's Boaz Weinstein describes event contracts as a hedging tool — [Finance Magnates/FT](https://www.financemagnates.com/fintech/wall-street-quants-move-into-prediction-markets-to-hunt-for-arbitrage-not-to-bet/)
- Raven, a market maker, raised at a $58.2M pre-money valuation (led by Coinbase Ventures) and says it has quoted more than 3,000 contracts — [PMP Weekly Oct 5 2026](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- **Jane Street, Citadel Securities, Wintermute:** none of my searches found a reliable source confirming a US prediction-market MM role. The only Jane Street mention was an unsourced X post — [X/Laika AI](https://x.com/Laika_ai/status/2064662950141423972)
- A Kalshi spokesperson said institutional MMs are "about 7 percent or lower" of volume on its most liquid markets — [search summary of Kalshi spokesperson quote, via bookmakersreview/FairPredicts coverage](https://www.bookmakersreview.com/industry/kalshi-trading-strategy/) (secondary, could not verify the original)
- CNBC (Aug 19 2026): "Hedge funds are about to jump in big to prediction markets." The page returned 403, so its contents are unverified — [CNBC](https://www.cnbc.com/2026/08/19/hedge-funds-are-about-to-jump-in-big-to-prediction-markets.html)

**Sportsbook / exchange-affiliated market makers**
- **FanDuel Predicts** is a FanDuel–CME JV. CME paid a reported $10.2M for a 51% stake. FanDuel's trading arm is the affiliated MM. CME CEO Terry Duffy has said it is problematic for companies to use affiliated MMs on exchanges in which they have a financial interest — [Sportico](https://www.sportico.com/business/sports-betting/2026/fanduel-cme-group-prediction-markets-sports-terry-duffy-1234939913/)
- In August 2026 FanDuel moved all sports and novelty contracts from CME to Crypto.com (CDNA/OG). CME keeps financial contracts. Sports contracts are offered only where FanDuel does not run a sportsbook — [Bloomberg Aug 5 2026](https://www.bloomberg.com/news/articles/2026-08-05/cme-fanduel-venture-falters-as-prediction-startups-race-ahead); [DeFi Rate](https://defirate.com/news/flutter-moves-fanduel-predicts-sports-contracts-crypto-com/); [Bitcoin.com](https://news.bitcoin.com/igaming/cme-keeps-51-of-fanduel-predicts-but-loses-its-sports-business/)
- Flutter's market-making made $6M of revenue in Q2 2026. It expects about $50M of revenue and adjusted EBITDA from market making in 2026 and declines to name the venues — [DeFi Rate](https://defirate.com/news/flutter-moves-fanduel-predicts-sports-contracts-crypto-com/)
- Flutter is reportedly about 15% of Kalshi's combo volume, per JPMorgan analysts. Its full-year prediction-market spend is now expected near $200M (previously $300M), offset by about $50M of MM EBITDA. Flutter is also a market maker on Crypto.com — [PMP Weekly Oct 5 2026](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- Crypto.com built an in-house market-making team for its North American derivatives business. The company says it supports liquidity rather than acting as a profit-driven prop arm — [FinanceFeeds](https://financefeeds.com/crypto-com-builds-in-house-market-maker-as-prediction-markets-expand/)
- **DraftKings / DKeX.** DraftKings bought Railbird (a CFTC-licensed DCM) for a reported $50M upfront plus up to $200M in incentives, and launched DKeX in June 2026. Railbird filed an MM program; its public version redacts which markets get incentives and the amounts — [BusinessWire Jun 24 2026](https://www.businesswire.com/news/home/20260624009284/en/DraftKings-Launches-Proprietary-Exchange-to-Bolster-Differentiated-Predictions-Experience); [Gambling Insider](https://www.gamblinginsider.com/news/168100/citizens-railbird-market-making-draftkings-predictions)
- DKeX certified COMBOS as a swap class. Its combo MMs reportedly "no longer need rebates" — [PMP Weekly Oct 5 2026](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- Citizens analysts call market making "the most attractive layer" and estimate that third-party MM activity on DraftKings could be worth about $184M EBITDA by 2030 — [Gambling Insider](https://www.gamblinginsider.com/news/168100/citizens-railbird-market-making-draftkings-predictions)

**In-play delay exemptions**
- I found no exchange rulebook, filing or credible report of a MM exemption from in-play delays or speed bumps on Kalshi, Polymarket US, CDNA, DKeX or Rothera. One broker (Sportmarket) says bets routed to Kalshi/Polymarket are accepted "instantly, without the usual sportsbook delay," which suggests the exchanges may not run a sportsbook-style bet delay at all — [howprosbet.com](https://howprosbet.com/prediction-markets-on-sportmarket/)

### Inferences
- The dominant 2026 model is "equity/affiliation for liquidity." Jump gets equity, SIG owns the Rothera exchange, and Flutter, Crypto.com and DraftKings run affiliated or in-house MMs. A new pure-prop entrant competes against firms that also capture exchange economics. Expect sharpest competition in high-volume sports moneylines and less in long-tail markets, props and combos/RFQ.
- Combos/parlays (RFQ) look like the current high-margin battleground. Kalshi combo volume was about $2B a day on record days, Flutter is about 15% of it, and DKeX combo MMs no longer need rebates. Those last two facts point to combo quoting being profitable without subsidy.
- Since no in-play delay exemption has been found, latency (feed speed plus cancel speed) is likely the decisive in-play edge, not a regulatory privilege. This is speculation.

### Gaps
- Exact fee rebates, position-limit multiples and quoting obligations in SIG's, Jump's or any MM's agreement are not public.
- Equity split of the Rothera JV between SIG and Robinhood.
- Whether Jane Street, Citadel Securities, Wintermute or DRW currently quote on any US DCM (only hiring or rumor evidence).
- No verified MM volume share by firm. Only Kalshi's "about 7% or lower" institutional-MM claim, and Flutter's about 15% of Kalshi combos.
- CNBC Aug 2026 hedge-fund article not readable (403).

## 2. Exchange market-maker programs: requirements, benefits, and how to apply

### Takeaway
Every major US DCM now runs a designated-MM program plus subsidy programs (stipends, volume incentives, taker-fee rebate shares), and the 2026 trend is to roll back blanket volume incentives in favor of targeted DMM and series-based programs. Kalshi's MM program is application-only (financial resources, experience, reputation review) with a 98%-per-hour uptime obligation on listed products, in exchange for reduced fees and higher position limits. Polymarket US runs a Market Maker Program (apply via institutional@qcex.com), a stipend-based Liquidity Provider Program, and a volume-share Market Incentive Program.

### Cited Findings
**Kalshi**
- MMs are designated participants providing "consistent, two-sided liquidity," approved after review of "financial resources, trading experience, and business reputation." In exchange for meeting quoting and volume requirements they "may receive reduced fees and certain adjusted position limits." Listed products include crypto (KXBTC, KXETH), indices (KXINX, KXNASDAQ100), sports (KXNBA, KXNFLGAME) and econ (KXCPI, KXFED), each with availability of "98% of each 1h increment." FCM partners listed: Webull, Robinhood Derivatives, Direct Access USA — [Kalshi Help Center](https://help.kalshi.com/en/articles/13823819-how-to-become-a-market-maker-on-kalshi)
- The help page gives no spread or depth thresholds, fee numbers, rate limits, FIX details or application contact. Secondary sources say the program is invitation-based or "highly selective," and that a Liquidity Provider Program is gated behind a signed MM Agreement with rewards allocated by auction — [kalshibacktest.com](https://kalshibacktest.com/resources/what-is-kalshi-market-maker); [Turbinefi](https://www.turbinefi.com/blog/how-to-market-make-prediction-markets-2026)
- Kalshi fee-rebate programs exclude members who signed a Market Maker Agreement or FCM Agreement, and Fee Rebate participants cannot also use the Volume Incentive Program — [Kalshi CFTC filing Jan 2025](https://www.cftc.gov/filings/orgrules/rules01132513688.pdf); [Kalshi CFTC filing Jun 2024](https://www.cftc.gov/sites/default/files/filings/orgrules/24/06/rules0627242994.pdf)
- Kalshi is ending its Volume Incentive Program no earlier than Oct 13, 2026, about a year before its filed Oct 1, 2027 end date. The program had capped payouts at half a cent per contract — [PMP Weekly Oct 5 2026](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- **Kalshi taker fee:** roundup(0.07 × C × P × (1−P)), with a peak of $0.0175 per contract at 50¢. Maker fees are reportedly 25% of the taker rate on sports and other "exception" series and zero elsewhere. Sources conflict, so check the live schedule — [whirligigbear](https://whirligigbear.substack.com/p/makertaker-math-on-kalshi); [Allium](https://allium.so/blog/kalshi-fees-why-the-cost-peaks-near-50-cents/); [DeFi Rate](https://defirate.com/prediction-markets/fees/)
- Kalshi also filed a "Temporary Perp Fee Rebate Program" (CFTC, June 24 2026) limited to self-clearing members, reportedly through Dec 31, 2026. I could not extract the full text — [CFTC filing](https://cftc.gov/filings/orgrules/rules0625267305.pdf)

**Polymarket US (QCX LLC / QC Clearing; acquired via QCEX for $112M)**
- Market Maker Program: "rewards approved market makers for providing liquidity across a wide range of contracts in given categories." Apply via institutional@qcex.com — [Polymarket US docs](https://docs.polymarket.us/incentives/market-maker)
- Effective Oct 15, 2026, the 2026 MM Program was amended into a series-based program. Rebates are a share of taker fees on passive fills, tiered by monthly maker share, with a minimum of 5% of series maker notional and a monthly cap — [PMP Weekly Oct 5 2026](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- Liquidity Provider Program (filed Mar 3, effective Mar 17 2026): a fixed weekly stipend per opted-in Series for continuous two-sided quotes on assigned instruments, adjusted for uptime. Approval is at Polymarket US's "sole discretion." DMM-agreement holders, Market Maker Program participants and FCM/IB customers are excluded — [CFTC filing](https://www.cftc.gov/sites/default/files/filings/orgrules/26/03/rules03032640252.pdf); [Polymarket Exchange notice](https://www.polymarketexchange.com/files/notices/Liquidity%20Provider%20Program%20(2026.03.03).pdf)
- Market Incentive Program (effective Mar 19 2026): pro-rata incentives by share of executed volume in designated markets — [Polymarket Exchange notice](https://polymarketexchange.com/files/notices/Market%20Incentive%20Program%20(2026.03.05).pdf)
- Polymarket acquired QCEX (DCM + DCO) for $112M — [PR Newswire](https://www.prnewswire.com/news-releases/polymarket-acquires-cftc-licensed-exchange-and-clearinghouse-qcex-for-112-million-302509626.html)
- Polymarket US fees: makers pay 0%, and takers pay by category. A schedule effective Jul 1, 2026 reportedly sets sports at 5% × p(1−p), or $1.25 per 100 contracts at 50¢. Earlier sources and the global docs say 3% for sports. Volume rebates apply above $250k monthly taker volume — [River Markets](https://www.rivermarkets.com/insights/polymarket-us-fees.html); [Polymarket docs](https://docs.polymarket.com/trading/fees?via=xp)
- Polymarket hired former Goldman Sachs partner Lisa Mantil to lead institutional growth (Sept/Oct 2026) — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)

**Other venues**
- OG.com / Crypto.com (NADEX, d/b/a CDNA) certified a Sports Event Contract Market Maker Incentive Program effective Oct 16, 2026 to Feb 14, 2027, with DMM and combo components. Payout terms are redacted — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- North American Derivatives Exchange operates as both OG Prediction Markets and Crypto.com Derivatives North America and is a CFTC DCM and DCO — [Bitcoin.com](https://news.bitcoin.com/igaming/cme-keeps-51-of-fanduel-predicts-but-loses-its-sports-business/); [FanDuel](https://www.fanduel.com/about/news/fanduel-predicts-to-expand-event-contract-offering-through-partnership-with-crypto-com-and-og-prediction-markets)
- ForecastEx (Interactive Brokers) created an Elections Liquidity Retainer Program (effective Oct 13, 2026) paying at most three MMs for two-sided quotes on up to 21 election products — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- Railbird/DKeX filed an MM program, with incentive details redacted in the public version — [Gambling Insider](https://www.gamblinginsider.com/news/168100/citizens-railbird-market-making-draftkings-predictions)

### Inferences
- How to apply in practice: Kalshi goes through its institutional team or the help center (no public email found). Polymarket US goes through institutional@qcex.com. Crypto.com/OG, DKeX, Rothera and ForecastEx DMM programs are certified with the CFTC, so counterparty contact likely runs through each exchange's institutional desk. Program terms are visible (partly redacted) in CFTC "orgrules" self-certification filings, which are the best primary-source monitor.
- Excluding MMs from public rebate programs means a new entrant has to choose between the generic fee-rebate/volume programs (open to anyone, but being wound down at Kalshi) and a negotiated MM agreement (reduced fees, higher limits, obligations).
- Benefits typical in TradFi DMM programs (higher API rate limits, FIX access, co-location) are plausible but not confirmed in any source I found for these venues. This is speculation.

### Gaps
- Exact spread, depth and size obligations, stipend dollar amounts, rate-limit tiers and FIX availability for any program. The Kalshi help page is silent, and the Polymarket LP PDF could not be text-extracted here; it is readable at the CFTC link.
- Rothera's MM program terms (none found).
- Whether any program offers queue or priority advantages. The Polymarket LP filing reportedly says it does not confer execution priority, but I could not verify the text.

## 3. Regulatory structures for a larger operation (entity, CFTC registration, position limits, state litigation)

### Takeaway
Proprietary trading for one's own account on a DCM generally does not require CFTC registration. Large operations typically trade through an LLC, either as a direct exchange member (Kalshi self-clearing, fully collateralized) or as an FCM customer (Robinhood, Webull etc.). This is based on general CFTC framework knowledge and was NOT confirmed by a source in this research. Kalshi position limits are per-contract and can be raised (MM agreements, or e.g. $7M on CPI). The overriding 2026 risk is the federal-state preemption fight over sports contracts, which has produced a circuit split and a pending SCOTUS cert petition (No. 26-299).

### Cited Findings
- Some Kalshi markets carry $25,000 position limits per a broker review. Kalshi raised its monthly inflation contract limit to $7M — [Investing.com review](https://investing.com/brokers/reviews/kalshi); [FA Mag](https://www.fa-mag.com/news/the-startup-that-lets-hedge-funds-bet-millions-on-real-life-events-72915.html)
- MMs receive "certain adjusted position limits" — [Kalshi Help Center](https://help.kalshi.com/en/articles/13823819-how-to-become-a-market-maker-on-kalshi)
- **Third Circuit (Apr 2026)**, 2–1 for Kalshi: federal law preempts NJ sports-gambling regulation because the contracts are swaps under exclusive CFTC jurisdiction — [Skadden](https://www.skadden.com/insights/publications/2026/04/third-circuit-affirms-kalshis-preliminary-injunction)
- **Ninth Circuit (Aug 28 2026)**, unanimous against Kalshi in Nevada: Kalshi had not shown CEA preemption. An en banc petition is pending. **Ninth Circuit (Sep 16 2026)**, *Blue Lake Rancheria v. Kalshi*: tribes are likely to succeed under IGRA — [Holland & Knight](https://www.hklaw.com/en/insights/publications/2026/08/ninth-circuit-upholds-state-and-tribal-authority-over-sports-related); [Buchalter](https://www.buchalter.com/insights/ninth-circuit-kalshis-sports-contracts-are-class-iii-gaming-on-tribal-land-and-tribes-can-sue-to-stop-them/); [Covers](https://www.covers.com/industry/ninth-circuit-rehearing-prediction-market-supreme-court-timeline-september-16-2026)
- **Sixth Circuit (Sep 2026):** Ohio and Tennessee can enforce gambling laws, and the CEA would not preempt even if the contracts are swaps. Ohio is ramping enforcement — [PYMNTS](https://www.pymnts.com/legal/2026/a-second-federal-appeals-court-rules-sports-event-contracts-are-not-swaps/); [SBC Americas Oct 5 2026](https://sbcamericas.com/2026/10/05/ohio-prediction-markets-crackdown-win)
- **Fourth Circuit (Maryland):** argued May 2026, decision pending — [Prediction News](https://predictionnews.com/news/kalshi-sports-contract-appeal-fourth-circuit-maryland-case)
- **SCOTUS:** New Jersey filed a cert petition Sep 2, 2026, docketed No. 26-299. Kalshi's response is due Nov 9. Gaming regulators filed a brief urging review (Oct 7 2026) — [SiaPredict](https://siapredict.com/blog/kalshi-supreme-court-flaherty-docket-26-299); [SBC Americas Oct 7 2026](https://sbcamericas.com/2026/10/07/kalshi-supreme-court-regulator-brief/)
- **Illinois (Oct 2 2026):** Judge Pacold partly granted preliminary injunctions (Coinbase, Kalshi, and US/CFTC cases), blocking Illinois from applying sports-wagering licensing and criminal provisions to Kalshi-listed contracts as likely swaps. The challenge to Illinois transaction fees (1.75% on the first $5M, 3.5% above) was not enjoined. Judge Griesbach in Wisconsin (July) went the other way and that case is on appeal to the 7th Circuit — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- **CFTC (Sep 28 2026):** a proposed rule to include event contracts in "swap" and an interim final rule excluding casino-style gambling products were sent to the White House; texts are not public. Coinbase Clearing and Quanta Clear were registered as DCOs (fully collateralized) — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- The NY Attorney General has sued Polymarket — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- FanDuel Predicts offers sports contracts only in states where FanDuel has no sportsbook — [Bitcoin.com (ES)](https://news.bitcoin.com/es/cme-conserva-el-51-de-fanduel-predicts-pero-pierde-su-negocio-deportivo/)

### Inferences
- For a well-capitalized entrant: form a US LLC (or LP plus GP), onboard KYB directly with each DCM as a member (Kalshi, Polymarket US, CDNA/OG, Rothera, DKeX). Some DCMs only accept retail-style or FCM-intermediated access, which matters for program eligibility, since FCM customers are excluded from several rebate and stipend programs. CFTC registration (CPO/CTA) becomes relevant only if managing outside money. Prop trading of own capital likely needs none, but confirm with derivatives counsel (no source found).
- State-law risk to the trader (vs the exchange) appears low in the litigation so far, which targets exchanges and FCMs. The practical risk is venue or geofence shutdowns in specific states (NV, OH, TN, MD and tribal lands), which shrink the addressable flow and can strand open positions. A SCOTUS grant in 2026–27 is a binary event for the whole sports-contract business. Speculation.
- Venue concentration risk: Kalshi's sports business is the most litigated. Rothera (SIG/Robinhood) and CDNA (Crypto.com) face the same legal question.

### Gaps
- No source found on CFTC registration requirements for proprietary event-contract traders, or on large-trader reporting thresholds (CFTC Part 17) as applied to event contracts.
- Published position-limit schedules for sports contracts on each venue were not retrieved.
- Whether exchanges geofence individual traders by state or only retail accounts (e.g., whether an LLC in NJ can trade while a NV resident cannot).

## 4. Hedging across venues and with sportsbooks; settlement-risk differences

### Takeaway
Cross-venue hedging and arbitrage is a core quant strategy (siloed, fragmented liquidity). Exchanges now actively court sportsbook hedging: Kalshi's Feb 2026 Sportsbook Hedging Rebate Program rebates taker and RFQ fees for sportsbooks buying 300k or more contracts in an order, and Game Point Capital plans about $30M a year of hedging. I found no source on retail-style sportsbook account limiting of hedgers, or on legal restrictions on individuals hedging between sportsbooks and DCMs.

### Cited Findings
- Kalshi's Sportsbook Hedging Rebate Program (CFTC filing Feb 2026) is for "entities that currently offer sportsbook services" buying at least 300,000 contracts for hedging. It rebates taker and RFQ fees, effective about Feb 23–24 2026 until the earlier of Feb 1, 2027 or termination. Sources disagree on whether the 300k threshold is per order or per month — [CFTC filing](https://www.cftc.gov/sites/default/files/filings/orgrules/26/02/rules02072638946.pdf); [DeFi Rate](https://defirate.com/news/kalshi-files-sportsbook-hedging-program-partners-with-game-point-capital/); [ReadWrite](https://readwrite.com/kalshi-fee-rebates-sportsbooks-risk)
- Kalshi partnered with Game Point Capital (sports insurance), which expects to hedge about $30M a year through Kalshi. Underdog has used Kalshi for layoffs. Kalshi also has an "Insurance Hedging Rebate Program" (Jan 2026) — [The Block](https://www.theblock.co/post/389841/kalshi-inks-sports-hedging-deal-with-game-point-on-the-heels-of-over-1-billion-in-super-bowl-trading); [CFTC filing Jan 2026](https://www.cftc.gov/sites/default/files/filings/orgrules/26/01/rules01302638513.pdf)
- "Platforms are still siloed and liquidity is fragmented, arbitrage opportunities are everywhere" (Joseph Saluzzi, Themis Trading, Jan 2026) — [Finance Magnates](https://www.financemagnates.com/fintech/wall-street-quants-move-into-prediction-markets-to-hunt-for-arbitrage-not-to-bet/)
- Brokers like Sportmarket route bets to Kalshi/Polymarket with instant acceptance, and the bets stay valid even after in-play information changes — [howprosbet](https://howprosbet.com/prediction-markets-on-sportmarket/)

### Inferences
- Settlement-risk differences (inferred from venue structure, not sourced): each DCM uses its own DCO, fully collateralized, so there is no counterparty default risk but capital is locked per venue. Cross-venue arbitrage needs separate funded accounts, which multiplies capital needs. Resolution-rule mismatches between venues (overtime, postponement, voids, player-prop DNPs) are the main basis risk. The arb-scanner in this repo already treats large cross-platform "edges" as likely `suspect_match`.
- Hedging against US sportsbooks is legal for bettors in their states, but sportsbooks routinely limit sharp or arbitrage accounts, so sportsbook-leg capacity is unreliable at scale. Speculation; no source found in this pass.
- Sportsbook layoff flow (Game Point, operator hedging) is large, informed-ish but price-insensitive flow. Quoting into it (RFQ for parlays and combos) is plausibly a profit pool for MMs.

### Gaps
- Sportsbook account limiting/"bet restrictions" data for hedgers (none found).
- Documented resolution-rule discrepancies between Kalshi, Polymarket US and CDNA for identical sports events.
- Capital efficiency: whether any venue offers portfolio margining or cross-margining (all appear fully collateralized; Coinbase Clearing and Quanta Clear are limited to fully collateralized).

## 5. Economics: market-maker margins and competition intensity (2025–2026)

### Takeaway
Academic evidence from Kalshi says makers beat takers. Bürgi/Deng/Whelan find takers lose about 32% on average versus makers' about 10% (both negative on average, driven by the favorite–longshot bias). Bartlett & O'Hara (2026), on 41.6M trades, find MMs earn about twice as much per contract in single-name markets despite greater adverse selection, because traders overbet YES on markets that mostly settle NO. Industry volume has exploded (more than $290B trailing-52-week; about 85% sports), while exchanges are cutting blanket incentives, pointing to rising MM competition and profitability without subsidy in the deepest markets.

### Cited Findings
- **Bartlett & O'Hara**, "Adverse Selection in Prediction Markets: Evidence from Kalshi" (SSRN 6615739, Apr 16 2026, rev. May): 41.6M trades. Kyle's λ and Glosten–Harris show more informed price impact in single-name than broad-based markets. Effective spreads are only modestly wider, but MMs earn about 2x per contract. YES-overbetting in markets that mostly settle NO creates a surplus that offsets adverse selection. A VPIN-style toxicity measure predicts maker losses in single-name markets only — [SSRN](https://papers.ssrn.com/sol3/Delivery.cfm/6615739.pdf?abstractid=6615739&mirid=1); [Stanford Law](https://law.stanford.edu/publications/adverse-selection-in-prediction-markets-evidence-from-kalshi/). (Note: the task described this as "Bartlett 2026 Stanford"; the co-author is Maureen O'Hara. A ResearchGate listing with the same title has an unrelated abstract.)
- **Bürgi, Deng & Whelan** (UCD WP2025_19; CEPR DP20631; 2026 versions "Makers and Takers"/"Makers or Takers"): over 300,000 contracts. Prices are informative and grow more accurate near resolution, with a favorite–longshot bias: contracts under 10¢ lose heavily, and high-priced contracts earn small positive returns. Takers lose about 32% on average versus makers about 10%. The early paper notes no institutionalized market makers driving spreads in its sample — [VoxEU/CEPR](https://cepr.org/voxeu/columns/economics-kalshi-prediction-market); [UCD WP](https://www.ucd.ie/economics/t4media/WP2025_19.pdf); [GWU 2026 version](https://www2.gwu.edu/~forcpgm/2026-001.pdf); [ifo/CESifo](https://www.ifo.de/en/cesifo/publications/2026/working-paper/makers-and-takers-economics-kalshi-prediction-market)
- **Volumes:**
  - US prediction-market monthly volume went from under $100M in early 2024 to over $8B in Dec 2025 — [Finance Magnates](https://www.financemagnates.com/fintech/wall-street-quants-move-into-prediction-markets-to-hunt-for-arbitrage-not-to-bet/)
  - More than $20B traded in one week on US-regulated markets, more than $290B trailing 52 weeks, about 85% sports — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
  - About $53B a month — [Forbes Oct 6 2026](https://forbes.com/sites/daraabasiita/2026/10/06/prediction-markets-hit-53-billion-a-month-courts-cant-agree-if-theyre-derivatives-or-bets)
  - Kalshi record days of $3.03B (Sep 26–27 2026), of which about $2B was combos. Sunday fee revenue was $18.3M — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
  - Novig: $2.19B since Aug 4, with a $36.6M daily average — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)
- **MM profitability data points:** Flutter expects about $50M of MM EBITDA in 2026, after $6M of Q2 revenue — [DeFi Rate](https://defirate.com/news/flutter-moves-fanduel-predicts-sports-contracts-crypto-com/). Citizens projects about $184M of third-party MM EBITDA on DraftKings by 2030 — [Gambling Insider](https://www.gamblinginsider.com/news/168100/citizens-railbird-market-making-draftkings-predictions)
- **Competition and incentive trend:** Kalshi is ending its Volume Incentive Program early (Oct 2026). Polymarket US moved to series-based taker-fee-share rebates. DKeX combo MMs no longer need rebates — [PMP Weekly](https://predictionmarketpulse.substack.com/p/pmp-weekly-october-5-2026)

### Inferences
- Taker fees are the main friction for liquidity-takers. Kalshi's peak taker fee is 1.75¢ per contract at 50¢, and Polymarket US sports is about 1.25¢ at 50¢ under the 5% schedule. That makes passive (maker) strategies structurally favored, consistent with the papers. An MM's gross edge combines the spread, behavioral flow (longshot/YES overbetting) and any program rebates. In single-name and in-play markets, adverse selection from informed or faster traders is the main cost.
- Competition in the most liquid sports moneylines (NFL, NBA, MLB) is likely intense in late 2026 (Jump, SIG, Flutter, Crypto.com in-house, Raven and others). Per-contract margins there are likely compressing toward fee-plus-adverse-selection breakeven. Less-contested pools include props, combos/RFQ, niche sports and new venues (Rothera, DKeX, Novig) during their liquidity-subsidy phase. This is speculation, since no quantitative spread time series was found.
- An unconstrained-capital entrant would need:
  - multi-venue membership and capital in each DCO
  - low-latency official data feeds (in-play), with sub-10ms targets cited by vendors but unverified
  - pricing models seeded from sharp sportsbook lines
  - an RFQ/combo pricing engine
  - negotiated DMM agreements for fees and limits
  - legal/compliance staff
  - optionally, an equity-for-liquidity deal with a newer venue, following Jump's template

### Gaps
- No published estimate of net margin per contract or per $ of volume for professional MMs on these venues, beyond the papers' maker-return averages, which cover all makers rather than pros.
- No time series of quoted spreads or depth for 2025–2026 showing compression (Jon-Becker's open dataset could be used: [GitHub](https://github.com/jon-becker/prediction-market-analysis)).
- Bürgi/Deng/Whelan's exact final-version figures were not verified from the full PDF (binary not parseable here).

## 6. US tax treatment of event-contract gains; 1099 reporting

### Takeaway
Unresolved. The IRS had issued no prediction-market guidance as of mid-2026. Candidate treatments are Section 1256 (60/40, mark-to-market, Form 6781), ordinary capital gain/loss, ordinary income, or gambling income. Many tax professionals view a 1256 position as aggressive because binary event contracts (CFTC-classed as swaps or options) don't clearly fit 1256's enumerated categories. Kalshi reportedly does not issue a 1099-B for trading gains (1099-INT for interest and 1099-MISC for rewards only), though one source disagrees.

### Cited Findings
- §1256 would give 60% LT / 40% ST regardless of holding period, with year-end mark-to-market on Form 6781. Whether event contracts qualify has no IRS ruling or case law, and there was no IRS guidance as of Jul 16 2026 — [Coselite](https://coselite.com/blog/section-1256-event-contracts); [CoinLedger](https://coinledger.io/blog/prediction-markets-tax)
- "Many tax professionals believe Kalshi event contracts do not qualify for Section 1256," and claiming it is considered aggressive — [Coselite Kalshi taxes](https://coselite.com/blog/kalshi-taxes)
- Kalshi issues 1099-INT (interest), 1099-MISC (rewards) and 1099-B/DA for crypto transfers only. Robinhood provides an "Event Contracts Annual Statement" that it says is not a substitute tax form. One source claims a 1099-B for proceeds, which conflicts — [Monaco CPA](https://www.monacocpa.cpa/prediction-market-tax); [CountOnSheep](https://countonsheep.com/blog/kalshi-taxes); [Awaken](https://awaken.tax/media/article/prediction-market-taxes)
- Under gambling treatment, ordinary rates apply to gross winnings with limited netting of losses — [CoinLedger](https://coinledger.io/blog/prediction-markets-tax)

### Inferences
- If the CFTC's pending proposed rule formally defines event contracts as "swaps," that strengthens the argument that they are §1256-excluded swaps under the Dodd-Frank §1256(b)(2)(B) carve-out, pushing toward ordinary capital or ordinary treatment. Speculation; consult tax counsel.
- A professional MM entity would likely elect trader-in-securities/commodities status with §475(f) mark-to-market (ordinary income and losses), which sidesteps the gambling-loss limitation risk. This is a common prop-firm practice but not sourced for event contracts specifically.
- Polymarket US 1099 practice was not found.

### Gaps
- No primary IRS or Treasury statement found. No source on Polymarket US, CDNA, DKeX or Rothera 1099 practices. No source on §475 elections for event-contract traders.
