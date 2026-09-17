"""Config loading: config.yaml + .env."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    @property
    def live(self) -> bool:
        return bool(self.raw.get("live", False))

    @property
    def scan_interval_sec(self) -> int:
        return int(self.raw.get("scan_interval_sec", 900))

    @property
    def sports(self) -> list:
        return self.raw.get("sports", [])

    @property
    def odds(self) -> dict:
        return self.raw.get("odds", {})

    @property
    def model(self) -> dict:
        return self.raw.get("model", {})

    @property
    def strategy(self) -> dict:
        return self.raw.get("strategy", {})

    @property
    def db_path(self) -> Path:
        p = Path(self.raw.get("storage", {}).get("db_path", "data/bot.db"))
        return p if p.is_absolute() else ROOT / p

    @property
    def odds_api_key(self) -> str:
        return os.environ.get("ODDS_API_KEY", "")

    @property
    def polymarket_private_key(self) -> str:
        return os.environ.get("POLYMARKET_PRIVATE_KEY", "")

    @property
    def polymarket_funder(self) -> str:
        return os.environ.get("POLYMARKET_FUNDER_ADDRESS", "")


def load_config(path: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_path = path or ROOT / "config.yaml"
    with open(cfg_path) as f:
        return Config(raw=yaml.safe_load(f) or {})
