"""Evidence-weighted sleeve allocation for the trading bankroll.

One question: given what has actually been MEASURED about each sport, how
much of the bankroll may each one risk?

Prior weights below are not preferences — each cites the measurement behind
it (`docs/VALIDATION_2026-09-15.md`, the engine docstrings). A sport with no
validated model does not get money because its model is sophisticated; it
gets money when it has evidence, and the sleeve says so in `evidence`.

The priors then move with realized closing-line value, which is the earliest
honest read on whether an edge is real (the go-live gate is CLV-based for the
same reason). Two properties matter and are tested:

* Allocation reacts to CLV, never to a losing streak. A sleeve grows only on
  positive *measured* CLV over at least `clv_min_bets` observations — never
  because it is down and "due". Losses reduce risk elsewhere (drawdown scales
  Kelly toward its floor, negative CLV tightens the edge bar in
  `positions.py`); nothing here ever adds risk after a loss.
* Allocation only ever tightens the configured caps. The per-sport budget is
  the MIN of its allocation and `bankroll.max_fraction_per_sport`, so the risk
  layer stays fail-closed and this module can never loosen it.
"""

from __future__ import annotations

from dataclasses import dataclass

# How far measured CLV may move a sleeve's weight, and the band it may move
# inside. A sleeve can never be sized out of existence by CLV alone (it would
# stop generating the evidence that could restore it) nor run away on a good
# run — hence the floor and cap.
CLV_GAIN = 8.0
CLV_FLOOR = 0.4
CLV_CAP = 1.6


@dataclass(frozen=True)
class Sleeve:
    sport: str
    prior_weight: float
    evidence: str
    needs_ratings: bool = False


SLEEVES: tuple[Sleeve, ...] = (
    Sleeve(
        sport="baseball",
        prior_weight=0.55,
        evidence=(
            "Walk-forward n=11,661: log loss 0.6809 vs always-home 0.689, "
            "Brier 0.2440, 55.7% acc, calibration bins on the diagonal. The "
            "only model validated against a real baseline on a large sample."
        ),
    ),
    Sleeve(
        sport="tennis",
        prior_weight=0.35,
        evidence=(
            "Best-developed model (surface-blended Elo + O'Malley/Markov) and "
            "the sharpest target band in the research (0.60-0.63 log loss), "
            "but NOT yet fitted or validated on this host. Held at zero until "
            "`sportsbot fit tennis` produces ratings."
        ),
        needs_ratings=True,
    ),
    Sleeve(
        sport="table_tennis",
        prior_weight=0.10,
        evidence=(
            "Walk-forward n=414: log loss 0.6779 vs coin 0.6931 — beats a coin, "
            "but the engine records no edge against the market and fast leagues "
            "carry documented match-fixing risk. Measurement-only sleeve."
        ),
    ),
)

# Deliberately not a sleeve: the Kalshi weather dailies. On 168 settled
# markets the market scores Brier 0.0751 against our best baseline's 0.1828 —
# we are far worse than the price, so there is nothing to bet. It stays a
# substrate data arm, and the substrate is shadow-mode by protocol anyway.
EXCLUDED = {
    "weather": ("market Brier 0.0751 vs our baseline 0.1828 on 168 settled "
                "markets — no edge; substrate data only, shadow mode by protocol"),
}


def clv_multiplier(mean_clv: float | None, n_clv: int, min_bets: int) -> float:
    """Weight multiplier from realized CLV. Returns 1.0 (no opinion) until a
    sleeve has `min_bets` observations — an unmeasured sleeve is neither
    rewarded nor punished."""
    if mean_clv is None or n_clv < min_bets:
        return 1.0
    return max(CLV_FLOOR, min(CLV_CAP, 1.0 + CLV_GAIN * mean_clv))


def allocate(
    bankroll: float,
    cfg: dict,
    by_sport: dict[str, dict] | None = None,
    has_ratings: dict[str, bool] | None = None,
) -> dict:
    """Per-sport risk budget in dollars.

    `by_sport` is `positions.category_report()["by_sport"]`; `has_ratings`
    says which sports currently have fitted ratings on this host.

    Returns {"sleeves": {sport: {...}}, "unallocated", "bankroll"} where each
    sleeve carries its weight, dollar budget, the cap that bound it, and the
    evidence line — the dashboard renders this verbatim so the reason for
    every dollar is visible.
    """
    by_sport = by_sport or {}
    has_ratings = has_ratings or {}
    sports_cfg = cfg.get("sports", {})
    adaptive = cfg.get("adaptive", {})
    bank_cfg = cfg.get("bankroll", {})
    min_bets = int(adaptive.get("clv_min_bets", 30))
    sport_cap = float(bank_cfg.get("max_fraction_per_sport", 0.20))
    total_cap = float(bank_cfg.get("max_total_exposure", 0.50))

    eligible, blocked = [], {}
    for s in SLEEVES:
        if not sports_cfg.get(s.sport, {}).get("enabled", False):
            blocked[s.sport] = "disabled in config"
        elif s.needs_ratings and not has_ratings.get(s.sport, False):
            blocked[s.sport] = "0 rated entities — run `sportsbot fit`"
        else:
            eligible.append(s)

    weights: dict[str, float] = {}
    for s in eligible:
        stats = by_sport.get(s.sport, {})
        # Count CLV observations, never settled bets: mean_clv is averaged
        # over rows that carried a closing price, so 40 settled bets with 5
        # closing prices must not clear a 30-observation guard.
        n_clv = int(stats.get("n_clv", 0) or 0)
        mult = clv_multiplier(stats.get("mean_clv"), n_clv, min_bets)
        weights[s.sport] = s.prior_weight * mult

    total_w = sum(weights.values())
    sleeves: dict[str, dict] = {}
    allocated = 0.0
    for s in SLEEVES:
        stats = by_sport.get(s.sport, {})
        if s.sport in blocked:
            sleeves[s.sport] = {
                "weight": 0.0, "budget": 0.0, "active": False,
                "bound_by": blocked[s.sport], "evidence": s.evidence,
                "prior_weight": s.prior_weight,
                "mean_clv": stats.get("mean_clv"), "n": int(stats.get("n", 0) or 0),
            }
            continue
        share = weights[s.sport] / total_w if total_w > 0 else 0.0
        raw = bankroll * total_cap * share
        capped = min(raw, bankroll * sport_cap)
        n_clv = int(stats.get("n_clv", 0) or 0)
        mult = clv_multiplier(stats.get("mean_clv"), n_clv, min_bets)
        sleeves[s.sport] = {
            "weight": round(share, 4),
            "budget": round(capped, 2),
            "active": True,
            "bound_by": ("per-sport cap" if capped < raw - 1e-9
                         else "CLV-adjusted share" if mult != 1.0
                         else "evidence prior"),
            "clv_multiplier": round(mult, 3),
            "evidence": s.evidence,
            "prior_weight": s.prior_weight,
            "mean_clv": stats.get("mean_clv"),
            "n": int(stats.get("n", 0) or 0),
        }
        allocated += capped
    return {
        "bankroll": round(bankroll, 2),
        "sleeves": sleeves,
        "allocated": round(allocated, 2),
        "unallocated": round(bankroll - allocated, 2),
        "excluded": EXCLUDED,
    }
