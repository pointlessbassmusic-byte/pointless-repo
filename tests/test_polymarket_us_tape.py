"""Polymarket US websocket tape: parsing, coverage, the client read, the collector."""
import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from sportsbot.core.types import Exchange, MarketInfo
from sportsbot.exchanges import polymarket_us_tape as ust
from sportsbot.exchanges.base import TapeGap
from sportsbot.exchanges.polymarket_us import PolymarketUSClient

SECRET = base64.b64encode(bytes(range(32))).decode()


def _trade(slug="nhl-a-b", px="0.555", qty="2", t="2026-10-19T20:00:00.123456789Z",
           intent="ORDER_INTENT_SELL_LONG", tid="x1"):
    # the docs' markets-websocket trade shape (camelCase, string enums)
    return {"requestId": "t1-0", "subscriptionType": "SUBSCRIPTION_TYPE_TRADE",
            "trade": {"marketSlug": slug, "price": {"value": px, "currency": "USD"},
                      "quantity": {"value": qty, "currency": "USD"}, "tradeTime": t, "tradeId": tid,
                      "maker": {"side": "ORDER_SIDE_BUY", "intent": "ORDER_INTENT_BUY_LONG"},
                      "taker": {"side": "ORDER_SIDE_SELL", "intent": intent}}}


def test_parse_trade_reads_the_documented_shape_and_the_aggressor_side():
    slug, p = ust.parse_trade(_trade())
    assert slug == "nhl-a-b" and p.price_yes == 0.555 and p.size == 2.0 and p.trade_id == "x1"
    assert p.ts == datetime(2026, 10, 19, 20, 0, 0, 123456, tzinfo=timezone.utc)
    assert p.taker_side == "no"                          # the aggressor sold YES
    assert ust.parse_trade(_trade(intent="ORDER_INTENT_BUY_SHORT"))[1].taker_side == "no"
    assert ust.parse_trade(_trade(intent="ORDER_INTENT_BUY_LONG"))[1].taker_side == "yes"
    snake = {"trade": {"market_slug": "s", "price": "0.4", "qty": "1", "trade_time": "2026-10-19T20:00:00Z"}}
    assert ust.parse_trade(snake)[1].price_yes == 0.4 and ust.parse_trade(snake)[1].taker_side is None
    for bad in ({"heartbeat": {}}, _trade(px="1.0"), _trade(qty="0"), _trade(t="nope"), _trade(slug=""), "x"):
        assert ust.parse_trade(bad) is None


def test_subscriptions_are_chunked_at_the_venue_limit():
    msgs = ust.subscribe_messages([f"s{i}" for i in range(250)], "t1")
    assert [len(m["subscribe"]["marketSlugs"]) for m in msgs] == [100, 100, 50]
    assert {m["subscribe"]["subscriptionType"] for m in msgs} == {"SUBSCRIPTION_TYPE_TRADE"}
    assert len({m["subscribe"]["requestId"] for m in msgs}) == 3


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_coverage_needs_the_subscription_before_the_window_and_a_live_session(tmp_path):
    clock = Clock()
    st = ust.TapeStore(str(tmp_path / "t.sqlite"), clock=clock)
    sid = st.open_session()
    st.subscribed(sid, ["a"])                            # coverage from 1000 + SUB_LAG_S
    clock.t = 1100.0
    st.touch(sid)
    assert st.covered("a", since=1010.0)
    assert not st.covered("a", since=1001.0)             # before the subscription took effect
    assert not st.covered("b", since=1010.0)             # never subscribed
    clock.t = 1100.0 + ust.COVERAGE_GRACE_S + 1           # the session went silent
    assert not st.covered("a", since=1010.0)
    sid2 = st.open_session()                             # a reconnect does not cover the gap before it
    st.subscribed(sid2, ["a"])
    assert not st.covered("a", since=1010.0)
    assert st.covered("a", since=clock.t + ust.SUB_LAG_S)


def _market(slug="nhl-a-b"):
    return MarketInfo(exchange=Exchange.POLYMARKET_US, market_id=slug, yes_token_id=f"{slug}#long",
                      no_token_id=f"{slug}#short", question="q", slug=slug)


def test_client_reads_the_tape_and_refuses_a_window_it_does_not_cover(tmp_path):
    t0 = datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc)
    clock = Clock(t0.timestamp() - 60)
    st = ust.TapeStore(str(tmp_path / "t.sqlite"), clock=clock)
    sid = st.open_session()
    st.subscribed(sid, ["nhl-a-b"])
    for i, px in enumerate((0.50, 0.51)):
        _, p = ust.parse_trade(_trade(px=str(px), t=(t0 + timedelta(seconds=10 * (i + 1))).isoformat(),
                                      tid=f"x{i}"))
        assert st.add("nhl-a-b", p)
    assert not st.add("nhl-a-b", p)                      # a duplicate is stored once
    st.commit()
    clock.t = t0.timestamp() + 30
    st.touch(sid)
    c = PolymarketUSClient(key_id="", secret_key="", tape=st)
    got = c.recent_trades(_market(), t0)
    assert [x.price_yes for x in got] == [0.51, 0.50]     # newest first
    with pytest.raises(TapeGap):
        c.recent_trades(_market(), t0 - timedelta(hours=1))
    with pytest.raises(TapeGap):
        c.recent_trades(_market("other"), t0)


def test_client_without_a_tape_reports_a_gap_not_an_empty_tape(tmp_path, monkeypatch):
    """Regression (rehearsal 2026-10-19): returning [] with no record made the
    executor's shadow store tape_through 0.0 on every US order -- a guess
    written as data. No record is a gap like any other."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ust.ENV_DB, raising=False)
    with pytest.raises(TapeGap):
        PolymarketUSClient(key_id="", secret_key="").recent_trades(_market(), datetime.now(timezone.utc))


class FakeWS:
    def __init__(self, inbox):
        self.inbox, self.sent = list(inbox), []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, m):
        self.sent.append(json.loads(m))

    async def recv(self):
        if not self.inbox:
            raise ConnectionError("closed")
        return self.inbox.pop(0)


def test_collector_signs_subscribes_records_and_adds_new_slugs(tmp_path):
    clock = Clock()
    st = ust.TapeStore(str(tmp_path / "t.sqlite"), clock=clock)
    ws = FakeWS([json.dumps({"heartbeat": {}}), json.dumps(_trade()), json.dumps(_trade()),
                 json.dumps({"requestId": "t1-0", "error": "bad slug"}), "not json"])
    seen = {}
    # discovery runs out after two calls (StopIteration): later refreshes must keep the session
    lists = iter([["nhl-a-b"], ["nhl-a-b", "nfl-c-d"]])

    def connect(url, headers):
        seen.update(url=url, headers=headers)
        return ws

    coll = ust.TapeCollector(st, "kid", SECRET, lambda: next(lists), connect=connect, refresh_s=0.0, clock=clock)
    with pytest.raises(ConnectionError):
        asyncio.run(coll.session())
    assert seen["url"] == "wss://api.polymarket.us/v1/ws/markets"
    assert seen["headers"]["X-PM-Access-Key"] == "kid" and seen["headers"]["X-PM-Signature"]
    subs = [m["subscribe"]["marketSlugs"] for m in ws.sent]
    assert subs == [["nhl-a-b"], ["nfl-c-d"]]           # the refresh subscribes only the new slug
    assert coll.stats["prints"] == 1 and coll.stats["errors"] == 1
    assert st.status()["prints"] == 1 and st.status()["slugs_subscribed"] == 2
    assert st.conn.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 2


def test_collector_reconnects_with_backoff_until_the_deadline(tmp_path):
    clock = Clock()
    st = ust.TapeStore(str(tmp_path / "t.sqlite"), clock=clock)
    naps = []

    async def sleep(s):
        naps.append(s)
        clock.t += s

    def connect(url, headers):
        raise OSError("refused")

    coll = ust.TapeCollector(st, "kid", SECRET, lambda: ["s"], connect=connect, sleep=sleep, clock=clock,
                             max_backoff=8.0)
    stats = asyncio.run(coll.run(hours=0.01))           # 36 s of fake time
    assert stats["errors"] == len(naps) >= 3
    base = [int(n) for n in naps]                        # 1, 2, 4, 8, 8 ... plus jitter < 1
    assert base[:4] == [1, 2, 4, 8] and max(base) <= 8


def test_cli_without_keys_records_nothing_and_creates_no_tape(tmp_path, monkeypatch):
    """No keys: the command exits cleanly and leaves NO file behind (an empty
    tape would turn every read into a TapeGap instead of no-tape-no-fill)."""
    from typer.testing import CliRunner

    from sportsbot.cli import app

    monkeypatch.chdir(tmp_path)
    for k in ("POLYMARKET_US_KEY_ID", "POLYMARKET_US_SECRET_KEY", ust.ENV_DB):
        monkeypatch.delenv(k, raising=False)
    res = CliRunner().invoke(app, ["us-tape"])
    assert res.exit_code == 0 and "not set" in res.output
    assert not (tmp_path / "data" / "us_tape.sqlite").exists()
    res = CliRunner().invoke(app, ["us-tape", "--status"])
    assert res.exit_code == 0 and "no tape" in res.output


def test_doctor_reads_the_tape_state_for_the_us_venue_only(tmp_path, monkeypatch):
    from sportsbot.bot.doctor import FAIL, PASS, WARN, check_tape

    monkeypatch.setenv(ust.ENV_DB, str(tmp_path / "t.sqlite"))
    assert check_tape({"exchange": "kalshi"}) == []
    assert check_tape({"exchange": "polymarket_us"})[0].level == WARN
    assert check_tape({"exchange": "polymarket_us", "mode": "live"})[0].level == FAIL
    st = ust.TapeStore(str(tmp_path / "t.sqlite"))
    sid = st.open_session()
    st.subscribed(sid, ["a"])
    (c,) = check_tape({"exchange": "polymarket_us", "mode": "live"})
    assert c.level == PASS and "1 slugs" in c.detail


def test_a_reader_sees_the_recorders_writes_on_the_same_file(tmp_path):
    path = str(tmp_path / "t.sqlite")
    writer, reader = ust.TapeStore(path), ust.TapeStore(path)
    assert writer.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    sid = writer.open_session()
    writer.subscribed(sid, ["nhl-a-b"])
    writer.add("nhl-a-b", ust.parse_trade(_trade())[1])
    writer.commit()
    assert reader.status()["prints"] == 1
