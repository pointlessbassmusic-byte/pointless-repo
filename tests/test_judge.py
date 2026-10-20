"""Shadow LLM judge: funnel, parsing, budget, bench, evaluation. No network/SDK."""
import json

import pytest

from sportsbot.signals.judge import JudgeRecorder, evaluate, free_kill, parse_answer

NOW = 1_790_000_000


def mk(ticker="KXRT-1", bid="0.40", ask="0.44", vol="500", close="2027-01-01T00:00:00Z"):
    return {"ticker": ticker, "title": "t", "yes_bid_dollars": bid, "yes_ask_dollars": ask,
            "volume_fp": vol, "close_time": close, "rules_primary": "r"}


def test_free_kill_names_the_check_that_fired():
    assert free_kill(mk(), NOW) is None
    assert free_kill(mk(bid="0"), NOW) == "one_sided"
    assert free_kill(mk(bid="0.30", ask="0.45"), NOW) == "wide_spread"
    assert free_kill(mk(bid="0.01", ask="0.02"), NOW) == "price_band"
    assert free_kill(mk(vol="5"), NOW) == "low_volume"
    assert free_kill(mk(close="2026-09-21T13:00:00Z"), NOW) == "closing_soon"


def test_parse_answer_clips_and_rejects_garbage():
    assert parse_answer('{"p_yes": 1.3, "differs_from_market": false, "reason": "x"}')[0] == 1.0
    with pytest.raises(ValueError):
        parse_answer('{"p_yes": NaN, "differs_from_market": true, "reason": "x"}')
    with pytest.raises(json.JSONDecodeError):
        parse_answer("not json")


class _Resp:
    def __init__(self, d):
        self._d = d

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _Http:
    def __init__(self, markets):
        self.markets = markets

    def get(self, url, params=None):
        assert url.endswith("/events")
        return _Resp({"events": [{"category": "Entertainment", "markets": self.markets},
                                 {"category": "Sports", "markets": [mk("KXMLB-1")]}], "cursor": ""})


def test_cycle_respects_category_budget_and_bench(tmp_path):
    calls = []

    def judge(state):
        calls.append(state)
        return {"model": "m", "input_tokens": 10, "output_tokens": 5, "refused": False,
                "text": '{"p_yes": 0.5, "differs_from_market": false, "reason": "no edge over market"}'}

    ms = [mk(f"KXRT-{i}") for i in range(5)] + [mk("KXRT-wide", bid="0.1", ask="0.5")]
    r = JudgeRecorder(str(tmp_path / "j.sqlite"), judge, max_calls_per_day=3, client=_Http(ms))
    assert r.cycle({"Entertainment"}, now=NOW) == 3            # daily budget
    assert all(s["category"] == "Entertainment" for s in calls)  # sports never judged here
    assert r.cycle({"Entertainment"}, now=NOW + 60) == 0       # budget still spent today
    r.max_calls = 100
    assert r.cycle({"Entertainment"}, now=NOW + 120) == 2      # the 3 judged ones are benched
    assert r.rejections["wide_spread"] >= 1 and r.rejections["benched"] >= 3


def test_refusal_and_errors_are_recorded_or_benched_not_raised(tmp_path):
    def judge(state):
        if state["title"] == "boom":
            raise RuntimeError("api down")
        return {"model": "m", "input_tokens": 1, "output_tokens": 1, "refused": True}

    a, b = mk("KXRT-a"), mk("KXRT-b")
    b["title"] = "boom"
    r = JudgeRecorder(str(tmp_path / "j.sqlite"), judge, client=_Http([a, b]))
    assert r.cycle({"Entertainment"}, now=NOW) == 1
    assert r.db.execute("SELECT refused, p_yes FROM judgements").fetchone() == (1, None)
    assert r.db.execute("SELECT reason FROM bench WHERE ticker='KXRT-b'").fetchone() == ("error",)


def test_evaluate_uses_first_judgement_per_market(tmp_path):
    r = JudgeRecorder(str(tmp_path / "j.sqlite"), lambda s: {}, client=_Http([]))
    ins = "INSERT INTO judgements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    r.db.execute(ins, (1, "A", "E", 9, .4, .5, .45, 0.9, 1, "", "m", "high", 1, 1, "h", 0))
    r.db.execute(ins, (2, "A", "E", 9, .4, .5, .45, 0.1, 1, "", "m", "high", 1, 1, "h", 0))
    r.db.execute("INSERT INTO results VALUES ('A', 1.0, 3)")
    e = evaluate(r.db, weight=0.5)
    assert e["n"] == 1
    assert abs(e["brier_judge"] - 0.01) < 1e-9 and abs(e["brier_market"] - 0.3025) < 1e-9
