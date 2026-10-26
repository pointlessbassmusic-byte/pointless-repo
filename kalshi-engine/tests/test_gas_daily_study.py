"""Gas ladder study: the model uses only values published before the
decision, the quote is the candle nearest close - 2 h, and the taker rule
and fee follow the pre-registration."""
from datetime import date, timedelta

from src.gas_daily_study import _quote, event_date, history, model_prob, rows, score, trades


def _values(n, start=date(2026, 4, 1), step=0.01):
    return {start + timedelta(days=i): 4.0 + step * i for i in range(n)}


def test_event_date_parses_the_ticker():
    assert event_date("KXAAAGASD-26OCT26") == date(2026, 10, 26)
    assert event_date("junk") is None


def test_history_uses_only_prior_values_and_needs_twenty_residuals():
    v = _values(40)
    h = history(v)
    first = min(h)
    # first change on day 1, drift from day 6 (five changes), 20 residuals -> day 26
    assert first == date(2026, 4, 1) + timedelta(days=26)
    prev, mu, sigma = h[date(2026, 4, 30)]
    assert prev == v[date(2026, 4, 29)] and abs(mu - 0.01) < 1e-12 and sigma == 0.002
    # changing a future value cannot move an earlier forecast
    v2 = dict(v)
    v2[date(2026, 5, 9)] = 9.0
    assert history(v2)[date(2026, 4, 30)] == h[date(2026, 4, 30)]


def test_model_prob_is_centred_on_yesterday_plus_drift():
    assert abs(model_prob(4.01, 4.00, 0.01, 0.002) - 0.5) < 1e-12
    assert model_prob(4.00, 4.00, 0.01, 0.002) > 0.99


def test_quote_takes_the_candle_nearest_the_decision_within_an_hour():
    c = [{"end_period_ts": 1000, "yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.45"}},
         {"end_period_ts": 4600, "yes_bid": {"close_dollars": "0.50"}, "yes_ask": {"close_dollars": "0.55"}}]
    assert _quote(c, 4000) == (0.50, 0.55)
    assert _quote(c, 9000) == (None, None)


def test_taker_rule_and_fee():
    rs = [("E1", date(2026, 5, 1), 1.0, 0.80, 0.60, 0.70),   # YES edge 0.10 -> buy at 0.70
          ("E1", date(2026, 5, 1), 0.0, 0.20, 0.30, 0.40),   # NO at 0.70 vs 0.80 -> buy NO
          ("E2", date(2026, 5, 2), 1.0, 0.52, 0.48, 0.50)]   # no edge
    t = trades(rs)
    assert [round(x[2], 6) for x in t] == [round(1 - 0.70 - 0.07 * 0.70 * 0.30, 6),
                                          round(1 - 0.70 - 0.07 * 0.70 * 0.30, 6)]
    s = score(rs)
    assert s["trades"] == 2 and s["rows"] == 3


def _q(b, a):
    return [{"end_period_ts": 10_000 - 7200, "yes_bid": {"close_dollars": b},
             "yes_ask": {"close_dollars": a}}]


def test_rows_skip_one_sided_and_rail_quotes():
    v = _values(30)
    evs = []
    for d, val in v.items():
        tick = f"KXAAAGASD-{d.strftime('%y%b%d').upper()}"
        evs.append({"event_ticker": tick, "close": 10_000, "markets": [
            {"strike": round(val - 0.005, 4), "type": "greater", "result": "yes", "value": val,
             "candles": _q("0.60", "0.65")},
            {"strike": round(val + 0.5, 4), "type": "greater", "result": "no", "value": val,
             "candles": _q("0.00", "0.01")}]})
    r = rows(evs)
    assert r and all(0.03 <= x[5] <= 0.97 for x in r) and {x[1] for x in r} == {max(v) - timedelta(days=i) for i in range(4)}
