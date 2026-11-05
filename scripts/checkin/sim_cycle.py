"""One paper Runner cycle for the daily check-in read-out.

The server runs the continuous paper book (`sportsbot-sim.service`); this is
the sandbox read-out of the same config: one cycle, then let its resting
touch orders live out their TTL so each becomes evidence (a tape fill or a
shadowed cancel) instead of a row left open when the process exits.

    python3 scripts/checkin/sim_cycle.py [config/sim.yaml]

Prints SUMMARY (the cycle dict), PORTFOLIO (portfolio:last sport_budget and
style_by_sport) and DRAIN (fills booked, cancelled, still resting — the last
must be 0).
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sportsbot.bot.runner import Runner  # noqa: E402
from sportsbot.cli import _setup  # noqa: E402

log = logging.getLogger("sim_cycle")


def main(config: str = "config/sim.yaml") -> None:
    runner = Runner(_setup(config))
    # as run_forever does: book what a previous process left, sweep the rest
    for step in (runner.executor.restore_open_orders, runner.executor.sweep_orphans):
        try:
            step()
        except Exception:
            log.exception("%s failed; continuing", step.__name__)
    summary = runner.cycle()
    print("SUMMARY", json.dumps(summary, default=str))
    port = runner.store.get_kv("portfolio:last") or {}
    print("PORTFOLIO sport_budget", json.dumps(port.get("sport_budget")),
          "style_by_sport", json.dumps(port.get("style_by_sport")))
    print("DRAIN", json.dumps(runner.drain_resting(), default=str))


if __name__ == "__main__":
    main(*sys.argv[1:2])
