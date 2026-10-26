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


def _tape_with(path, samples, prints, covered=(), now=None):
    """A tape whose live session has covered `covered` since an hour before
    the prints, with the clock 30 s after them (`now`, default T)."""
    now = now or datetime(2026, 10, 26, 20, 0, 30, tzinfo=timezone.utc)
    clock = Clock()
    clock.t = (now - timedelta(hours=1)).timestamp()
    store = ust.TapeStore(str(path / "tape.sqlite"), clock=clock)
    sid = store.open_session()
    store.subscribed(sid, list(covered))
    for m in samples:
        store.sample(json.dumps(m))
    for m in prints:
        store.add(*ust.parse_trade(m))
    clock.t = now.timestamp()
    store.touch(sid)
    store.commit()
    return store


def _bbo(last):
    """slug -> the public /bbo lastTradePx (long frame)."""
    return lambda slug: {"last_px": last.get(slug)}


T = "2026-10-26T20:00:00Z"


def test_verify_passes_when_fields_parse_and_covered_prints_equal_the_last_trade(tmp_path):
    msgs = [_trade(slug="a", px="0.300", t=T, tid="1"), _trade(slug="b", px="0.700", t=T, tid="2")]
    res = ust.verify(_tape_with(tmp_path, msgs, msgs, covered=("a", "b")), _bbo({"a": 0.30, "b": 0.70}))
    assert res["ok"] and res["frame"]["agree"] == 2 and res["parsed"] == res["trade_samples"] == 2


def test_verify_flags_a_flipped_frame_unknown_fields_and_intents(tmp_path):
    good = _trade(slug="a", px="0.300", t=T, intent="ORDER_INTENT_SOMETHING_NEW")
    renamed = {"trade": {"slugOfMarket": "a", "px_value": "0.3", "qty": "1", "tradeTime": T}}
    res = ust.verify(_tape_with(tmp_path, [good, renamed], [good], covered=("a",)), _bbo({"a": 0.70}))
    assert not res["ok"] and res["frame"]["flipped"] == 1
    text = " ".join(res["problems"])
    assert "1 of 2 trade messages did not parse" in text and "slugOfMarket" in text
    assert "ORDER_INTENT_SOMETHING_NEW" in text and "not in YES terms" in text


def test_verify_only_trusts_covered_prints_away_from_one_half(tmp_path):
    near = _trade(slug="c", px="0.505", t=T)
    res = ust.verify(_tape_with(tmp_path, [near], [near], covered=("c",)), _bbo({"c": 0.495}))
    assert not res["ok"] and res["frame"]["near_half"] == 1 and "unconfirmed" in " ".join(res["problems"])
    other = tmp_path / "other"
    other.mkdir()
    gap = _trade(slug="d", px="0.2", t=T)                    # never subscribed: its last print may be stale
    res = ust.verify(_tape_with(other, [gap], [gap]), _bbo({"d": 0.8}))
    assert res["frame"]["not_covered"] == 1 and res["frame"]["flipped"] == 0 and not res["ok"]
    empty = tmp_path / "empty"
    empty.mkdir()
    res = ust.verify(_tape_with(empty, [], []), _bbo({}))
    assert not res["ok"] and res["problems"] == ["no trade message recorded yet"]


def test_cli_verify_reads_the_tape_and_the_public_bbo_without_keys(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sportsbot.cli import app

    monkeypatch.chdir(tmp_path)
    for k in ("POLYMARKET_US_KEY_ID", "POLYMARKET_US_SECRET_KEY", ust.ENV_DB):
        monkeypatch.delenv(k, raising=False)
    res = CliRunner().invoke(app, ["us-tape", "--verify"])
    assert res.exit_code == 0 and "no tape" in res.output
    (tmp_path / "data").mkdir()
    now = datetime.now(timezone.utc)                         # the CLI reads coverage on the real clock
    m = _trade(slug="a", px="0.300", t=(now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _tape_with(tmp_path / "data", [m], [m], covered=("a",), now=now).conn.close()
    (tmp_path / "data" / "tape.sqlite").rename(tmp_path / "data" / "us_tape.sqlite")
    monkeypatch.setattr(PolymarketUSClient, "get_bbo", lambda self, slug: {"last_px": 0.30})
    res = CliRunner().invoke(app, ["us-tape", "--verify"])
    assert res.exit_code == 0 and "us-tape verify: OK" in res.output
