"""The model-vs-market harness: the test that decides whether to trade.

Each case pins a way this measurement can lie and look like an edge.
"""

from datetime import datetime, timezone

from sportsbot.backtest.vs_market import (
    MarketSample,
    clv_correlation,
    flat_stake,
    price_at_lead,
    score_leads,
    suspect_in_play,
    verdict,
)
from sportsbot.exchanges.kalshi import (
    DEFAULT_MAKER_FEE,
    kalshi_fee_per_share,
    kalshi_maker_fee_per_share,
)


def _s(mid, won, prices, model):
    return MarketSample(market_id=mid, won=won, prices=prices, model_prob=model)


def test_a_model_that_loses_to_the_line_is_reported_as_losing():
    """The market nails every game; the model is a coin. No amount of
    baseline-beating should dress that up as an edge."""
    samples = [_s(f"m{i}", i % 2 == 0,
                  {3: 0.95 if i % 2 == 0 else 0.05}, 0.5)
               for i in range(20)]
    sc = score_leads(samples, leads=[3])
    assert sc[0].market_brier < sc[0].model_brier
    assert sc[0].edge_brier < 0
    assert not sc[0].model_beats_market
    assert verdict(sc, [], set()).startswith("NO EDGE")


def test_a_real_edge_is_reported_as_one():
    samples = [_s(f"m{i}", i % 2 == 0,
                  {3: 0.5}, 0.9 if i % 2 == 0 else 0.1)
               for i in range(20)]
    sc = score_leads(samples, leads=[3])
    assert sc[0].edge_brier > 0
    st = flat_stake(samples, lead=3, fee_fn=lambda p: 0.0, min_edges=[0.05])
    assert st[0].roi > 0
    assert verdict(sc, st, set()).startswith("POSSIBLE EDGE")


def test_in_play_contamination_is_flagged_not_scored_as_skill():
    """A price sampled after the event starts scores like genius. If the
    harness cannot see that, every future backtest inherits the lie."""
    samples = []
    for i in range(30):
        won = i % 2 == 0
        samples.append(_s(f"m{i}", won,
                          {24: 0.5, 0: 0.99 if won else 0.01}, 0.5))
    sc = score_leads(samples, leads=[24, 0])
    sus = suspect_in_play(sc)
    assert 0 in sus and 24 not in sus
    assert "in-play?" in __import__(
        "sportsbot.backtest.vs_market", fromlist=["format_report"]
    ).format_report(sc, [], None, sus)


def test_no_clean_lead_is_said_out_loud():
    samples = [_s(f"m{i}", i % 2 == 0, {24: 0.99 if i % 2 == 0 else 0.01}, 0.5)
               for i in range(20)]
    sc = score_leads(samples, leads=[24])
    assert verdict(sc, [], {24}).startswith("NO CLEAN LEAD")


def test_fees_are_charged_on_every_bet():
    """A backtest that skips fees is the documented failure in this repo's
    own archive. Same bets, non-zero fee, strictly worse PnL."""
    samples = [_s(f"m{i}", True, {3: 0.5}, 0.9) for i in range(10)]
    free = flat_stake(samples, 3, lambda p: 0.0, [0.05])[0]
    paid = flat_stake(samples, 3, lambda p: 0.02, [0.05])[0]
    assert paid.bets == free.bets
    assert paid.pnl < free.pnl


def test_both_sides_are_considered():
    """An edge on NO is as tradable as one on YES. Scoring only YES would
    hide half the strategy — and half the losses."""
    samples = [_s("m", False, {3: 0.9}, 0.1)]      # model likes NO at 0.10
    r = flat_stake(samples, 3, lambda p: 0.0, [0.05])[0]
    assert r.bets == 1 and r.wins == 1 and r.pnl > 0


def test_zero_clv_is_distinguishable_from_positive():
    import random
    rng = random.Random(7)
    noise = [_s(f"m{i}", True, {24: 0.5, 3: 0.5 + rng.uniform(-0.05, 0.05)},
                0.5 + rng.uniform(-0.05, 0.05)) for i in range(400)]
    assert abs(clv_correlation(noise)["corr"]) < 0.25

    signal = []
    for i in range(200):
        d = rng.uniform(-0.1, 0.1)
        signal.append(_s(f"s{i}", True, {24: 0.5, 3: 0.5 + d * 0.8}, 0.5 + d))
    assert clv_correlation(signal)["corr"] > 0.8


def test_price_at_lead_never_borrows_a_later_price():
    start = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    def candle(hours_before, px):
        ts = (start.timestamp() - hours_before * 3600)
        return {"end_period_ts": ts, "price": {"close_dollars": str(px)}}
    candles = [candle(6, "0.40"), candle(3, "0.50"), candle(1, "0.80")]
    got = price_at_lead(candles, start, leads=[6, 3, 2])
    assert got[6] == 0.40
    assert got[3] == 0.50
    assert got[2] == 0.50          # the 1h candle is after the 2h target


def test_missing_prices_are_skipped_not_imputed():
    samples = [_s("a", True, {3: 0.5}, 0.6), _s("b", False, {}, 0.6)]
    sc = score_leads(samples, leads=[3])
    assert sc[0].n == 1


def test_maker_fills_are_not_free():
    """Kalshi reports fee_type 'quadratic_with_maker_fees' on both sports
    series, and the strategy prefers maker execution. A zero maker fee
    would under-cost the bot's own preferred path."""
    assert kalshi_maker_fee_per_share("KXATPMATCH-X") > 0
    assert DEFAULT_MAKER_FEE > 0


def test_maker_fee_carries_the_series_multiplier():
    atp = kalshi_maker_fee_per_share("KXATPMATCH-X")
    mlb = kalshi_maker_fee_per_share("KXMLBGAME-X")
    assert abs(mlb - atp * 0.5) < 1e-12


def test_maker_fee_is_flat_so_it_bites_hardest_on_cheap_contracts():
    """The taker fee shapes with p(1-p); the maker fee does not. Near the
    wings a resting order is barely cheaper than crossing the spread, which
    is the opposite of the intuition maker-first quoting relies on."""
    maker = kalshi_maker_fee_per_share("KXATPMATCH-X")
    assert maker > kalshi_fee_per_share(0.02, 1.0)     # cheaper to take
    assert maker < kalshi_fee_per_share(0.50, 1.0)     # cheaper to post


def test_maker_fee_override_from_env(monkeypatch):
    monkeypatch.setenv("KALSHI_MAKER_FEE_PER_CONTRACT", "0.01")
    assert abs(kalshi_maker_fee_per_share("KXATPMATCH-X") - 0.01) < 1e-12
    monkeypatch.setenv("KALSHI_MAKER_FEE_PER_CONTRACT", "nonsense")
    assert kalshi_maker_fee_per_share("KXATPMATCH-X") == DEFAULT_MAKER_FEE
