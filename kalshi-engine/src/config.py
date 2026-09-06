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
    def use_demo(self) -> bool:
        return bool(self.raw.get("use_demo", True))

    @property
    def read_prod(self) -> bool:
        # demo market data is synthetic; default to real prod data for public reads
        return bool(self.raw.get("read_prod", True))

    @property
    def scan_interval_sec(self) -> int:
        return int(self.raw.get("scan_interval_sec", 900))

    @property
    def markets(self) -> dict:
        return self.raw.get("markets", {})

    @property
    def substrate(self) -> dict:
        return self.raw.get("substrate", {})

    @property
    def strategy(self) -> dict:
        return self.raw.get("strategy", {})

    @property
    def db_path(self) -> Path:
        p = Path(self.raw.get("storage", {}).get("db_path", "data/engine.db"))
        return p if p.is_absolute() else ROOT / p

    @property
    def api_key_id(self) -> str:
        return os.environ.get("KALSHI_API_KEY_ID", "")

    @property
    def private_key_path(self) -> str:
        return os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")


def load_config(path: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_path = path or ROOT / "config.yaml"
    with open(cfg_path) as f:
        return Config(raw=yaml.safe_load(f) or {})
