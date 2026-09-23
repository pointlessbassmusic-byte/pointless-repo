"""Market-aware backtest: the properties that make its numbers trustworthy."""

import json
from datetime import datetime, timezone

import pytest

from sportsbot.backtest.kalshi_market import (
    Bet,
    Result,
    event_date,
    event_start_ts,
    quote_at_lead,
)


def test_event_start_is_parsed_in_eastern():
    """Kalshi stamps the ticker in US Eastern; reading it as UTC would put
    the decision point hours off and silently sample the wrong quote."""
    ts = event_start_ts("KXMLBGAME-26SEP242210SDLAD")
    assert datetime.fromtimestamp(ts, timezone.utc).isoformat() == "2026-09-25T02:10:00+00:00"
    assert event_date("KXMLBGAME-26SEP242210SDLAD").date().isoformat() == "2026-09-24"
    assert event_start_ts("KXMLBGAME-nonsense") is None


def _candles(start_ts: int) -> list[dict]:
    """Hourly candles from 6h before first pitch to 4h after, where the price
    jumps to near-certainty once the game is under way."""
    out = []
    for h in range(-6, 5):
        ts = start_ts + h * 3600
        px = "0.4000" if h < 0 else "0.9700"
        out.append({
            "end_period_ts": ts,
            "yes_bid": {"close_dollars": px},
            "yes_ask": {"close_dollars": f"{float(px) + 0.02:.4f}"},
            "price": {"close_dollars": px},
        })
    return out


def test_closing_line_is_taken_before_first_pitch(tmp_path, monkeypatch):
    """These markets stay open through the game, so the last quote before
    CLOSE already knows the result. Measuring CLV against it would just be
    asking whether the bet won."""
    import sportsbot.backtest.kalshi_market as km

    monkeypatch.setattr(km, "CACHE_DIR", str(tmp_path))
    start = int(datetime(2026, 9, 24, 23, 10, tzinfo=timezone.utc).timestamp())
    close = start + 4 * 3600                       # market closes after the game
    (tmp_path / "T.json").write_text(json.dumps(_candles(start)))

    bid, ask, closing = quote_at_lead(None, "KXMLBGAME", "T", close,
                                      lead_hours=2.0, start_ts=start)
    assert (bid, ask) == (0.40, 0.42)              # sampled 2h before first pitch
    assert closing == pytest.approx(0.41)          # pre-game, NOT the 0.97 in-play print


def test_clv_is_signed_for_the_side_taken():
    yes = Bet(date=datetime.now(timezone.utc), market="m", side="YES",
              model_prob=0.6, entry=0.50, close=0.55, stake=5.0, edge=0.04,
              won=True, pnl=5.0)
    no = Bet(date=datetime.now(timezone.utc), market="m", side="NO",
             model_prob=0.6, entry=0.50, close=0.45, stake=5.0, edge=0.04,
             won=True, pnl=5.0)
    assert yes.clv == pytest.approx(0.05)    # price rose after we bought YES
    assert no.clv == pytest.approx(-0.05)    # a NO entry is stored side-adjusted


def test_summary_reports_nothing_rather_than_dividing_by_zero():
    r = Result(considered=10, priced=4)
    s = r.summary()
    assert s["bets"] == 0 and s["considered"] == 10 and s["priced"] == 4
    assert "roi" not in s      # no bets -> no return to report


def test_summary_arithmetic():
    now = datetime.now(timezone.utc)
    r = Result(considered=2, priced=2, bets=[
        Bet(date=now, market="a", side="YES", model_prob=0.6, entry=0.50,
            close=0.55, stake=5.0, edge=0.04, won=True, pnl=4.90),
        Bet(date=now, market="b", side="NO", model_prob=0.6, entry=0.40,
            close=0.50, stake=4.0, edge=0.05, won=False, pnl=-4.10),
    ])
    s = r.summary()
    assert s["bets"] == 2
    assert s["staked"] == 9.0
    assert s["pnl"] == 0.80
    assert s["roi"] == round(0.80 / 9.0, 4)
    assert s["hit_rate"] == 0.5
    assert s["mean_clv"] == round((0.05 + 0.10) / 2, 4)
    assert s["clv_positive_rate"] == 1.0


def _tennis_mkt(ev: str, code: str, player: str, result: str) -> dict:
    return {"ticker": f"{ev}-{code}", "event_ticker": ev, "yes_sub_title": player,
            "result": result, "settlement_ts": "2026-09-23T08:02:58Z",
            "close_time": "2026-09-23T08:00:54Z"}


def test_tennis_pairing_prices_a_deterministic_side(monkeypatch):
    """One MarketGame per event; the priced side is the alphabetically first
    player so a rerun prices the same market; the anchor is settlement − 3h."""
    import sportsbot.backtest.kalshi_market as km

    ev = "KXATPMATCH-26SEP23BASCIN"
    pages = [{"markets": [
        _tennis_mkt(ev, "CIN", "Federico Cina", "no"),
        _tennis_mkt(ev, "BAS", "Nikoloz Basilashvili", "yes"),
    ]}]

    class FakeClient:
        def _request(self, method, path, params=None):
            return pages.pop(0) if pages else {"markets": []}

    games = km.fetch_settled_tennis(FakeClient(), series=("KXATPMATCH",))
    assert len(games) == 1
    g = games[0]
    assert g.home == "federico cina" and g.away == "nikoloz basilashvili"
    assert g.home_ticker.endswith("-CIN")
    assert g.home_won is False                    # Cina's market settled NO
    assert g.date.date().isoformat() == "2026-09-23"
    settle = int(datetime(2026, 9, 23, 8, 2, 58, tzinfo=timezone.utc).timestamp())
    assert g.start_ts == settle - 3 * 3600


def test_tennis_walk_forward_cannot_learn_from_the_same_day(monkeypatch):
    """Two matches on one day: the prediction for either must be identical
    whichever is listed first, i.e. neither result reached the model before
    the day's predictions were made."""
    import sportsbot.backtest.kalshi_market as km
    from sportsbot.data.tennis_data import MatchResult

    d = datetime(2026, 9, 23, tzinfo=timezone.utc)
    hist = [MatchResult(date=d, winner="a", loser="b", surface="", best_of=3,
                        level="", tourney="t"),
            MatchResult(date=d, winner="a", loser="c", surface="", best_of=3,
                        level="", tourney="t")]
    # a prior month of history so 'a','b','c' clear min_matches
    for i in range(12):
        prior = datetime(2026, 8, 1 + i, tzinfo=timezone.utc)
        hist += [MatchResult(date=prior, winner="a", loser="b", surface="",
                             best_of=3, level="", tourney="t"),
                 MatchResult(date=prior, winner="c", loser="a", surface="",
                             best_of=3, level="", tourney="t")]
    games = [km.MarketGame(date=d, home="a", away="b", home_ticker="KXATPMATCH-X-A",
                           close_ts=0, home_won=True, start_ts=0),
             km.MarketGame(date=d, home="a", away="c", home_ticker="KXATPMATCH-Y-A",
                           close_ts=0, home_won=True, start_ts=0)]
    monkeypatch.setattr(km, "quote_at_lead", lambda *a, **k: (0.49, 0.51, 0.50))

    class C:
        def fee_multiplier(self, t):
            return 1.0

    def probs(order):
        r = km.run_tennis_backtest(order, games, C(), min_edge=-1.0,
                                   max_uncertainty=1.0)
        return {b.market: b.model_prob for b in r.bets}

    forward = probs(hist)
    swapped = probs([hist[1], hist[0]] + hist[2:])
    assert forward == swapped
    assert len(forward) == 2
