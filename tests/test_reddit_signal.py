"""Reddit team-news recorder + the pre-registered news-veto read-out
(docs/NEWS_VETO_PREREG_2026-10-13.md). Offline: the API is a mock transport."""

import json
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest

from sportsbot.core.types import Sport
from sportsbot.data.store import Store
from sportsbot.signals import reddit
from sportsbot.signals.reddit import (
    NewsRecorder,
    RedditClient,
    mentions,
    news_terms,
    news_veto_test,
    team_aliases,
)


def test_news_terms_and_aliases():
    assert "scratched" in news_terms("Rays: Arozarena scratched from tonight's lineup")
    assert "lineup" in news_terms("Rays: Arozarena scratched from tonight's lineup")
    assert news_terms("Game thread: Rays at Guardians") == []
    assert team_aliases("Tampa Bay Rays") == {"tampa bay rays", "rays"}
    assert "red sox" in team_aliases("Boston Red Sox") and "sox" not in team_aliases("Boston Red Sox")
    # a team-sport code resolves through data/teams.py to the full name + nickname
    assert {"detroit red wings", "red wings"} <= team_aliases("DET", Sport.HOCKEY)
    assert team_aliases(None) == set()
    assert mentions("Red Wings place Larkin on IR", {"red wings"})
    assert not mentions("Raysburg news", {"rays"})


def _transport(posts, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/access_token":
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["authorization"] == "bearer tok"
        assert request.url.host == "oauth.reddit.com"
        return httpx.Response(200, json={"data": {"children": [{"data": p} for p in posts]}})
    return httpx.MockTransport(handler)


def test_client_uses_app_only_oauth_and_refuses_without_keys(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        RedditClient()
    seen = []
    c = RedditClient("id", "secret", username="me",
                     http=httpx.Client(transport=_transport([{"id": "a", "title": "x"}], seen)))
    assert c.new_posts("baseball") == [{"id": "a", "title": "x"}]
    c.new_posts("hockey")
    assert sum(r.url.path == "/api/v1/access_token" for r in seen) == 1   # token cached
    assert "(by /u/me)" in seen[-1].headers["user-agent"]


class _Fake:
    def __init__(self, by_sub):
        self.by_sub = by_sub

    def new_posts(self, sub):
        return self.by_sub.get(sub, [])


def test_recorder_dedupes_and_stamps_first_seen(tmp_path):
    posts = [{"id": "p1", "title": "Guardians: Ramirez scratched", "created_utc": 900.0},
             {"id": "p2", "title": "Game thread", "created_utc": 950.0}]
    rec = NewsRecorder(str(tmp_path / "r.sqlite"), _Fake({"baseball": posts}), ["baseball"])
    assert rec.poll_once(now=1000.0, pause=0) == 2
    assert rec.poll_once(now=2000.0, pause=0) == 0                 # already stored
    rows = rec.conn.execute("SELECT id, first_seen, flagged FROM reddit_posts ORDER BY id").fetchall()
    assert rows == [("p1", 1000.0, 1), ("p2", 1000.0, 0)]          # first sighting kept


def _fill(store, n, market, t, clv, home="Tampa Bay Rays", away="Cleveland Guardians"):
    """One live touch fill at price 0.50 on `market`, closing at 0.50 + clv."""
    store.record_market(market, "kalshi", "baseball", home, away, None)
    iso = datetime.fromtimestamp(t, timezone.utc).isoformat()
    store.record_order(client_id=f"c{n}", order_id=f"o{n}", market_id=market, side="yes",
                       price=0.50, size=1.0, filled=1.0, status="filled",
                       raw={"arm": "baseball/touch/maker", "placed_at": iso, "first_fill_at": iso})
    bet = store.record_bet(market_id=market, sport="baseball", side="yes", model_prob=0.5,
                           entry_price=0.50, stake=0.5, size=1.0, edge=0.0, exchange="kalshi",
                           mode="live", arm="baseball/touch/maker")
    store.settle_bet(bet, 1, 0.5, closing_price=0.50 + clv)


def test_news_veto_flags_only_news_the_bot_could_have_seen(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    rdb = str(tmp_path / "r.sqlite")
    conn = sqlite3.connect(rdb)
    conn.executescript(reddit.SCHEMA)
    t0 = 1_800_000_000.0
    # recorder span: from t0 to t0 + 10 days
    conn.execute("INSERT INTO reddit_posts VALUES ('start','baseball',?,?,'x',0,'')", (t0, t0))
    conn.execute("INSERT INTO reddit_posts VALUES ('end','baseball',?,?,'x',0,'')",
                 (t0 + 864000, t0 + 864000))
    n = 0
    for i in range(4):          # flagged: news 10 min before, seen before the fill
        t = t0 + 7200 + i * 86400
        conn.execute("INSERT INTO reddit_posts VALUES (?,?,?,?,?,1,'scratched')",
                     (f"f{i}", "baseball", t - 600, t - 590, "Rays: Diaz scratched"))
        _fill(store, n, f"KXMLBGAME-F{i}", t, clv=-0.04)
        n += 1
    for i in range(4):          # quiet: no news; one only seen AFTER the fill
        t = t0 + 50000 + i * 86400
        if i == 0:
            conn.execute("INSERT INTO reddit_posts VALUES ('late','baseball',?,?,?,1,'scratched')",
                         (t - 600, t + 30, "Rays: Diaz scratched"))
        _fill(store, n, f"KXMLBGAME-Q{i}", t, clv=+0.02)
        n += 1
    _fill(store, n, "KXMLBGAME-EARLY", t0 + 60, clv=0.0)      # inside the first hour: excluded
    conn.commit()

    rep = news_veto_test(store, rdb, mode="live", min_per_group=4)
    assert rep["n_flagged"] == 4 and rep["n_quiet"] == 4
    assert rep["excluded"]["outside_recording"] == 1
    assert rep["mean_clv_flagged"] == pytest.approx(-0.04) and rep["mean_clv_quiet"] == pytest.approx(0.02)
    assert rep["verdict"] == "PASS" and rep["diff_quiet_minus_flagged"]["lo"] > 0
    assert news_veto_test(store, rdb, mode="live")["verdict"] == "PENDING"   # default bar is 20
    assert news_veto_test(store, rdb, mode="paper")["n_flagged"] == 0        # per book
    json.dumps(rep)   # report is plain data


def test_veto_is_never_imported_by_the_trading_path():
    import pathlib

    for path in pathlib.Path("sportsbot/bot").glob("*.py"):
        assert "signals.reddit" not in path.read_text(), f"{path} imports the reddit signal"
