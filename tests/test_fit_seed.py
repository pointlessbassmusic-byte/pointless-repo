"""`fit baseball` on a host the Stats API refuses: install the committed seed."""

import json
import shutil

import httpx
import yaml
from typer.testing import CliRunner

from sportsbot.cli import app
from sportsbot.data.mlb_data import MLBStatsClient


def _cfg(tmp_path, seeds):
    cfg = {"sports": {"baseball": {"home_advantage_elo": 24.0}},
           "storage": {"ratings_dir": str(tmp_path / "ratings"), "seeds_dir": str(seeds),
                       "sqlite_path": str(tmp_path / "t.sqlite")},
           "logging": {"level": "WARNING"}}
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


def _refuse(self, seasons=3):
    req = httpx.Request("GET", "https://statsapi.mlb.com/api/v1/schedule")
    raise httpx.HTTPStatusError("Client error '406 Not Acceptable'", request=req,
                                response=httpx.Response(406, request=req))


def test_fit_baseball_installs_the_seed_when_the_api_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(MLBStatsClient, "history", _refuse)
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    shutil.copyfile("config/seeds/baseball.json", seeds / "baseball.json")
    result = CliRunner().invoke(app, ["fit", "baseball", "-c", _cfg(tmp_path, seeds)])
    assert result.exit_code == 0, result.output
    assert "refused this host" in result.output and "HTTPStatusError" in result.output
    # the provenance line echoes the committed seed's own metadata; read it
    # from the file, since the daily check-in refreshes the seed (a fixed
    # date here broke the suite on the first refresh after it was written)
    meta = json.load(open("config/seeds/baseball.json"))["seed_meta"]
    assert f"fitted {meta['fitted_at'][:10]}" in result.output
    assert f"{meta['n_games']} games" in result.output
    installed = json.load(open(tmp_path / "ratings" / "baseball.json"))
    assert len(installed["elo"]) == 31 and installed["last_season"] == 2026


def test_fit_baseball_without_a_seed_still_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(MLBStatsClient, "history", _refuse)
    result = CliRunner().invoke(app, ["fit", "baseball", "-c", _cfg(tmp_path, tmp_path / "none")])
    assert result.exit_code != 0
    assert not (tmp_path / "ratings" / "baseball.json").exists()


def test_committed_seed_loads_into_the_model_and_carries_provenance():
    from sportsbot.engine.baseball import BaseballModel

    d = json.load(open("config/seeds/baseball.json"))
    assert d["seed_meta"]["n_games"] > 5000 and d["seed_meta"]["fitted_at"].startswith("2026")
    m = BaseballModel()
    m.load("config/seeds/baseball.json")       # extra key is ignored by load()
    assert len(m.elo.ratings) == 31 and m._last_season == 2026
