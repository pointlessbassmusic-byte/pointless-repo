"""Polymarket market backtest: parsing, anchoring, and the price-series quote."""

from sportsbot.backtest.polymarket_market import _ts, price_at_lead


def test_timestamps_parse_both_gamma_shapes():
    """Gamma mixes '2026-09-28 11:51:47' and '2026-09-29T11:30:00' — both
    are UTC and both must parse; a missing value is None, never an error."""
    assert _ts("2026-09-29T11:30:00") == 1790681400
    assert _ts("2026-09-28 11:51:47") == 1790596307
    assert _ts("2026-09-29T11:30:00Z") == 1790681400
    assert _ts(None) is None and _ts("") is None


def _hist(points):
    return [{"t": t, "p": p} for t, p in points]


def test_quote_ignores_the_listing_placeholder_and_uses_pre_anchor_close():
    """A resolved series opens with a run of exactly 0.5 before any trade —
    that is the listing placeholder, not a price. The decision mid is the
    nearest traded point to the lead; the closing line is the last point
    strictly before the anchor, never the in-play print after it."""
    h = _hist([(1000, 0.5), (4600, 0.5), (8200, 0.42), (11800, 0.44),
               (15400, 0.46), (19000, 0.90)])
    q = price_at_lead(h, start_ts=16000, lead_hours=2.0)
    assert q is not None
    bid, ask, closing = q
    assert (bid, ask) == (0.415, 0.425)        # 0.42 with a one-tick spread
    assert closing == 0.46                     # last pre-anchor point, not 0.90


def test_quote_refuses_when_nothing_traded_near_the_decision_point():
    assert price_at_lead(_hist([(1, 0.5)]), 100, 1.0) is None          # placeholder only
    assert price_at_lead(_hist([(1000, 0.4)]), 100000, 2.0) is None    # 27h away
    assert price_at_lead(_hist([(1000, 0.995)]), 4600, 1.0) is None    # outside (0.02, 0.98)


def test_fetch_resolved_skips_voids_and_anchors_by_sport(monkeypatch):
    """A ["0.5","0.5"] resolution is a void and must not become a game; MLB
    anchors on gameStartTime, tennis on closedTime minus three hours."""
    import sportsbot.backtest.polymarket_market as pm

    import time as _time
    day = _time.strftime("%Y-%m-%d", _time.gmtime(_time.time() - 3600))

    def mkt(slug, prices, start=f"{day} 11:30:00", closed=f"{day} 14:00:00"):
        return {"sportsMarketType": "moneyline", "slug": slug,
                "outcomePrices": prices, "outcomes": '["A","B"]',
                "clobTokenIds": '["tokA","tokB"]', "gameStartTime": start,
                "closedTime": closed, "volumeNum": "10"}

    class FakeHTTP:
        def get(self, url, params=None):
            class R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self):
                    return [{"slug": "ev", "markets": [
                        mkt("void", '["0.5","0.5"]'),
                        mkt("decided", '["1","0"]'),
                    ]}]
            return R()

    # two 3-day windows both return the same page: the token dedups
    mlb = pm.fetch_resolved("baseball", days=6, window_days=3, http=FakeHTTP(), pause=0)
    assert [g.slug for g in mlb] == ["decided"]
    assert mlb[0].home_won is True
    assert mlb[0].start_ts == pm._ts(f"{day} 11:30:00")

    ten = pm.fetch_resolved("tennis", days=6, window_days=3, http=FakeHTTP(), pause=0)
    assert ten[0].start_ts == pm._ts(f"{day} 14:00:00") - 3 * 3600
