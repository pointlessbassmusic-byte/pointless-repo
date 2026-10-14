"""Wallet scan: leaderboard pool, copy pricing, persistence verdict."""
import httpx
import pytest

from sportsbot.backtest import wallet_review as wr
from sportsbot.backtest import wallet_scan as ws

A = "0x" + "11" * 20
B = "0x" + "22" * 20


class Http:
    def get(self, url, params=None):
        rows = [{"proxyWallet": A, "userName": "alpha", "pnl": 10.0, "vol": 100.0},
                {"proxyWallet": "not-an-address", "userName": "x"}]
        if params["category"] == "CRYPTO":
            rows.append({"proxyWallet": B.upper().replace("0X", "0x"), "userName": "beta", "pnl": 5.0, "vol": 50.0})
        return httpx.Response(200, json=rows, request=httpx.Request("GET", url))


def test_pool_dedupes_wallets_and_records_their_categories():
    info = ws.pool(Http(), per_category=2, month=1)
    assert set(info) == {A, B}
    assert "CRYPTO/ALL" in info[B]["categories"] and len(info[A]["categories"]) == 2 * len(ws.CATEGORIES)


def _g(side, payout, ts=1000.0, idx=0):
    return wr.Graded(ts=ts, condition_id="c", slug="s", title="t", side=side, outcome_index=idx, price=0.4,
                     shares=1, usdc=0.4, category="politics", lead_h=None, payout=payout)


def test_copy_enters_after_the_delay_at_the_then_price_plus_costs():
    hist = [(1100.0, 0.45), (1700.0, 0.50), (2000.0, 0.55)]
    v = ws.copy_value(_g("BUY", 1.0), hist, delay=600, fee_rate=0.05)
    entry = 0.50 + 0.005
    assert v == pytest.approx(1.0 - entry - 0.05 * entry * (1 - entry))
    # a SELL is copied by buying the other outcome
    v = ws.copy_value(_g("SELL", 0.0), hist, delay=600, fee_rate=0.0)
    assert v == pytest.approx(1.0 - (0.50 + 0.005))
    # unresolved, no later price, or the game already started: no copy
    assert ws.copy_value(_g("BUY", None), hist, 600, 0.05) is None
    assert ws.copy_value(_g("BUY", 1.0), hist, 5000, 0.05) is None
    g = _g("BUY", 1.0)
    g.extra["start_ts"] = 1200.0
    assert ws.copy_value(g, hist, 600, 0.05) is None


def _result(addr, older_mo, newer_mo, copies):
    half = {"settle": {"mean": 0.0, "se": 0.0, "n": 20}}
    return {"address": addr, "trades": 40, "two_sided": 0.0,
            "older": {**half, "markout_24h": {"mean": older_mo, "se": 0.01, "n": 20},
                      "copy": {"mean": None, "se": None, "n": 0}},
            "newer": {**half, "markout_24h": {"mean": newer_mo, "se": 0.01, "n": 20},
                      "copy": {"mean": None, "se": None, "n": 0}},
            "newer_copy_rows": [(c, f"{addr}-{i}") for i, c in enumerate(copies)]}


def test_verdict_ranks_on_the_older_half_and_scores_the_newer():
    res = [_result(f"w{i}", o, n, [c] * 5) for i, (o, n, c) in enumerate(
        [(0.05, 0.04, 0.03), (0.03, 0.02, 0.01), (0.01, 0.00, -0.02), (-0.01, -0.02, -0.03)])]
    v = ws.verdict(res, top_frac=0.25)
    assert v["usable"] == 4 and v["markout_rank_corr"] == pytest.approx(1.0)
    assert v["top_newer_copy"]["wallets"] == 1 and v["top_newer_copy"]["mean"] == pytest.approx(0.03)
    assert v["rest_newer_copy"]["mean"] == pytest.approx((0.01 - 0.02 - 0.03) / 3)
    assert "rank correlation" in ws.format_scan({}, res, v)


def test_token_sample_is_deterministic():
    assert ws._sampled("abc", 0.5) == ws._sampled("abc", 0.5)
    assert ws._sampled("abc", 1.0) is True
