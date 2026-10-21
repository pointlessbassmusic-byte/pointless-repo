"""MLB Stats API client: a 406 walks the fallback requests and keeps the first that answers 200."""

import httpx
import pytest

from sportsbot.data import mlb_data as M


def _transport(accept):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((str(req.url).split("?")[0], req.headers.get("user-agent", "")))
        if accept(req):
            return httpx.Response(200, json={"dates": [{"games": [{"gamePk": 1}]}]})
        return httpx.Response(406, text="Not Acceptable")
    return httpx.MockTransport(handler), calls


def test_406_falls_back_to_a_plain_agent_and_sticks(monkeypatch):
    monkeypatch.delenv("SPORTSBOT_STATSAPI_BASE", raising=False)
    t, calls = _transport(lambda r: r.headers.get("user-agent", "").startswith("curl/"))
    c = M.MLBStatsClient(transport=t)
    assert c._get("/schedule", {"sportId": 1}) == {"dates": [{"games": [{"gamePk": 1}]}]}
    assert calls[0][1].startswith("Mozilla/") and calls[1][1].startswith("curl/")
    n = len(calls)
    c._get("/schedule", {"sportId": 1})
    assert len(calls) == n + 1 and calls[-1][1].startswith("curl/")       # the working request is kept


def test_406_falls_back_to_http_when_https_is_refused(monkeypatch):
    monkeypatch.delenv("SPORTSBOT_STATSAPI_BASE", raising=False)
    t, calls = _transport(lambda r: r.url.scheme == "http")
    c = M.MLBStatsClient(transport=t)
    assert c._get("/schedule")["dates"]
    assert c.base.startswith("http://")


def test_406_everywhere_still_raises(monkeypatch):
    monkeypatch.delenv("SPORTSBOT_STATSAPI_BASE", raising=False)
    monkeypatch.setattr(M.MLBStatsClient._get.retry, "stop", M.stop_after_attempt(1))
    t, _ = _transport(lambda r: False)
    with pytest.raises(httpx.HTTPStatusError):
        M.MLBStatsClient(transport=t)._get("/schedule")


def test_base_override(monkeypatch):
    monkeypatch.setenv("SPORTSBOT_STATSAPI_BASE", "https://mirror.example/api/v1")
    t, calls = _transport(lambda r: True)
    M.MLBStatsClient(transport=t)._get("/teams")
    assert calls[0][0] == "https://mirror.example/api/v1/teams"
