# Kalshi LIP shadow measurement

Recorder: `python -m sportsbot.signals.lip --db data/lip.sqlite` (or
`deploy/sportsbot-lip.service`). Data only, public endpoints, never trades.

Analysis: `PYTHONPATH=. python research/lip/analyze.py data/lip.sqlite [Q]`
reports, per category and quote variant (join best bid / one tick behind), the
prorated reward share minus the P&L of simulated (queue-aware) fills held to
settlement.

Pass bar (from docs/ARTICLE_EVAL_2026-10-02.md): net > 0 with day-clustered
t ≥ 2 over ≥ 14 days, computed on settled markets only. The reward unit
(`period_reward` × 1e-4 USD) is an assumption, consistent with Kalshi's
published $1–$1,000 per market per day range; confirm against a real payout
before any live pilot.
