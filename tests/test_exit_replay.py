"""Exit-rule replay on synthetic price paths with known shapes."""

from sportsbot.backtest.exit_replay import PathSim, run_grid, simulate


def rec(prices, won, start_ts=0.0, step=300.0, game_start=None):
    return {"sport": "tennis", "won": won, "game_start": game_start,
            "path": [(start_ts + i * step, p) for i, p in enumerate(prices)]}


def test_stop_saves_a_collapsing_loser():
    # 0.50 entry grinding to zero: stop at 50% of cost exits near 0.25.
    prices = [0.50 - 0.02 * i for i in range(24)]
    s = simulate(rec([max(p, 0.03) for p in prices], won=False), 0.5, None)
    assert s.exited and s.exit_reason == "stop"
    assert s.pnl_rule > s.pnl_hold  # recovered part of the stake
    assert s.pnl_hold == -1.0


def test_stop_alone_leaves_monotone_winner_untouched():
    prices = [0.50 + 0.02 * i for i in range(24)]
    s = simulate(rec([min(p, 0.97) for p in prices], won=True), 0.5, None)
    assert not s.exited
    assert s.pnl_rule == s.pnl_hold > 0


def test_edge_rule_acts_as_take_profit_on_big_winners():
    # Production semantics: with the model term frozen at entry, a gain
    # beyond ~(exit_edge + costs)/model_weight ≈ 21.6 points flips the
    # blend below the cash-out value -> the winner is closed early at a
    # high price. This is an implicit take-profit; the replay documents it.
    prices = [min(0.50 + 0.02 * i, 0.97) for i in range(24)]
    s = simulate(rec(prices, won=True), 0.5, -0.05)
    assert s.exited and s.exit_reason == "edge"
    assert 0.0 < s.pnl_rule < s.pnl_hold   # profit kept, payout sacrificed


def test_whipsaw_winner_pays_the_insurance_premium():
    # A dip that OUTLASTS the min-hold window then recovers to win: the
    # stop exits at the bottom — the cost side of the ledger.
    prices = [0.50, 0.45, 0.38, 0.30, 0.24, 0.22, 0.22, 0.22,
              0.40, 0.70, 0.95, 0.97, 0.97]
    s = simulate(rec(prices, won=True), 0.5, None)
    assert s.exited and s.exit_reason == "stop"
    assert s.pnl_rule < s.pnl_hold


def test_min_hold_and_entry_band_respected():
    # First ticks outside the band are skipped; a crash inside the min-hold
    # window can't trigger until the hold expires (then exits at the floor).
    prices = [0.92, 0.90, 0.50] + [0.10] * 17
    s = simulate(rec(prices, won=False, step=120.0), 0.5, None)  # 2-min ticks
    assert s.entry == 0.50
    assert s.exited and s.exit_reason == "stop"
    assert -1.0 < s.pnl_rule < -0.5  # exited at 0.10-ish, not at 0.5-value


def test_pre_start_cutoff_blocks_inplay_entries():
    gs = 1000.0
    s = simulate(rec([0.5] * 12, won=True, start_ts=gs + 60.0,
                     game_start=gs), 0.5, -0.05)
    assert s is None  # only in-play ticks available: production never enters


def test_grid_report_shape():
    records = [rec([0.5 - 0.02 * i for i in range(24)], won=False),
               rec([0.5 + 0.02 * i for i in range(24)], won=True)]
    report = run_grid(records)
    assert report["n_paths"] == 2 and report["base_rate_win"] == 0.5
    assert report["production"] == {"stop": 0.5, "edge": -0.05}
    cells = {(c["stop"], c["edge"]): c for c in report["grid"]}
    assert (0.5, -0.05) in cells and (None, -0.05) in cells
    stop_only = cells[(0.5, None)]
    assert stop_only["n"] == 2 and stop_only["saves"] == 1
    assert stop_only["whipsaws"] == 0          # monotone winner untouched
    both = cells[(0.5, -0.05)]
    assert both["whipsaws"] == 1               # edge take-profits the winner
    assert isinstance(PathSim("t", True, 0.5, 1.0, 1.0, False, ""), PathSim)
