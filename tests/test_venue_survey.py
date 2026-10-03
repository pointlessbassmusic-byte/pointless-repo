from sportsbot.substrate_bridge import venue_survey as vs


def test_code_split_and_dates():
    assert vs.split_codes("26OCT12BUFLAR", set(vs.NFL)) == ("BUF", "LAR")
    assert vs.split_codes("BUFLAR", set(vs.NFL)) == ("BUF", "LAR")
    assert vs.split_codes("TORVGK", set(vs.NHL)) == ("TOR", "VGK")
    assert vs.split_codes("HOUDAL", set(vs.NBA)) == ("HOU", "DAL")
    assert vs.kalshi_event_date("KXNFLGAME-26OCT12BUFLAR").isoformat() == "2026-10-12"
    # Polymarket start 2026-10-03 00:00 UTC is the Eastern evening of Oct 2
    assert vs._pm_date_et("2026-10-03 00:00:00+00").isoformat() == "2026-10-02"


def test_summary_box_and_costs():
    k = vs.Side("kalshi", "K", "Bills", bid=0.44, ask=0.46, bid_size=100, ask_size=100, fee_rate=0.07, maker_share=0.25)
    p = vs.Side("polymarket", "P", "Bills", bid=0.47, ask=0.48, bid_size=500, ask_size=500, fee_rate=0.03)
    s = vs.summarise([vs.PairedGame("nfl", "2026-10-12", k, p, {"vol24": 1000.0})])["nfl"]
    assert s["pairs"] == 1
    assert abs(s["k_taker_fee_mid"] - 0.07 * 0.45 * 0.55) < 1e-9
    assert abs(s["p_taker_fee_mid"] - 0.03 * 0.475 * 0.525) < 1e-9
    # YES@K 0.46 + NO@PM 0.53 = 0.99 gross; fees push it over $1 -> no box
    assert s["box_positive"] == 0 and s["box_best"] < 0
    assert "nfl" in vs.format_summary({"nfl": s})
