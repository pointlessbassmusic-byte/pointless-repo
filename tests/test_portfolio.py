"""Portfolio allocation: every rule that moves money is pinned here, and
every case where a rule could loosen risk is pinned to the tight side."""

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from sportsbot.bot import portfolio as pf

CFG = {"bankroll": {"max_total_exposure": 0.50, "max_fraction_per_sport": 0.20},
       "sports": {"baseball": {"enabled": True, "signal": "sharp"},
                  "tennis": {"enabled": True},
                  "table_tennis": {"enabled": False}},
       "execution": {"post_inside_spread": True}}
RUNNING = {"baseball/sharp/maker", "baseball/sharp/taker",
           "tennis/model/maker", "tennis/model/taker"}
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def _row(arm, clv, won=True, entry=0.5, pnl=None, ts=None, market=None, sharp=None):
    return {"arm": arm, "sport": arm.split("/")[0], "market_id": market or f"m{abs(hash((arm, clv, ts))) % 10**6}",
            "entry_price": entry, "closing_price": entry + clv,
            "sharp_closing_price": sharp, "outcome": 1 if won else 0,
            "model_prob": 0.6, "pnl": (pnl if pnl is not None else (1.0 if won else -1.0)),
            "ts": (ts or NOW).isoformat()}


def _evidence_rows(arm, n, clv, won_share=0.6):
    return [_row(arm, clv + (0.002 if i % 2 else -0.002), won=(i % 10) < won_share * 10,
                 market=f"{arm.replace(chr(47), chr(95))}{i}", ts=NOW - timedelta(hours=i)) for i in range(n)]


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------
def test_arm_of_intent_reads_signal_and_style_from_the_reason():
    assert pf.arm_of_intent("baseball", "sharpline p=0.5 blend=0.5 mid=0.5 maker") == "baseball/sharp/maker"
    assert pf.arm_of_intent("tennis", "tennis_elo_markov_v1 p=0.6 blend=0.55 mid=0.5 taker") == "tennis/model/taker"
    assert pf.arm_of_row({"sport": "tennis"}) == "tennis/model/taker"      # legacy rows
    assert pf.arm_of_row({"sport": "tennis", "arm": "tennis/sharp/maker"}) == "tennis/sharp/maker"


def test_running_arms_follow_the_config():
    assert pf.running_arms_for(CFG) == RUNNING
    no_post = {**CFG, "execution": {"post_inside_spread": False}}
    assert pf.running_arms_for(no_post) == {"baseball/sharp/taker", "tennis/model/taker"}
    with pytest.raises(ValueError):
        pf.running_arms_for({"sports": {"tennis": {"enabled": True, "signal": "elo"}}})


# ---------------------------------------------------------------------------
# the operator's file
# ---------------------------------------------------------------------------
def test_load_allocation_defaults_to_the_strict_reading_and_validates(tmp_path):
    cfg = pf.load_allocation(str(tmp_path / "missing.yaml"))
    assert cfg.bankroll_mode == "config" and cfg.learning_budget_usd == 0.0 and cfg.arms == {}
    f = tmp_path / "a.yaml"
    f.write_text(yaml.safe_dump({"bankroll_mode": "equity",
                                 "learning": {"budget_usd": 25, "weekly_loss_stop_usd": 20},
                                 "arms": {"baseball/sharp/maker": {"weight": None, "learn": True},
                                          "tennis/model/taker": {"weight": 0.1, "enabled": False}}}))
    cfg = pf.load_allocation(str(f))
    assert cfg.bankroll_mode == "equity" and cfg.learning_budget_usd == 25.0
    assert cfg.setting("baseball/sharp/maker").learn is True
    assert cfg.setting("tennis/model/taker").weight == 0.1
    assert cfg.setting("tennis/model/taker").enabled is False
    assert cfg.setting("never/mentioned/arm") == pf.ArmSetting()
    f.write_text(yaml.safe_dump({"arms": {"baseball/sharp/maker": {"weight": 1.5}}}))
    with pytest.raises(ValueError):
        pf.load_allocation(str(f))
    f.write_text(yaml.safe_dump({"bankroll_mode": "yolo"}))
    with pytest.raises(ValueError):
        pf.load_allocation(str(f))
    f.write_text(yaml.safe_dump({"arms": {"not-an-arm": {}}}))
    with pytest.raises(ValueError):
        pf.load_allocation(str(f))


def test_the_shipped_allocation_file_parses():
    cfg = pf.load_allocation("config/allocation.yaml")
    assert cfg.bankroll_mode == "equity"
    assert cfg.learning_budget_usd == 0.0                 # nothing funded until the operator says so
    assert cfg.setting("table_tennis/model/taker").enabled is False


# ---------------------------------------------------------------------------
# evidence and the gate
# ---------------------------------------------------------------------------
def test_evidence_prefers_the_sharp_close_and_gate_needs_the_interval():
    rows = _evidence_rows("baseball/sharp/maker", 250, 0.01)
    ev = pf.arm_evidence(rows)
    assert ev["n_clv"] == 250 and ev["mean_clv"] == pytest.approx(0.01, abs=1e-9)
    assert ev["clv_lo"] > 0 and ev["sharp_share"] == 0.0
    gate = pf.arm_gate(ev, fees_verified=True)
    assert gate["ready"] is True
    assert pf.arm_gate(ev, fees_verified=False)["ready"] is False
    thin = pf.arm_evidence(rows[:50])
    assert pf.arm_gate(thin, True)["ready"] is False      # sample size
    noisy = pf.arm_evidence([_row("a/model/taker", 0.03 if i % 2 else -0.03, market=f"n{i}")
                             for i in range(250)])
    assert noisy["clv_lo"] < 0 < noisy["clv_hi"]
    assert pf.arm_gate(noisy, True)["ready"] is False     # interval spans zero
    sharp = pf.arm_evidence([_row("a/sharp/maker", 0.0, entry=0.5, sharp=0.53, market="s1")])
    assert sharp["mean_clv"] == pytest.approx(0.03) and sharp["sharp_share"] == 1.0


# ---------------------------------------------------------------------------
# allocation rules
# ---------------------------------------------------------------------------
def test_live_funds_nothing_without_evidence_or_learning_money():
    port = pf.allocate_arms(100.0, CFG, pf.AllocationConfig(), [], RUNNING, mode="live")
    assert port["allocated"] == 0.0
    assert port["style_by_sport"] == {"baseball": "none", "tennis": "none"}
    assert all("gate not met" in e["source"] for n, e in port["arms"].items() if e["running"])


def test_paper_shares_the_cap_equally_among_running_arms():
    port = pf.allocate_arms(100.0, CFG, pf.AllocationConfig(), [], RUNNING, mode="paper")
    assert port["total_cap"] == 50.0 and port["sport_cap"] == 20.0
    # four arms x 12.5 = 50, then each sport scaled to its 20 cap
    assert port["sport_budget"] == {"baseball": 20.0, "tennis": 20.0}
    assert port["style_by_sport"] == {"baseball": "both", "tennis": "both"}
    assert port["learning"]["arms"] == []


def test_manual_weight_wins_and_is_capped_by_the_sport_cap():
    alloc = pf.AllocationConfig(arms={"baseball/sharp/maker": pf.ArmSetting(weight=0.8)})
    port = pf.allocate_arms(100.0, CFG, alloc, [], RUNNING, mode="live")
    arm = port["arms"]["baseball/sharp/maker"]
    assert "manual" in arm["source"] and "per-sport cap" in arm["source"]
    assert arm["budget"] == 20.0                           # 0.8 x 50 = 40, capped at 20
    assert port["style_by_sport"] == {"baseball": "maker", "tennis": "none"}
    assert port["arms"]["baseball/sharp/taker"]["budget"] == 0.0


def test_evidence_shares_by_the_ci_lower_bound_only_when_the_gate_is_green():
    rows = (_evidence_rows("baseball/sharp/maker", 250, 0.02)
            + _evidence_rows("tennis/model/taker", 250, 0.005))
    port = pf.allocate_arms(100.0, CFG, pf.AllocationConfig(), rows, RUNNING,
                            fees_verified=True, mode="live")
    b, t = port["arms"]["baseball/sharp/maker"], port["arms"]["tennis/model/taker"]
    assert b["gate"]["ready"] and t["gate"]["ready"]
    assert b["budget"] > t["budget"] > 0
    assert b["budget"] <= 20.0 and t["budget"] <= 20.0
    # fees not verified: the gate stays red and nothing is funded
    cold = pf.allocate_arms(100.0, CFG, pf.AllocationConfig(), rows, RUNNING,
                            fees_verified=False, mode="live")
    assert cold["allocated"] == 0.0


def test_learning_budget_is_split_equally_and_pauses_on_the_weekly_stop():
    alloc = pf.AllocationConfig(learning_budget_usd=30.0, learning_weekly_loss_stop_usd=20.0,
                                arms={"baseball/sharp/maker": pf.ArmSetting(learn=True),
                                      "tennis/model/maker": pf.ArmSetting(learn=True)})
    port = pf.allocate_arms(250.0, CFG, alloc, [], RUNNING, mode="live", now=NOW)
    assert port["arms"]["baseball/sharp/maker"]["budget"] == 15.0
    assert port["arms"]["tennis/model/maker"]["budget"] == 15.0
    assert port["style_by_sport"] == {"baseball": "maker", "tennis": "maker"}
    assert port["learning"]["paused"] is False
    # a week of losses past the stop pauses every learning arm
    losses = [_row("baseball/sharp/maker", 0.0, won=False, pnl=-7.0,
                   ts=NOW - timedelta(days=d), market=f"L{d}") for d in range(3)]
    port = pf.allocate_arms(250.0, CFG, alloc, losses, RUNNING, mode="live", now=NOW)
    assert port["learning"]["loss_7d"] == -21.0 and port["learning"]["paused"] is True
    assert port["allocated"] == 0.0
    assert "PAUSED" in port["arms"]["baseball/sharp/maker"]["source"]
    # losses older than the window roll off
    old = [_row("baseball/sharp/maker", 0.0, won=False, pnl=-50.0,
                ts=NOW - timedelta(days=9), market="old")]
    assert pf.allocate_arms(250.0, CFG, alloc, old, RUNNING, mode="live", now=NOW)["learning"]["paused"] is False


def test_learning_never_exceeds_what_the_caps_leave():
    alloc = pf.AllocationConfig(learning_budget_usd=500.0,
                                arms={"baseball/sharp/maker": pf.ArmSetting(learn=True)})
    port = pf.allocate_arms(100.0, CFG, alloc, [], RUNNING, mode="live")
    assert port["arms"]["baseball/sharp/maker"]["budget"] == 20.0     # sport cap, not $500
    assert port["allocated"] <= port["total_cap"]


def test_disabled_and_not_running_arms_get_nothing_whatever_their_record():
    rows = _evidence_rows("baseball/model/taker", 250, 0.03)        # good record, but config runs sharp
    alloc = pf.AllocationConfig(arms={"tennis/model/taker": pf.ArmSetting(weight=0.5, enabled=False)})
    port = pf.allocate_arms(100.0, CFG, alloc, rows, RUNNING, fees_verified=True, mode="live")
    assert port["arms"]["baseball/model/taker"]["budget"] == 0.0
    assert "not running" in port["arms"]["baseball/model/taker"]["source"]
    assert port["arms"]["tennis/model/taker"]["budget"] == 0.0
    assert "disabled" in port["arms"]["tennis/model/taker"]["source"]


def test_losses_only_ever_shrink_a_budget():
    good = _evidence_rows("baseball/sharp/maker", 250, 0.02)
    before = pf.allocate_arms(100.0, CFG, pf.AllocationConfig(), good, RUNNING,
                              fees_verified=True, mode="live")["arms"]["baseball/sharp/maker"]["budget"]
    worse = good + [_row("baseball/sharp/maker", -0.05, won=False, market=f"w{i}") for i in range(100)]
    after = pf.allocate_arms(100.0, CFG, pf.AllocationConfig(), worse, RUNNING,
                             fees_verified=True, mode="live")["arms"]["baseball/sharp/maker"]["budget"]
    assert after <= before


def test_effective_bankroll_rolls_equity_in_only_when_asked():
    eq = pf.AllocationConfig(bankroll_mode="equity")
    assert pf.effective_bankroll(eq, 100.0, 137.5) == 137.5
    assert pf.effective_bankroll(eq, 100.0, -3.0) == 0.0
    assert pf.effective_bankroll(eq, 100.0, None) == 100.0
    assert pf.effective_bankroll(pf.AllocationConfig(), 100.0, 137.5) == 100.0


def test_dashboard_rows_are_funded_first_and_carry_the_gate_progress():
    alloc = pf.AllocationConfig(arms={"tennis/model/taker": pf.ArmSetting(weight=0.2)})
    port = pf.allocate_arms(100.0, CFG, alloc, [], RUNNING, mode="live")
    rows = pf.summarise_for_dashboard(port)
    assert rows[0]["arm"] == "tennis/model/taker" and rows[0]["budget"] == 10.0
    assert 0.0 <= rows[0]["gate_progress"] <= 1.0
    assert len(pf.RESULTS) == 12 and pf.RESULTS[-1][0] == "14"
