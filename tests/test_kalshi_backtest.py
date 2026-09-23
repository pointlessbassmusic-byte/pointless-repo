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
