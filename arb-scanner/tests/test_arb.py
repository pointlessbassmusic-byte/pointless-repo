from datetime import datetime, timedelta, timezone

from src.arb import ArbDetector, kalshi_fee
from src.feeds import BinaryMarket, _poly_fee_rate
from src.matcher import MarketPair, find_pairs, jaccard, tokens


def bm(platform, mid, question="Will X happen?", yes_ask=None, no_ask=None,
       close=None, volume=1000, fee_rate=None):
    return BinaryMarket(
        platform=platform, market_id=mid, question=question,
        yes_bid=None, yes_ask=yes_ask, no_bid=None, no_ask=no_ask,
        volume=volume, close_time=close, fee_rate=fee_rate,
    )


def test_kalshi_fee_peaks_at_half():
    assert kalshi_fee(0.5, 0.07) > kalshi_fee(0.1, 0.07)
    assert abs(kalshi_fee(0.5, 0.07) - 0.0175) < 1e-9


def test_cross_platform_arb_detected_and_fee_gated():
    det = ArbDetector({"min_net_edge": 0.02, "kalshi_fee_rate": 0.07})
    p = bm("polymarket", "tok", yes_ask=0.40, fee_rate=0.0)
    k = bm("kalshi", "TICK", no_ask=0.50)
    pair = MarketPair(poly=p, kalshi=k, similarity=0.8)
    opps = det.scan_pairs([pair])
    # gross = 1 - 0.40 - 0.50 = 0.10; kalshi fee on NO@0.50 = 0.0175 → net ~0.0825
    assert len(opps) == 1
    assert opps[0].yes_platform == "polymarket"
    assert abs(opps[0].net_edge - (0.10 - 0.07 * 0.5 * 0.5)) < 1e-6

    # shrink the mispricing so fees eat the edge
    k.no_ask = 0.585
    assert det.scan_pairs([MarketPair(poly=p, kalshi=k, similarity=0.8)]) == []


def test_bundle_arb_same_platform():
    det = ArbDetector({"min_net_edge": 0.02, "kalshi_fee_rate": 0.0})
    m = bm("kalshi", "T", yes_ask=0.45, no_ask=0.50)
    opps = det.scan_bundles([m])
    assert len(opps) == 1 and abs(opps[0].net_edge - 0.05) < 1e-9
    assert det.scan_bundles([bm("kalshi", "T", yes_ask=0.50, no_ask=0.50)]) == []


def test_matcher_pairs_same_question_and_respects_close_time():
    now = datetime.now(timezone.utc)
    p = bm("polymarket", "tok", question="Will Bitcoin reach $150,000 by December 31?",
           close=now + timedelta(days=5))
    k_good = bm("kalshi", "KXBTC", question="Bitcoin price above $150,000 on Dec 31?",
                close=now + timedelta(days=5, hours=3))
    k_far = bm("kalshi", "KXBTC-FAR", question="Bitcoin price above $150,000 on Dec 31?",
               close=now + timedelta(days=40))
    pairs = find_pairs([p], [k_far, k_good], min_similarity=0.3)
    assert len(pairs) == 1 and pairs[0].kalshi.market_id == "KXBTC"

    unrelated = bm("kalshi", "KXWEATHER", question="High temperature in Miami today?",
                   close=now + timedelta(days=5))
    assert find_pairs([p], [unrelated], min_similarity=0.3) == []


def test_alias_normalization_bridges_btc_bitcoin():
    a = tokens("Will BTC close above 150000?")
    b = tokens("Bitcoin above 150000 at close?")
    assert jaccard(a, b) > 0.4


def test_implausible_cross_platform_edge_flagged_as_suspect():
    det = ArbDetector({"min_net_edge": 0.02, "max_net_edge": 0.15, "kalshi_fee_rate": 0.0})
    p = bm("polymarket", "tok", yes_ask=0.05)
    k = bm("kalshi", "TICK", no_ask=0.05)  # "90% edge" = wrong-question match
    opps = det.scan_pairs([MarketPair(poly=p, kalshi=k, similarity=0.55)])
    assert [o.kind for o in opps] == ["suspect_match"]


def test_polarity_inverted_markets_never_pair():
    now = datetime.now(timezone.utc)
    p = bm("polymarket", "tok", question="Will BTC be below $100k on Dec 31?",
           close=now + timedelta(days=5))
    k_inverted = bm("kalshi", "KXUP", question="BTC above $100k on Dec 31?",
                    close=now + timedelta(days=5))
    assert find_pairs([p], [k_inverted], min_similarity=0.3) == []
    # same direction still pairs
    k_same = bm("kalshi", "KXDOWN", question="BTC below $100k on Dec 31?",
                close=now + timedelta(days=5))
    pairs = find_pairs([p], [k_same], min_similarity=0.3)
    assert len(pairs) == 1 and pairs[0].kalshi.market_id == "KXDOWN"


def test_disjoint_strike_numbers_never_pair():
    now = datetime.now(timezone.utc)
    p = bm("polymarket", "tok", question="Highest temperature in NYC between 82-83 on September 8?",
           close=now + timedelta(days=1))
    wrong_band = bm("kalshi", "KX8081", question="Highest temperature in NYC between 80-81 on September 8?",
                    close=now + timedelta(days=1))
    right_band = bm("kalshi", "KX8283", question="Highest temperature in NYC between 82-83 on September 8?",
                    close=now + timedelta(days=1))
    pairs = find_pairs([p], [wrong_band, right_band], min_similarity=0.3)
    assert len(pairs) == 1 and pairs[0].kalshi.market_id == "KX8283"


def test_gamma_fee_schedule_parsed_and_unknown_is_not_free():
    on = {"feesEnabled": True, "feeSchedule": {"exponent": 1, "rate": 0.03, "takerOnly": True}}
    assert _poly_fee_rate(on) == 0.03
    assert _poly_fee_rate({"feesEnabled": False, "feeSchedule": {"rate": 0.05}}) == 0.0
    # Gamma silent, or fees on with no schedule: unknown, so the detector's
    # default applies rather than zero
    assert _poly_fee_rate({}) is None
    assert _poly_fee_rate({"feesEnabled": True}) is None


def test_polymarket_taker_fee_is_charged_per_market():
    """Polymarket charges rate * P * (1-P) per share (0.05 on most markets in
    2026), not zero. A box that clears with Polymarket free must be charged
    its leg's fee, at the market's own rate or the default when unknown."""
    det = ArbDetector({"min_net_edge": 0.0, "kalshi_fee_rate": 0.07, "poly_fee_rate": 0.05})
    k = bm("kalshi", "TICK", no_ask=0.50)
    gross = 1 - 0.47 - 0.50
    kfee = 0.07 * 0.5 * 0.5
    for rate, charged in ((0.03, 0.03), (None, 0.05), (0.0, 0.0)):
        p = bm("polymarket", "tok", yes_ask=0.47, fee_rate=rate)
        opps = det.scan_pairs([MarketPair(poly=p, kalshi=k, similarity=0.8)])
        assert len(opps) == 1
        assert abs(opps[0].net_edge - round(gross - kfee - charged * 0.47 * 0.53, 4)) < 1e-9


def test_fees_do_not_launder_a_suspect_match_into_an_arb():
    """Live, Oct 2026: 'best AI model' (Polymarket, YES 0.54) against 'best
    coding model' (Kalshi, NO 0.29) is a 17-point gap. Charging Polymarket's
    fee took net to 14.6%, under max_net_edge, and relabelled the
    wrong-question pair an arb. Plausibility is judged on the gross gap."""
    det = ArbDetector({"min_net_edge": 0.02, "max_net_edge": 0.15,
                       "kalshi_fee_rate": 0.07, "poly_fee_rate": 0.05})
    p = bm("polymarket", "tok", yes_ask=0.54)
    k = bm("kalshi", "TICK", no_ask=0.29)
    opps = det.scan_pairs([MarketPair(poly=p, kalshi=k, similarity=0.58)])
    assert len(opps) == 1 and opps[0].net_edge < 0.15
    assert opps[0].kind == "suspect_match"
