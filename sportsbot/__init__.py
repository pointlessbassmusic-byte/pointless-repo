"""sportsbot — consolidated sports prediction engine + exchange trading bot.

Layers:
    core      — math substrate: odds, staking, Elo, Markov chains, calibration
    engine    — per-sport prediction models (tennis, baseball, table tennis)
    data      — data ingestion (Sackmann tennis CSVs, MLB Stats API, table tennis)
    exchanges — venue clients (Polymarket live, Kalshi-ready)
    bot       — live trading loop: scan, price, stake, execute, risk
    backtest  — historical evaluation of models and strategies
"""

__version__ = "1.0.0"
