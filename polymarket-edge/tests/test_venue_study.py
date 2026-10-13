"""Polymarket-vs-Kalshi venue study: ticker parsing, game folding with the
home side taken from the event ticker, candle selection, lead-lag and the
fee-aware strategy arithmetic."""
from datetime import datetime, timezone

from src.venue_study import (
    KalshiGame, group_games, kalshi_fee, kalshi_team, lead_lag, pair_games, parse_ticker,
    quote_at, strategy,
)
from src.sportsbook_study import PolyMatch


def _m(ticker, sub, result):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
            "yes_sub_title": sub, "result": result}


def test_parse_ticker_and_kalshi_names():
    assert parse_ticker("KXLALIGAGAME-26SEP20VCFRSO-VCF") == (
        "KXLALIGAGAME", datetime(2026, 9, 20), "VCFRSO", "VCF")
    assert parse_ticker("KXLALIGAGAME-26SEP20VCFRSO-TIE")[3] == "TIE"
    assert parse_ticker("KXFEDDECISION-26OCT-H25") is None
    assert kalshi_team("Real Sociedad") == "Sociedad"
    assert kalshi_team("M´gladbach") == "M'gladbach"        # Kalshi's acute accent
    assert kalshi_team("PSG") == "Paris SG" and kalshi_team("Paris") == "Paris FC"
    assert kalshi_team("Nowhere United") is None


def test_group_games_reads_home_from_the_ticker_pair_and_the_winner_from_results():
    ms = [_m("KXLALIGAGAME-26SEP20VCFRSO-VCF", "Valencia", "no"),
          _m("KXLALIGAGAME-26SEP20VCFRSO-TIE", "Tie", "no"),
          _m("KXLALIGAGAME-26SEP20VCFRSO-RSO", "Real Sociedad", "yes")]
    games, unmatched = group_games(ms)
    assert unmatched == set() and len(games) == 1
    g = games[0]
    assert (g.league, g.home, g.away, g.result) == ("SP1", "Valencia", "Sociedad", "A")
    assert g.tickers == {"H": ms[0]["ticker"], "D": ms[1]["ticker"], "A": ms[2]["ticker"]}
    # an unknown name is reported and the game skipped, never guessed
    ms[2]["yes_sub_title"] = "Nowhere United"
    games, unmatched = group_games(ms)
    assert games == [] and unmatched == {"Nowhere United"}
    # an incomplete event (two markets) is skipped
    assert group_games(ms[:2])[0] == []


def test_pair_games_needs_league_teams_date_and_agreeing_result():
    g = KalshiGame(league="SP1", event="KXLALIGAGAME-26SEP20VCFRSO", date=datetime(2026, 9, 20),
                   home="Valencia", away="Sociedad", tickers={}, result="A")
    pm = PolyMatch(league="SP1", slug="lal-vcf-rso", home="Valencia CF",
                   away="Real Sociedad de Fútbol",
                   kickoff=datetime(2026, 9, 20, 20, 0, tzinfo=timezone.utc), markets={}, result="A")
    assert pair_games([g], [pm]) == [(g, pm)]
    late = PolyMatch(**{**pm.__dict__, "kickoff": datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)})
    assert pair_games([g], [late]) == [(g, late)]           # past midnight UTC still pairs
    other = PolyMatch(**{**pm.__dict__, "result": "H"})
    assert pair_games([g], [other]) == []                     # disagreeing result: skipped
    swapped = PolyMatch(**{**pm.__dict__, "home": "Real Sociedad", "away": "Valencia"})
    assert pair_games([g], [swapped]) == []                   # reverse fixture does not pair


def test_quote_at_takes_the_last_two_sided_candle_before_the_horizon():
    t0 = 1_000_000
    def c(end, bid, ask):
        return {"end_period_ts": end, "yes_bid": {"close_dollars": bid},
                "yes_ask": {"close_dollars": ask}}
    candles = [c(t0 - 5 * 3600, "0.30", "0.32"), c(t0 - 4 * 3600, "0.31", "0.33"),
               c(t0 - 3600, "0.35", "0.36"), c(t0, "0.00", "0.01")]
    assert quote_at(candles, t0, 1) == (0.35, 0.36)
    assert quote_at(candles, t0, 3.5) == (0.31, 0.33)      # last candle at or before 3.5h out
    assert quote_at(candles, t0, 6) is None                  # nothing yet quoted
    assert quote_at(candles, t0, 0) is None                  # settled print (bid 0) is one-sided
    assert quote_at([c(t0 - 10 * 3600, "0.3", "0.4")], t0, 1) is None   # too stale (> 3h)


def _row(slug, side, h, pm, k_mid, outcome=0, k_ask=None):
    return {"slug": slug, "side": side, "horizon_h": h, "pm_price": pm, "k_mid": k_mid,
            "k_bid": k_mid - 0.01, "k_ask": k_ask if k_ask is not None else k_mid + 0.01,
            "outcome": outcome}


def test_lead_lag_finds_the_venue_that_closes_the_gap():
    rows = []
    for i in range(40):
        gap = 0.05 if i % 2 else -0.04
        k6 = 0.4 + 0.002 * i
        rows.append(_row(f"m{i}", "H", 6, k6 + gap, k6))
        # Kalshi converges fully to Polymarket by 1h; Polymarket does not move
        rows.append(_row(f"m{i}", "H", 1, k6 + gap, k6 + gap))
    ll = lead_lag(rows, 6, 1)
    assert abs(ll["kalshi"][0] - 1.0) < 1e-9 and ll["kalshi"][2] == 40
    assert abs(ll["polymarket"][0]) < 1e-9


def test_strategy_charges_the_kalshi_fee_and_can_mark_at_the_1h_mid():
    rows = [_row("a", "H", 6, 0.50, 0.44, outcome=1, k_ask=0.45),   # gap 5c: bought, pays
            _row("b", "H", 6, 0.50, 0.44, outcome=0, k_ask=0.45),   # bought, loses
            _row("c", "H", 6, 0.46, 0.44, outcome=1, k_ask=0.45)]   # gap 1c: not bought
    s = strategy(rows, 0.03)
    cost = 0.45 + kalshi_fee(0.45)
    assert s["n"] == 2 and abs(s["mean_ret"] - ((1 / cost - 1) + (-1)) / 2) < 1e-9
    exits = [_row("a", "H", 1, 0.5, 0.49), _row("b", "H", 1, 0.5, 0.47)]
    m = strategy(rows, 0.03, exit_rows=exits)
    assert m["n"] == 2 and abs(m["mean_ret"] - ((0.49 / cost - 1) + (0.47 / cost - 1)) / 2) < 1e-9
    assert strategy(rows, 0.10)["n"] == 0
