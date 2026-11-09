"""Regression tests for the review findings: fill-based exposure accounting,
cancel-failure retention, and end-to-end settlement through the runner path.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from sportsbot.bot.executor import Executor
from sportsbot.core.types import (
    BetIntent,
    BookLevel,
    Exchange,
    MarketInfo,
    MarketQuote,
    Side,
)
from sportsbot.data.store import Store
from sportsbot.exchanges.paper import PaperExchange


def _market(**kw):
    defaults = dict(
        exchange=Exchange.POLYMARKET, market_id="m1", yes_token_id="t1",
        no_token_id="t2", question="A vs B", slug="a-b",
        start_time=datetime.now(timezone.utc) + timedelta(hours=6),
    )
    from sportsbot.core.types import Sport

    defaults.setdefault("sport", Sport.TENNIS)
    defaults.update(kw)
    return MarketInfo(**defaults)


def _quote(bid=0.48, ask=0.50, depth=200.0):
    return MarketQuote(
        market_id="m1", bid=bid, ask=ask,
        bids=[BookLevel(price=bid, size=depth)],
        asks=[BookLevel(price=ask, size=depth)],
    )


def _intent(side=Side.YES, price=0.50, size=100.0):
    return BetIntent(market=_market(), side=side, prob=0.60, price=price,
                     size=size, edge=0.10, kelly_fraction=0.03)


class TestExposureAccounting:
    def test_resting_order_creates_no_exposure(self, tmp_path):
        store = Store(str(tmp_path / "t.sqlite"))
        paper = PaperExchange(starting_balance=1000.0)
        ex = Executor(paper, store)
        # Maker price below the ask: rests, no fill in the conservative sim.
        ex.submit(_intent(price=0.49), quote=_quote(ask=0.50))
        exp = store.exposure_by()
        assert exp["total"] == 0.0 and exp["open_positions"] == 0

    def test_filled_order_creates_exact_exposure(self, tmp_path):
        store = Store(str(tmp_path / "t.sqlite"))
        paper = PaperExchange(starting_balance=1000.0)
        ex = Executor(paper, store)
        ex.submit(_intent(price=0.50, size=100.0), quote=_quote(ask=0.50, depth=60.0))
        exp = store.exposure_by()
        # Only the 60 filled shares count, at the intent price.
        assert exp["total"] == pytest.approx(0.50 * 60.0)

    def test_failed_cancel_keeps_order_tracked(self, tmp_path):
        store = Store(str(tmp_path / "t.sqlite"))
        paper = PaperExchange(starting_balance=1000.0)
        ex = Executor(paper, store, order_ttl_seconds=0.0)

        ex.submit(_intent(price=0.40), quote=_quote())  # rests
        assert len(ex._open) == 1
        paper.cancel_order_orig = paper.cancel_order
        paper.cancel_order = lambda oid: False  # simulate venue failure
        assert ex.expire_stale_orders() == 0
        assert len(ex._open) == 1  # still tracked, retried next cycle
        paper.cancel_order = paper.cancel_order_orig
        assert ex.expire_stale_orders() == 1
        assert len(ex._open) == 0


class _ResolvingPaper(PaperExchange):
    """Paper venue whose data client reports m1 resolved YES."""

    def __init__(self, resolution: Optional[bool] = True):
        super().__init__(starting_balance=1000.0)
        self._resolution = resolution

    def get_resolution(self, market_id: str) -> Optional[bool]:
        return self._resolution


class TestSettlement:
    def _runner_stub(self, tmp_path, resolution):
        """Wire just enough of Runner to exercise _settle_resolved."""
        from sportsbot.bot.runner import Runner

        runner = Runner.__new__(Runner)
        runner.store = Store(str(tmp_path / "t.sqlite"))
        runner.data_client = _ResolvingPaper(resolution)
        runner.executor = Executor(runner.data_client, runner.store)
        return runner

    def test_settles_open_bets(self, tmp_path):
        runner = self._runner_stub(tmp_path, resolution=True)
        runner.executor.submit(_intent(side=Side.YES, price=0.50, size=100.0),
                               quote=_quote(ask=0.50, depth=100.0))
        runner.store.snapshot_quote("m1", 0.55, 0.57)
        assert len(runner.store.open_bets()) == 1
        n = runner._settle_resolved()
        assert n == 1
        settled = runner.store.settled_bets()
        assert settled[0]["outcome"] == 1
        assert settled[0]["pnl"] == pytest.approx(100.0 - 50.0)
        assert settled[0]["closing_price"] == pytest.approx(0.56)
        # exposure released
        assert runner.store.exposure_by()["total"] == 0.0

    def test_losing_side(self, tmp_path):
        runner = self._runner_stub(tmp_path, resolution=False)
        runner.executor.submit(_intent(side=Side.YES, price=0.50, size=100.0),
                               quote=_quote(ask=0.50, depth=100.0))
        runner._settle_resolved()
        settled = runner.store.settled_bets()
        assert settled[0]["outcome"] == 0
        assert settled[0]["pnl"] == pytest.approx(-50.0)

    def test_no_close_without_a_book_seen_after_the_entry(self, tmp_path):
        """The close was the last snapshot of the market, whenever it was
        taken. In a book that scans once a day the only snapshot of a filled
        touch order is the quote it was posted against, so every fill
        'closed' at its entry mid and every CLV was the half-spread (+0.5 pt
        on all four sandbox fills, Oct 13–31). A close must be a book seen
        after the entry; with none, there is no close."""
        runner = self._runner_stub(tmp_path, resolution=False)
        runner.store.snapshot_quote("m1", 0.49, 0.51)          # the quote it was posted against
        runner.executor.submit(_intent(side=Side.YES, price=0.50, size=100.0),
                               quote=_quote(ask=0.50, depth=100.0))
        runner._settle_resolved()
        settled = runner.store.settled_bets()
        assert settled[0]["outcome"] == 0                       # settlement itself unaffected
        assert settled[0]["closing_price"] is None

    def test_unresolved_stays_open(self, tmp_path):
        runner = self._runner_stub(tmp_path, resolution=None)
        runner.executor.submit(_intent(), quote=_quote(ask=0.50, depth=100.0))
        assert runner._settle_resolved() == 0
        assert len(runner.store.open_bets()) == 1


class _VoidPaper(PaperExchange):
    """Paper venue whose data client reports m1 resolved 50/50 (void)."""

    def __init__(self):
        super().__init__(starting_balance=1000.0)

    def get_resolution(self, market_id: str) -> Optional[bool]:
        return None                                   # no winner flag on either token

    def get_void(self, market_id: str) -> bool:
        return True


class TestVoidSettlement:
    """Nov 5: Rolex Paris Masters, Berrettini v Alcaraz closed 12 min after a
    paper touch fill, six hours before its start, and UMA resolved it 50/50
    (outcomePrices 0.5/0.5, neither CLOB token flagged winner). get_resolution
    returns None without a winner, so the bet stayed open for good: its
    exposure was never released and its PnL never booked. A void pays half
    a share each side; book it like an early close (outcome stays NULL, so
    it is no win/lose observation for Brier/calibration)."""

    def _runner(self, tmp_path):
        from sportsbot.bot.runner import Runner

        runner = Runner.__new__(Runner)
        runner.store = Store(str(tmp_path / "t.sqlite"))
        runner.data_client = _VoidPaper()
        runner.executor = Executor(runner.data_client, runner.store)
        return runner

    def test_a_void_market_settles_at_half_and_releases_exposure(self, tmp_path):
        runner = self._runner(tmp_path)
        runner.executor.submit(_intent(side=Side.YES, price=0.40, size=100.0),
                               quote=_quote(bid=0.38, ask=0.40, depth=100.0))
        assert runner.store.exposure_by()["total"] > 0
        assert runner._settle_resolved() == 1
        assert runner.store.open_bets() == []
        assert runner.store.exposure_by()["total"] == 0.0
        (b,) = runner.store.settled_bets()
        assert b["outcome"] is None and b["status"] == "closed"
        assert b["pnl"] == pytest.approx(100 * 0.5 - 40.0)

    def test_paper_balance_is_paid_half_per_share(self, tmp_path):
        runner = self._runner(tmp_path)
        runner.executor.submit(_intent(side=Side.YES, price=0.40, size=100.0),
                               quote=_quote(bid=0.38, ask=0.40, depth=100.0))
        before = runner.data_client.balance
        runner._settle_resolved()
        assert runner.data_client.balance == pytest.approx(before + 50.0)
