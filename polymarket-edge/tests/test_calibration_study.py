"""The calibration study's pure parts: parsing, sampling, scoring."""
from datetime import datetime, timezone

from src.calibration_study import (_parse_closed, calibration, favorite_strategy,
                                   fetch_resolved_markets, yes_price_before)


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._p


class _Http:
    """Serves canned Gamma pages and trade queries, recording what was asked."""
    def __init__(self, pages=(), trades=()):
        self.pages, self.trades, self.calls = list(pages), list(trades), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if "gamma" in url:
            return _Resp(self.pages.pop(0)) if self.pages else _Resp([], 422)
        return _Resp(self.trades.pop(0) if self.trades else [])


def _market(**kw):
    m = {"id": "1", "question": "q", "conditionId": "0xc", "outcomes": '["Yes","No"]',
         "outcomePrices": '["1","0"]', "clobTokenIds": '["Y","N"]',
         "closedTime": "2026-09-16 18:00:00+00", "endDate": "2026-09-16T18:00:00Z",
         "volumeNum": 50000, "negRisk": True, "feesEnabled": False,
         "events": [{"ticker": "fed-decision-in-september"}]}
    m.update(kw)
    return m


def test_parse_closed_handles_gamma_dialects():
    assert _parse_closed("2026-09-16 18:00:00+00") == datetime(2026, 9, 16, 18, tzinfo=timezone.utc)
    assert _parse_closed("2026-09-16T18:00:00Z") == datetime(2026, 9, 16, 18, tzinfo=timezone.utc)
    assert _parse_closed(None) is None and _parse_closed("junk") is None


def test_fetch_keeps_only_clean_scheduled_resolutions():
    pages = [[
        _market(id="ok"),
        _market(id="unresolved", outcomePrices='["0.6","0.4"]'),
        _market(id="multi", outcomes='["A","B"]'),
        _market(id="thin", volumeNum=100),
        # a "by <date>" market resolved YES months before its legal end date:
        # its calendar says nothing about when the price was formed
        _market(id="early", closedTime="2026-03-01 12:00:00+00", endDate="2029-01-19T23:59:00Z"),
    ]]
    got = fetch_resolved_markets(_Http(pages), n=10, min_volume=10_000)
    assert [m["id"] for m in got] == ["ok"]
    assert got[0]["outcome"] == 1 and got[0]["category"] == "fed-decision-in-september"


def test_fetch_stops_at_gamma_offset_cap():
    # first page full, second page refused with 422: return what we have
    got = fetch_resolved_markets(_Http([[_market(id=str(i)) for i in range(100)]]), n=500,
                                 min_volume=0)
    assert len(got) == 100


def test_yes_price_is_normalised_to_the_yes_token():
    m = {"condition_id": "0xc", "yes_token": "Y"}
    http = _Http(trades=[[{"asset": "N", "price": 0.03, "timestamp": 10},
                          {"asset": "Y", "price": 0.96, "timestamp": 9}]])
    # the latest trade is on the NO token at 3c: YES was 97c
    assert abs(yes_price_before(http, m, 100) - 0.97) < 1e-9
    assert http.calls[0][1]["end"] == 100
    assert yes_price_before(_Http(trades=[[]]), m, 100) is None


def _row(h, p, y):
    return {"horizon_h": h, "price": p, "outcome": y}


def test_favorite_strategy_buys_either_side_and_scores_per_dollar():
    rows = [_row(24, 0.95, 1),   # YES favorite wins: 1/0.95 - 1
            _row(24, 0.05, 0),   # NO favorite (95c) wins: 1/0.95 - 1
            _row(24, 0.95, 0),   # YES favorite loses: -1
            _row(24, 0.60, 1)]   # no favorite at this threshold
    s = favorite_strategy(rows, threshold=0.90)[24]
    assert s["n"] == 3
    expected = (2 * (1 / 0.95 - 1) - 1) / 3
    assert abs(s["mean_return"] - expected) < 1e-9
    assert abs(s["hit_rate"] - 2 / 3) < 1e-9


def test_calibration_buckets_by_price_and_reports_realized_rate():
    rows = [_row(1, 0.92, 1), _row(1, 0.93, 1), _row(1, 0.94, 0), _row(1, 0.12, 0)]
    table = {(lo, hi): (n, yr) for lo, hi, n, _, yr in calibration(rows)[1]}
    assert table[(0.90, 0.95)] == (3, 2 / 3)
    assert table[(0.10, 0.15)] == (1, 0.0)
