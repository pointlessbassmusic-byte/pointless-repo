"""The scanner's funnel loss must reach the decision feed, aggregated.

Markets dropped before pricing used to vanish with only a log line, which
made "the bot placed no bets" indistinguishable from "the bot never looked
at anything" -- the exact confusion the decision feed exists to prevent.
"""

from types import SimpleNamespace

from sportsbot.bot.runner import Runner
from sportsbot.bot.scanner import Scanner
from sportsbot.core.types import Exchange, MarketInfo, Sport


def _market(mid, home, away, sport=Sport.TENNIS):
    return MarketInfo(exchange=Exchange.KALSHI, market_id=mid, question=mid,
                      slug=mid, sport=sport, home=home, away=away)


class _UnfitModel:
    name = "tennis_elo_markov_v1"


class _FitModel:
    name = "m"


def test_unfit_model_reports_every_market_it_drops():
    sc = Scanner({Sport.TENNIS: _UnfitModel()})
    markets = [_market(f"T{i}", "A", "B") for i in range(5)]
    scanned, drops = sc.scan_verbose(markets)
    assert scanned == []
    assert len(drops) == 5
    assert all("no rated entities" in d.reason for d in drops)
    assert all("sportsbot fit" in d.reason for d in drops)


def test_unfit_model_warns_once_not_once_per_market(caplog):
    sc = Scanner({Sport.TENNIS: _UnfitModel()})
    with caplog.at_level("WARNING"):
        sc.scan_verbose([_market(f"T{i}", "A", "B") for i in range(50)])
    assert caplog.text.count("no rated entities") == 1


def test_unmatched_entity_names_the_side_that_failed(monkeypatch):
    import sportsbot.bot.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.Scanner, "_rated_entities",
                        lambda self, model: ["known team"])
    sc = scanner_mod.Scanner({Sport.BASEBALL: _FitModel()})
    _, drops = sc.scan_verbose(
        [_market("B1", "known team", "wholly unknown club", Sport.BASEBALL)])
    assert len(drops) == 1
    assert "wholly unknown club" in drops[0].reason


def test_scan_still_returns_a_plain_list():
    """`scan` is the older API; it must keep returning markets only."""
    sc = Scanner({Sport.TENNIS: _UnfitModel()})
    assert sc.scan([_market("T1", "A", "B")]) == []


# ---------------------------------------------------------------------------
# Aggregation: the feed must survive a fully-dropped slate
# ---------------------------------------------------------------------------
class _RecordingStore:
    def __init__(self):
        self.rows = []

    def record_decision(self, **kw):
        self.rows.append(kw)


def _runner_stub(store):
    return SimpleNamespace(store=store, account="sim",
                           _record_scan_drops=Runner._record_scan_drops)


def test_a_fully_dropped_slate_writes_one_row_per_reason_not_per_market():
    """200 markets lost to one cause is one feed row, not 200. The dashboard
    renders the most recent decisions, so per-market rows would evict every
    real bet and skip within a single cycle."""
    store = _RecordingStore()
    r = _runner_stub(store)
    drops = [SimpleNamespace(market=_market(f"T{i}", "A", "B"),
                             reason="model X has no rated entities")
             for i in range(200)]
    Runner._record_scan_drops(r, drops)
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["action"] == "skip"
    assert "200" in row["title"]
    assert "no rated entities" in row["reason"]
    # The label must not pass for a real venue ticker.
    assert row["market_id"].startswith("(")


def test_drops_are_grouped_by_sport_and_reason():
    store = _RecordingStore()
    r = _runner_stub(store)
    drops = (
        [SimpleNamespace(market=_market(f"T{i}", "A", "B", Sport.TENNIS),
                         reason="unfit") for i in range(3)]
        + [SimpleNamespace(market=_market("B1", "A", "B", Sport.BASEBALL),
                           reason="unfit")]
        + [SimpleNamespace(market=_market("T9", "A", "B", Sport.TENNIS),
                           reason="no match")]
    )
    Runner._record_scan_drops(r, drops)
    assert len(store.rows) == 3
    # largest group first, so a truncated feed shows the dominant cause
    assert store.rows[0]["sport"] == "tennis" and "3" in store.rows[0]["title"]


def test_recording_a_drop_never_breaks_the_cycle():
    class Boom:
        def record_decision(self, **kw):
            raise RuntimeError("db gone")

    r = SimpleNamespace(store=Boom(), account="sim")
    Runner._record_scan_drops(
        r, [SimpleNamespace(market=_market("T1", "A", "B"), reason="x")])


def test_no_drops_writes_nothing():
    store = _RecordingStore()
    Runner._record_scan_drops(_runner_stub(store), [])
    assert store.rows == []
