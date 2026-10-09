"""Strategy arms, their measured evidence, and the capital each may risk.

An **arm** is one way of trading one sport: `sport/signal/style`, e.g.
`baseball/sharp/maker` (rest orders at the Pinnacle-anchored fair value)
or `tennis/model/taker` (cross the spread on the Elo model). Every fill is
tagged with its arm, so each arm accrues its own closing-line value, its
own Brier, its own gate. Capital goes to arms, not to sports, because the
evidence this repo has gathered separates exactly along that line:
models lose to the line (Results 2–9), takers pay over the close
(Result 14), and the only measured profits sit with resting orders.

Three sources of money, in priority order, each only ever tightening the
risk layer's caps:

1. **Manual** — `config/allocation.yaml` `weight: <fraction>` on an arm
   pins its share of the total exposure cap. The operator's call wins.
2. **Evidence** — an arm whose gate is green (>= 200 CLV-graded fills,
   mean net CLV with a market-clustered 95% CI above zero, Brier under
   0.25, fees verified) shares the remaining cap in proportion to the
   LOWER bound of its CLV interval. Not the mean: the lower bound is what
   the data guarantees.
3. **Learning** — an arm marked `learn: true` that has NOT passed its gate
   may risk an equal slice of `learning.budget_usd`, so that real fills
   accrue the evidence a gate needs. A rolling 7-day realised loss on the
   learning arms at or past `learning.weekly_loss_stop_usd` pauses all of
   them until the window rolls off. Learning money is a bounded tuition
   fee, never a position.

Everything else gets zero and the reason is written down. Losses never
raise an allocation anywhere here: a losing arm's CI widens or drops, its
budget falls, and the drawdown/daily-loss switches in `bot/risk.py` act on
top. Repositioning capital toward what is measured to work is this
module's whole job; chasing what just lost is not in it.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import yaml

from sportsbot.data.store import event_of
from sportsbot.signals.sharp import cluster_bootstrap_mean

log = logging.getLogger(__name__)

SIGNALS = ("model", "sharp")
STYLES = ("maker", "taker")
DEFAULT_PATH = "config/allocation.yaml"

MIN_GRADED_FOR_GATE = 200
MAX_BRIER = 0.25
SHARP_MODEL_NAME = "sharpline"


def arm_name(sport: str, signal: str, style: str) -> str:
    return f"{sport}/{signal}/{style}"


def parse_arm(name: str) -> tuple[str, str, str]:
    sport, signal, style = name.split("/")
    return sport, signal, style


def arm_of_intent(sport: str, reason: str) -> str:
    """The arm a BetIntent belongs to, from the strategy's reason string
    ("<model> p=… blend=… mid=… maker|taker")."""
    r = (reason or "").strip()
    signal = "sharp" if r.startswith(SHARP_MODEL_NAME) else "model"
    style = "maker" if r.endswith("maker") else "taker"
    return arm_name(sport, signal, style)


def arm_of_row(row: dict) -> str:
    """Arm for a bets row; fills recorded before arms existed were model-
    driven takers, which is what they are labelled."""
    arm = row.get("arm")
    if arm:
        return str(arm)
    return arm_name(str(row.get("sport") or "unknown"), "model", "taker")


# ---------------------------------------------------------------------------
# Configuration (the operator's file)
# ---------------------------------------------------------------------------
@dataclass
class ArmSetting:
    weight: Optional[float] = None   # None = automatic; a number pins the share
    learn: bool = False
    enabled: bool = True


@dataclass
class AllocationConfig:
    bankroll_mode: str = "config"          # config | equity
    learning_budget_usd: float = 0.0
    learning_weekly_loss_stop_usd: float = 0.0
    arms: dict[str, ArmSetting] = field(default_factory=dict)
    path: str = DEFAULT_PATH

    def setting(self, arm: str) -> ArmSetting:
        return self.arms.get(arm, ArmSetting())


def load_allocation(path: str = DEFAULT_PATH) -> AllocationConfig:
    """Read the operator's allocation file. A missing file means: every arm
    automatic, no learning money, bankroll from config -- the strictest
    reading, which is the right default for a file that controls money."""
    cfg = AllocationConfig(path=path)
    if not os.path.exists(path):
        return cfg
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}
    cfg.bankroll_mode = str(raw.get("bankroll_mode", "config")).lower()
    if cfg.bankroll_mode not in ("config", "equity"):
        raise ValueError(f"allocation.bankroll_mode must be config|equity, got {cfg.bankroll_mode!r}")
    learn = raw.get("learning", {}) or {}
    cfg.learning_budget_usd = max(0.0, float(learn.get("budget_usd", 0.0) or 0.0))
    cfg.learning_weekly_loss_stop_usd = max(
        0.0, float(learn.get("weekly_loss_stop_usd", 0.0) or 0.0))
    for name, spec in (raw.get("arms", {}) or {}).items():
        parse_arm(str(name))                       # validates the shape
        spec = spec or {}
        w = spec.get("weight")
        if w is not None:
            w = float(w)
            if not (0.0 <= w <= 1.0):
                raise ValueError(f"arm {name}: weight must be in [0, 1], got {w}")
        cfg.arms[str(name)] = ArmSetting(weight=w, learn=bool(spec.get("learn", False)),
                                         enabled=bool(spec.get("enabled", True)))
    return cfg


# ---------------------------------------------------------------------------
# Evidence and the gate, per arm
# ---------------------------------------------------------------------------
def arm_evidence(rows: Iterable[dict]) -> dict:
    """What one arm's settled fills say. CLV uses the sharp close where a
    fill has one (`bets.sharp_closing_price`) and the venue close otherwise;
    `sharp_share` says how much of the sample is graded against Pinnacle."""
    clvs, clusters, briers, pnl = [], [], [], 0.0
    n_settled = n_sharp = 0
    for r in rows:
        n_settled += 1
        pnl += float(r.get("pnl") or 0.0)
        entry = r.get("entry_price")
        close = r.get("sharp_closing_price")
        if close is not None:
            n_sharp += 1
        else:
            close = r.get("closing_price")
        if close is not None and entry is not None:
            clvs.append(float(close) - float(entry))
            clusters.append(event_of(r.get("market_id") or ""))
        if r.get("outcome") is not None and r.get("model_prob") is not None:
            briers.append((float(r["model_prob"]) - float(r["outcome"])) ** 2)
    ci = cluster_bootstrap_mean(clvs, clusters, n_boot=1000, seed=3) if clvs else None
    return {
        "n_settled": n_settled,
        "n_clv": len(clvs),
        "mean_clv": (ci["mean"] if ci else None),
        "clv_lo": (ci["lo"] if ci else None),
        "clv_hi": (ci["hi"] if ci else None),
        "brier": (sum(briers) / len(briers) if briers else None),
        "pnl": round(pnl, 2),
        "sharp_share": (n_sharp / len(clvs) if clvs else 0.0),
    }


def arm_gate(ev: dict, fees_verified: bool) -> dict:
    """Per-arm go-live gate. Same four criteria as `bot/gate.py`, with the
    CLV test sharpened to the interval: the lower bound must clear zero."""
    n = int(ev.get("n_clv") or 0)
    lo = ev.get("clv_lo")
    brier = ev.get("brier")
    criteria = [
        {"name": "sample size", "target": f">= {MIN_GRADED_FOR_GATE} CLV-graded fills",
         "value": str(n), "ok": n >= MIN_GRADED_FOR_GATE,
         "progress": min(1.0, n / MIN_GRADED_FOR_GATE)},
        {"name": "CLV interval", "target": "95% CI lower bound > 0",
         "value": ("not measured yet" if lo is None else f"{lo:+.4f}"),
         "ok": lo is not None and lo > 0.0,
         "progress": (1.0 if lo is not None and lo > 0.0 else 0.0)},
        {"name": "Brier", "target": f"< {MAX_BRIER}",
         "value": ("not measured yet" if brier is None else f"{brier:.4f}"),
         "ok": brier is not None and brier < MAX_BRIER,
         "progress": (1.0 if brier is not None and brier < MAX_BRIER else 0.0)},
        {"name": "fees verified", "target": "one tiny manual trade on the venue",
         "value": "verified" if fees_verified else "outstanding",
         "ok": bool(fees_verified), "progress": 1.0 if fees_verified else 0.0},
    ]
    return {"ready": all(c["ok"] for c in criteria), "criteria": criteria}


def learning_loss_7d(rows: Iterable[dict], learning_arms: set[str],
                     now: Optional[datetime] = None) -> float:
    """Realised PnL of the learning arms over the trailing seven days
    (negative = loss). Rows without a realised pnl do not count."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).isoformat()
    total = 0.0
    for r in rows:
        if arm_of_row(r) not in learning_arms or r.get("pnl") is None:
            continue
        if str(r.get("ts") or "") >= since:
            total += float(r["pnl"])
    return round(total, 2)


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------
def allocate_arms(bankroll: float, cfg: dict, alloc: AllocationConfig,
                  settled_rows: list[dict], running_arms: Iterable[str],
                  fees_verified: bool = False,
                  now: Optional[datetime] = None, mode: str = "live",
                  priceable_sports: Optional[Iterable[str]] = None) -> dict:
    """Dollar budget per arm, and what the runner needs from it: the
    budget per sport and the execution style each sport may use.

    `settled_rows` are the account's settled bets (any arm); `running_arms`
    are the arms the current config can actually produce fills for (one
    signal per sport, see `runner.load_models`; styles follow from
    `execution.post_inside_spread`). An arm the config cannot run gets no
    money however good its record, and says so.

    `mode="paper"` is the simulator: its fills cost nothing and are the
    evidence a gate needs, so every running, enabled arm without a manual
    weight shares the cap equally. `mode="live"` is real money and applies
    the three sources above in order; nothing else is funded.

    `priceable_sports`, when given, names the sports whose model can price
    a market right now (fitted ratings, or sharp lines on record). An arm
    in any other sport gets nothing this cycle: money parked on a sport
    that cannot be priced is money taken from one that can.
    """
    bank_cfg = cfg.get("bankroll", {})
    total_cap = max(0.0, bankroll) * float(bank_cfg.get("max_total_exposure", 0.50))
    sport_cap = max(0.0, bankroll) * float(bank_cfg.get("max_fraction_per_sport", 0.20))
    running = set(running_arms)
    priceable = None if priceable_sports is None else set(priceable_sports)

    by_arm: dict[str, list[dict]] = {}
    for r in settled_rows:
        by_arm.setdefault(arm_of_row(r), []).append(r)
    names = sorted(running | set(alloc.arms) | set(by_arm))

    arms: dict[str, dict] = {}
    manual_total = 0.0
    evidence_pool: dict[str, float] = {}
    learners: list[str] = []
    for name in names:
        setting = alloc.setting(name)
        ev = arm_evidence(by_arm.get(name, []))
        gate = arm_gate(ev, fees_verified)
        entry = {"budget": 0.0, "source": "", "evidence": ev, "gate": gate,
                 "manual_weight": setting.weight, "learn": setting.learn,
                 "enabled": setting.enabled, "running": name in running}
        arms[name] = entry
        if not setting.enabled:
            entry["source"] = "disabled in allocation.yaml"
        elif name not in running:
            entry["source"] = "not running: this signal/style is not what the config trades"
        elif priceable is not None and parse_arm(name)[0] not in priceable:
            entry["source"] = ("nothing can price this sport right now "
                               "(no fitted ratings / no sharp lines on record)")
        elif setting.weight is not None:
            entry["budget"] = setting.weight * total_cap
            entry["source"] = f"manual weight {setting.weight:.0%} of the exposure cap"
            manual_total += entry["budget"]
        elif mode == "paper":
            evidence_pool[name] = 1.0
            entry["source"] = "paper: accruing evidence (equal share)"
        elif gate["ready"]:
            evidence_pool[name] = max(0.0, float(ev["clv_lo"] or 0.0))
            entry["source"] = "evidence: gate green, share by CLV lower bound"
        elif setting.learn:
            learners.append(name)
            entry["source"] = "learning budget (gate not met)"
        else:
            entry["source"] = "paper only: gate not met and not marked learn"

    remaining = max(0.0, total_cap - manual_total)
    pool_total = sum(evidence_pool.values())
    for name, w in evidence_pool.items():
        arms[name]["budget"] = remaining * (w / pool_total) if pool_total > 0 else 0.0

    loss_7d = learning_loss_7d(settled_rows, set(learners), now) if learners else 0.0
    if mode == "paper":
        learners = []                      # paper has no tuition to budget
    paused = bool(learners and alloc.learning_weekly_loss_stop_usd > 0
                  and loss_7d <= -alloc.learning_weekly_loss_stop_usd)
    if learners:
        slice_ = (0.0 if paused else
                  min(alloc.learning_budget_usd, remaining) / len(learners))
        for name in learners:
            arms[name]["budget"] = slice_
            if paused:
                arms[name]["source"] = (f"learning PAUSED: 7-day realised loss "
                                        f"{loss_7d:+.2f} past the "
                                        f"{alloc.learning_weekly_loss_stop_usd:.2f} stop")

    # Per-sport totals, scaled down (never up) to the sport cap; then the
    # total cap, which manual weights could otherwise overshoot together.
    sport_budget: dict[str, float] = {}
    for name, e in arms.items():
        sport = parse_arm(name)[0]
        sport_budget[sport] = sport_budget.get(sport, 0.0) + e["budget"]
    for sport, tot in list(sport_budget.items()):
        if tot > sport_cap > 0 or (sport_cap == 0 and tot > 0):
            scale = (sport_cap / tot) if tot > 0 else 0.0
            for name, e in arms.items():
                if parse_arm(name)[0] == sport:
                    e["budget"] *= scale
                    e["source"] += " (scaled to the per-sport cap)"
            sport_budget[sport] = sport_cap
    grand = sum(e["budget"] for e in arms.values())
    if grand > total_cap and grand > 0:
        scale = total_cap / grand
        for e in arms.values():
            e["budget"] *= scale
        sport_budget = {s: v * scale for s, v in sport_budget.items()}
    for e in arms.values():
        e["budget"] = round(e["budget"], 2)
    sport_budget = {s: round(v, 2) for s, v in sport_budget.items()}

    style_by_sport: dict[str, str] = {}
    for sport in sport_budget:
        styles = {parse_arm(n)[2] for n, e in arms.items()
                  if parse_arm(n)[0] == sport and e["budget"] > 0}
        style_by_sport[sport] = ("both" if styles == {"maker", "taker"}
                                 else next(iter(styles)) if styles else "none")

    return {
        "bankroll": round(bankroll, 2),
        "total_cap": round(total_cap, 2),
        "sport_cap": round(sport_cap, 2),
        "arms": arms,
        "sport_budget": sport_budget,
        "style_by_sport": style_by_sport,
        "allocated": round(sum(e["budget"] for e in arms.values()), 2),
        "learning": {
            "budget_usd": alloc.learning_budget_usd,
            "weekly_loss_stop_usd": alloc.learning_weekly_loss_stop_usd,
            "arms": learners, "loss_7d": loss_7d, "paused": paused,
        },
        "bankroll_mode": alloc.bankroll_mode,
        "mode": mode,
        "path": alloc.path,
    }


def running_arms_for(cfg: dict) -> set[str]:
    """The arms this config can produce fills for: one signal per enabled
    sport (`sports.<sport>.signal`, default model) and the styles the
    execution settings allow (post_inside_spread -> maker and, when the
    book leaves no room, taker)."""
    out = set()
    sports_cfg = cfg.get("sports", {}) or {}
    post = bool(cfg.get("execution", {}).get("post_inside_spread", True))
    for sport, sc in sports_cfg.items():
        if not isinstance(sc, dict) or not sc.get("enabled", True):
            continue
        signal = str(sc.get("signal", "model")).lower()
        if signal not in SIGNALS:
            raise ValueError(f"sports.{sport}.signal must be one of {SIGNALS}")
        out.add(arm_name(sport, signal, "taker"))
        if post:
            out.add(arm_name(sport, signal, "maker"))
    return out


# ---------------------------------------------------------------------------
# The results ledger the dashboard shows (one line per measured verdict)
# ---------------------------------------------------------------------------
RESULTS: tuple[tuple[str, str, str], ...] = (
    ("1–3", "Kalshi MLB vs the price", "model carries no information the price lacks (beta 0.065, t 0.20, n 873); no drift to trade"),
    ("4", "Kalshi maker variant", "not measurably better; resting orders are not free on game series"),
    ("5", "Kalshi tennis bootstrap", "worse than a coin against the price (Brier 0.259 vs 0.202); bets lost ~18%"),
    ("6", "favourite–longshot bias", "none to trade after fees on either venue"),
    ("7", "weather", "coin > climatology > NWS > market; the market already contains the forecast"),
    ("8", "Polymarket MLB", "same as Kalshi, measured to a third of a point"),
    ("9", "Polymarket tennis", "model loses to the price; the one 'bias' was a look-ahead"),
    ("10", "cross-venue lead–lag", "Polymarket follows Kalshi (pooled t 5.2) — not a taker trade; maker experiment needs real orders"),
    ("11", "other sports / categories", "no cross-venue box in 156 pairs; the cheaper venue is the sharper one"),
    ("12", "crypto Up/Down", "DMI/ADX below a coin; spot fair value loses; complete sets are market making"),
    ("13", "other venues / arbitrage / HFT", "every class closed, fee-eaten, or a name-matching artefact"),
    ("14", "copying wallets", "informed MLB takers exist (+1.0 pt) but a copy nets −0.7 pt; tennis has none"),
)


def summarise_for_dashboard(port: dict) -> list[dict]:
    """Flat rows for rendering, sorted funded-first."""
    rows = []
    for name, e in port["arms"].items():
        ev = e["evidence"]
        rows.append({
            "arm": name, "budget": e["budget"], "source": e["source"],
            "running": e["running"], "enabled": e["enabled"], "learn": e["learn"],
            "manual_weight": e["manual_weight"],
            "gate_ready": e["gate"]["ready"],
            "gate_progress": (sum(c["progress"] for c in e["gate"]["criteria"])
                              / len(e["gate"]["criteria"])),
            "n_clv": ev["n_clv"], "n_settled": ev["n_settled"],
            "mean_clv": ev["mean_clv"], "clv_lo": ev["clv_lo"], "clv_hi": ev["clv_hi"],
            "brier": ev["brier"], "pnl": ev["pnl"], "sharp_share": ev["sharp_share"],
        })
    rows.sort(key=lambda r: (-r["budget"], not r["running"], r["arm"]))
    return rows


def effective_bankroll(alloc: AllocationConfig, config_amount: float,
                       equity: Optional[float]) -> float:
    """What stakes are sized against. In equity mode profits roll into the
    bankroll and losses roll out of it; the result is never negative, and
    when no equity is known the config amount stands."""
    if alloc.bankroll_mode == "equity" and equity is not None and math.isfinite(equity):
        return max(0.0, float(equity))
    return float(config_amount)
