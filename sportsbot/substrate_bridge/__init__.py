"""Bridge between the sportsbot trading stack and the substrate engine
(substrate/ — see substrate/HANDOFF.md).

Shadow-mode only by the substrate protocol: these adapters move DATA
(decision-time probabilities, outcomes) — nothing here places orders or
routes substrate output back into the bot.
"""

from sportsbot.substrate_bridge.bot_events import export_events_csv
from sportsbot.substrate_bridge.kalshi_weather import WeatherSnapshotService

__all__ = ["export_events_csv", "WeatherSnapshotService"]
