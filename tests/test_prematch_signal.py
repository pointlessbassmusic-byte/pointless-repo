"""Pre-match quote recorder: snapshots only pre-start markets, settles after
the start, picks the snapshot nearest T-30 and scores the pre-registered
forward test net of each venue's fee."""

from datetime import datetime, timezone

from sportsbot.core.types import BookLevel, Exchange, MarketInfo, MarketQuote, Sport
from sportsbot.signals.prematch import CUT_S, Recorder, forward_test, observations


class _Client:
    def __init__(self, markets, quotes, results):
        self.markets, self.quotes, self.results = markets, quotes, results

    def list_sports_markets(self, sport):
        return [m for m in self.markets if m.sport.value == sport]

    def get_quote(self, m):
        bid, ask = self.quotes[m.market_id]
        return MarketQuote(market_id=m.market_id, bid=bid, ask=ask,
                           bids=[BookLevel(price=bid, size=10)], asks=[BookLevel(price=ask, size=10)])

    def get_resolution(self, market_id):
        return self.results.get(market_id)


def _m(mid, start, sport=Sport.TENNIS):
    return MarketInfo(exchange=Exchange.POLYMARKET_US, market_id=mid, sport=sport, home="A", away="B",
                      start_time=datetime.fromtimestamp(start, timezone.utc))


def test_records_only_prematch_and_settles_after_start(tmp_path):
    t0 = 1_000_000
    client = _Client([_m("soon", t0 + CUT_S), _m("started", t0 - 10)],
                     {"soon": (0.10, 0.12), "started": (0.5, 0.52)}, {"soon": False})
    rec = Recorder(str(tmp_path / "p.sqlite"), {"polymarket_us": client}, interval=1)
    assert rec.snapshot_once(now=t0) == 1                      # the started one is skipped
    assert rec.settle_once(now=t0 + 3600) == 0                 # too early to ask
    assert rec.settle_once(now=t0 + CUT_S + 7 * 3600) == 1
    obs = observations(rec.conn)
    assert len(obs) == 1 and obs[0]["market_id"] == "soon" and obs[0]["settle"] == 0


def test_forward_test_scores_longshot_no_net_of_fee(tmp_path):
    t0 = 1_000_000
    markets = [_m(f"m{i}", t0 + CUT_S) for i in range(4)]
    quotes = {f"m{i}": (0.08, 0.10) for i in range(4)}          # mid 0.09: longshots
    results = {"m0": False, "m1": False, "m2": False, "m3": True}
    rec = Recorder(str(tmp_path / "p.sqlite"), {"polymarket_us": _Client(markets, quotes, results)})
    rec.snapshot_once(now=t0)
    rec.settle_once(now=t0 + 10 * 3600)
    rep = forward_test(rec.conn, min_n=4)
    cell = rep["cells"]["polymarket_us/tennis/LONG-NO"]
    # NO costs 1 - bid = 0.92 plus fee 0.0695*0.92*0.08; three of four pay out
    fee = 0.0695 * 0.92 * 0.08
    expected = (3 * (1 - 0.92 - fee) + (0 - 0.92 - fee)) / 4
    assert cell["n"] == 4 and abs(cell["mean_cents"] - expected * 100) < 0.01
    assert rep["verdict"] in ("PASS", "FAIL")                   # n reached the bar
    assert rep["calibration"]["polymarket_us/tennis/<.15"]["n"] == 4
    assert forward_test(rec.conn)["verdict"] == "PENDING"       # default bar is 150


def test_nearest_snapshot_to_t_minus_30_wins(tmp_path):
    t0 = 1_000_000
    start = t0 + 3 * CUT_S
    client = _Client([_m("x", start)], {"x": (0.40, 0.42)}, {"x": True})
    rec = Recorder(str(tmp_path / "p.sqlite"), {"kalshi": client})
    rec.snapshot_once(now=t0)                                  # 60 min out: too far
    client.quotes["x"] = (0.44, 0.46)
    rec.snapshot_once(now=start - CUT_S - 120)                 # 32 min out: the one
    client.quotes["x"] = (0.60, 0.62)
    rec.snapshot_once(now=start - 60)                          # 1 min out: too late
    rec.settle_once(now=start + 10 * 3600)
    obs = observations(rec.conn)
    assert len(obs) == 1 and abs(obs[0]["mid"] - 0.45) < 1e-9


def test_only_markets_starting_within_the_horizon_are_quoted(tmp_path):
    t0 = 1_000_000
    no_start = MarketInfo(exchange=Exchange.KALSHI, market_id="nostart", sport=Sport.TENNIS,
                          home="A", away="B")
    client = _Client([_m("soon", t0 + 3600), _m("far", t0 + 5 * 3600), no_start],
                     {"soon": (0.4, 0.42), "far": (0.4, 0.42), "nostart": (0.4, 0.42)}, {})
    rec = Recorder(str(tmp_path / "p.sqlite"), {"kalshi": client}, horizon=3 * 3600)
    assert rec.snapshot_once(now=t0) == 1
    assert [r[0] for r in rec.conn.execute("SELECT market_id FROM snapshots")] == ["soon"]
