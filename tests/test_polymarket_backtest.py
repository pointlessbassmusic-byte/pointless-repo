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
    """A ["0.5","0.5"] resolution is a void and must not become a game; every
    sport anchors on gameStartTime, and a market whose gameStartTime is not
    before its closedTime (rescheduled / closed early) is skipped."""
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
    assert ten[0].start_ts == pm._ts(f"{day} 11:30:00")

    class LateStart(FakeHTTP):
        def get(self, url, params=None):
            class R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self):
                    return [{"slug": "ev", "markets": [
                        mkt("closed-first", '["1","0"]', start=f"{day} 15:00:00",
                            closed=f"{day} 14:00:00")]}]
            return R()
    assert pm.fetch_resolved("tennis", days=6, window_days=3, http=LateStart(), pause=0) == []


def test_price_at_lead_refuses_a_closing_print_at_the_rail():
    """If the last print before the anchor is 1.0 the market had already
    resolved by then — the anchor was late — and the row must be dropped,
    not scored as a 95-point CLV."""
    import sportsbot.backtest.polymarket_market as pm
    t0 = 1_800_000_000
    hist = [{"t": t0 - h * 3600, "p": 0.40} for h in range(30, 2, -1)] + [{"t": t0 - 3600, "p": 1.0}]
    assert pm.price_at_lead(hist, t0, 12.0) is None
    hist[-1]["p"] = 0.55
    assert pm.price_at_lead(hist, t0, 12.0) is not None


def test_mlb_backtest_orients_the_prediction_to_polymarkets_visitor_first_listing(monkeypatch):
    """PMGame.home is outcomes[0] = the visitor. The model is asked about the
    real matchup and the answer restated for the listed side, so a strong
    real-home favourite priced cheaply on the visitor's contract yields a NO
    bet on that contract (= the home team)."""
    from datetime import datetime, timezone
    import sportsbot.backtest.polymarket_market as pm
    from sportsbot.data.mlb_data import GameResult

    t0 = int(datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc).timestamp())
    hist = []
    # 60 earlier games: "strong" beats "weak" every time, alternating venues
    for i in range(60):
        d = datetime(2026, 7, 1 + i % 28, 23, 0, tzinfo=timezone.utc).replace(month=7 + i // 28)
        home, away = ("strong", "weak") if i % 2 else ("weak", "strong")
        hist.append(GameResult(date=d, home=home, away=away, home_score=5 if home == "strong" else 1,
                               away_score=1 if home == "strong" else 5))
    target = GameResult(date=datetime.fromtimestamp(t0, timezone.utc), home="strong", away="weak",
                        home_score=3, away_score=2)
    hist.append(target)
    game = pm.PMGame(date=target.date, slug="mlb-weak-strong", home="weak", away="strong",
                     token="tok", start_ts=t0, close_ts=t0 + 4 * 3600, home_won=False, volume=1.0)
    # the visitor ("weak") priced at 0.48 all day: far too rich (0.50 exactly
    # would be read as the listing placeholder and skipped)
    monkeypatch.setattr(pm, "price_history",
                        lambda token, http=None: [{"t": t0 - h * 3600, "p": 0.48} for h in range(30, 0, -1)])
    res = pm.run_mlb_backtest(hist, [game], lead_hours=6.0, min_edge=0.01)
    assert res.priced == 1 and len(res.bets) == 1
    bet = res.bets[0]
    assert bet.side == "NO"            # NO on the visitor's contract = the home side
    assert bet.won is True
