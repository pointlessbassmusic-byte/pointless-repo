"""Sharp-line CLV harness: each case pins a way grading against Pinnacle
could lie and look like an edge (or hide one).
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sportsbot.core.odds import remove_vig_two_way, shin_devig
from sportsbot.core.types import Exchange, MarketInfo, Sport
from sportsbot.data.store import Store
from sportsbot.signals import sharp as sh

T0 = datetime(2026, 10, 7, 23, 10, tzinfo=timezone.utc)   # first pitch


# ---------------------------------------------------------------------------
# Shin de-vig
# ---------------------------------------------------------------------------
def test_shin_sums_to_one_and_favours_the_favourite_more_than_proportional():
    fav, dog = shin_devig([1 / 1.25, 1 / 4.0])
    assert abs(fav + dog - 1.0) < 1e-9
    pf, _ = remove_vig_two_way(1 / 1.25, 1 / 4.0)
    assert fav > pf                       # longshot carries more of the margin
    assert fav < 1 / 1.25                 # but never above its implied price


def test_shin_identity_on_a_fair_book_and_rejects_bad_input():
    assert shin_devig([0.6, 0.4]) == [0.6, 0.4]
    with pytest.raises(ValueError):
        shin_devig([0.6])
    with pytest.raises(ValueError):
        shin_devig([0.0, 1.1])


# ---------------------------------------------------------------------------
# Odds API payload
# ---------------------------------------------------------------------------
def _payload(home="Los Angeles Dodgers", away="San Francisco Giants",
             commence=T0, books=None):
    books = books or {"pinnacle": (1.60, 2.45)}
    return [{
        "id": "ev1", "sport_key": "baseball_mlb",
        "commence_time": commence.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home_team": home, "away_team": away,
        "bookmakers": [
            {"key": k, "title": k, "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": home, "price": ph}, {"name": away, "price": pa}]}]}
            for k, (ph, pa) in books.items()],
    }]


def test_parse_events_keeps_two_way_books_only():
    payload = _payload(books={"pinnacle": (1.60, 2.45), "draftkings": (1.57, 2.50)})
    # a three-way soccer-style book must be dropped, not mis-devigged
    payload[0]["bookmakers"].append({"key": "bet365", "markets": [{"key": "h2h", "outcomes": [
        {"name": "Los Angeles Dodgers", "price": 2.0},
        {"name": "San Francisco Giants", "price": 3.0},
        {"name": "Draw", "price": 3.5}]}]})
    evs = sh.parse_events(payload)
    assert len(evs) == 1
    assert set(evs[0].books) == {"pinnacle", "draftkings"}
    assert evs[0].commence_time == T0
    rows = sh.quote_rows(evs)
    pin = next(r for r in rows if r["bookmaker"] == "pinnacle")
    assert abs(pin["home_fair"] + pin["away_fair"] - 1.0) < 1e-9
    assert pin["overround"] == pytest.approx(1 / 1.60 + 1 / 2.45 - 1.0)


def test_client_reads_credit_headers_and_stops_on_401(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sports"):
            return httpx.Response(200, json=[
                {"key": "tennis_atp_shanghai", "active": True, "has_outrights": False},
                {"key": "tennis_atp_winner", "active": True, "has_outrights": True},
                {"key": "baseball_mlb", "active": True, "has_outrights": False}],
                headers={"x-requests-remaining": "480"})
        if "baseball_mlb" in request.url.path:
            return httpx.Response(200, json=_payload(),
                                  headers={"x-requests-remaining": "479",
                                           "x-requests-last": "1"})
        return httpx.Response(401, json={"message": "bad key"})

    client = sh.OddsApiClient("k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.tennis_keys() == ["tennis_atp_shanghai"]     # outrights excluded
    evs = client.h2h("baseball_mlb", ["pinnacle"])
    assert len(evs) == 1 and client.credits_remaining == 479
    with pytest.raises(httpx.HTTPStatusError):
        client.h2h("tennis_atp_shanghai", ["pinnacle"])


# ---------------------------------------------------------------------------
# Collector: cadence and budget
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self, remaining=1000, fail=None):
        self.calls = []
        self.credits_remaining = remaining
        self.credits_used_last = 1
        self.fail = fail

    def tennis_keys(self):
        return ["tennis_atp_shanghai"]

    def h2h(self, key, books):
        self.calls.append(key)
        if self.fail:
            raise self.fail
        if key == "baseball_mlb":
            return sh.parse_events(_payload())
        return []


def _store(tmp_path):
    return Store(str(tmp_path / "s.sqlite"))


def test_snapshot_respects_interval_and_credit_reserve(tmp_path):
    store = _store(tmp_path)
    clock = {"now": T0 - timedelta(hours=5)}
    client = _FakeClient(remaining=1000)
    coll = sh.SharpCollector(store, sh.SharpConfig(snapshot_interval_minutes=15,
                                                   credits_reserve=50),
                             client, now=lambda: clock["now"])
    s1 = coll.snapshot([Sport.BASEBALL, Sport.TENNIS])
    assert s1["calls"] == 2 and s1["rows"] == 1 and s1["credits"] == 1000
    assert sorted(client.calls) == ["baseball_mlb", "tennis_atp_shanghai"]
    # not due yet: no spend
    clock["now"] += timedelta(minutes=5)
    assert coll.snapshot([Sport.BASEBALL])["skipped"] == "not due"
    assert len(client.calls) == 2
    clock["now"] += timedelta(minutes=11)
    assert coll.snapshot([Sport.BASEBALL])["calls"] == 1
    # below reserve: paused, and only one warning per day
    store.set_kv(sh.KV_CREDITS, 10)
    clock["now"] += timedelta(minutes=30)
    assert coll.snapshot([Sport.BASEBALL])["skipped"] == "budget"
    assert len(client.calls) == 3


def test_snapshot_without_a_client_or_for_table_tennis_spends_nothing(tmp_path):
    store = _store(tmp_path)
    coll = sh.SharpCollector(store, sh.SharpConfig(), None)
    assert coll.snapshot([Sport.BASEBALL])["skipped"] == "no api key"
    client = _FakeClient()
    coll = sh.SharpCollector(store, sh.SharpConfig(), client)
    assert coll.snapshot([Sport.TABLE_TENNIS])["skipped"] == "no sport keys"
    assert client.calls == []


def test_snapshot_stops_spending_after_a_429(tmp_path):
    store = _store(tmp_path)
    req = httpx.Request("GET", "https://x")
    err = httpx.HTTPStatusError("quota", request=req,
                                response=httpx.Response(429, request=req))
    client = _FakeClient(fail=err)
    coll = sh.SharpCollector(store, sh.SharpConfig(), client)
    s = coll.snapshot([Sport.BASEBALL, Sport.TENNIS])
    assert s["calls"] == 0 and len(client.calls) == 1   # broke out after the first
    # the failed attempt still took the slot: no retry storm on a dead key
    assert coll.snapshot([Sport.BASEBALL])["skipped"] == "not due"
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Matching and the close
# ---------------------------------------------------------------------------
def _ev(eid, home, away, commence=T0):
    return {"event_id": eid, "home_team": home, "away_team": away,
            "commence_time": commence.isoformat()}


def test_match_event_handles_visitor_first_listing_and_time_slack():
    events = [_ev("a", "Los Angeles Dodgers", "San Francisco Giants"),
              _ev("b", "New York Yankees", "Boston Red Sox")]
    # Polymarket lists the visitor as outcomes[0]: YES side is the book's away
    meta = {"home": "San Francisco Giants", "away": "Los Angeles Dodgers",
            "start_time": T0.isoformat()}
    ev, yes_is_home = sh.match_event(meta, events)
    assert ev["event_id"] == "a" and yes_is_home is False
    # same names a day later is a different game
    meta["start_time"] = (T0 + timedelta(days=1)).isoformat()
    assert sh.match_event(meta, events) is None
    # unknown start (Kalshi tennis) still matches on names alone
    meta["start_time"] = None
    assert sh.match_event(meta, events)[0]["event_id"] == "a"


def test_match_event_refuses_ambiguous_and_partial_matches():
    events = [_ev("a", "Jannik Sinner", "Carlos Alcaraz"),
              _ev("b", "Jannik Sinner", "Carlos Alcaraz", T0 + timedelta(hours=2))]
    meta = {"home": "Jannik Sinner", "away": "Carlos Alcaraz", "start_time": None}
    assert sh.match_event(meta, events) is None          # two candidates tie
    meta = {"home": "Jannik Sinner", "away": "Novak Djokovic", "start_time": None}
    assert sh.match_event(meta, events[:1]) is None      # one side unmatched


def _quotes(ts_probs):
    return [{"ts": ts.isoformat(), "home_fair": p, "away_fair": 1 - p}
            for ts, p in ts_probs]


def test_close_is_last_quote_before_the_lead_never_inside_it():
    qs = _quotes([(T0 - timedelta(hours=3), 0.60),
                  (T0 - timedelta(minutes=30), 0.62),
                  (T0 - timedelta(minutes=5), 0.80),     # inside the lead: in-play
                  (T0 + timedelta(minutes=40), 0.95)])
    close = sh.sharp_close(qs, T0, min_lead_minutes=10)
    assert close["home_fair"] == 0.62
    assert sh.sharp_close(qs[2:], T0, 10) is None       # only in-play prints: no close
    assert sh.quote_at(qs, T0 - timedelta(hours=2))["home_fair"] == 0.60


# ---------------------------------------------------------------------------
# Grading end to end on a store
# ---------------------------------------------------------------------------
def _seed(store, yes_is_book_home=True):
    """One MLB game: book quotes 0.60/0.40 at T0-3h and 0.62/0.38 at T0-30m,
    then an in-play print. Venue market lists the visitor first unless
    `yes_is_book_home`."""
    home, away = "Los Angeles Dodgers", "San Francisco Giants"
    for ts, p in ((T0 - timedelta(hours=3), 0.60), (T0 - timedelta(minutes=30), 0.62),
                  (T0 - timedelta(minutes=2), 0.85)):
        store.record_sharp_quotes([{
            "sport_key": "baseball_mlb", "event_id": "ev1",
            "commence_time": T0.isoformat(), "home_team": home, "away_team": away,
            "bookmaker": "pinnacle", "home_implied": p + 0.02,
            "away_implied": 1 - p + 0.02, "home_fair": p, "away_fair": 1 - p,
            "overround": 0.04}], ts=ts.isoformat())
    if yes_is_book_home:
        store.record_market("M1", "polymarket", "baseball", home, away, T0)
    else:
        store.record_market("M1", "polymarket", "baseball", away, home, T0)


def _decision(store, ts, action, mid, model, side=None, price=None, stake=None):
    store.conn.execute(
        "INSERT INTO decisions (ts, account, market_id, sport, title, action, side,"
        " model_prob, market_prob, price, edge, stake, reason)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts.isoformat(), "sim", "M1", "baseball", "M1", action, side, model, mid,
         price, None, stake, "t"))
    store.conn.commit()


def test_grading_orients_sides_subtracts_fees_and_scores_skips(tmp_path):
    store = _store(tmp_path)
    _seed(store, yes_is_book_home=False)      # venue YES = Giants (book away)
    t = T0 - timedelta(hours=2)
    # bet NO (= Dodgers) at 0.55; sharp close for Dodgers is 0.62
    _decision(store, t, "bet", mid=0.45, model=0.35, side="no", price=0.55, stake=5.5)
    # skip where the model prefers YES (Giants) at mid 0.45; sharp close YES = 0.38
    _decision(store, t, "skip", mid=0.45, model=0.50)
    grader = sh.Grader(store, sh.SharpConfig(), fee_for=lambda ex, mid: (lambda p: 0.01))
    graded, funnel = grader.grade_decisions("sim")
    assert funnel["graded"] == 2 and len(graded) == 2
    bet, skip = graded
    assert bet.kind == "bet" and bet.side == "no"
    assert bet.sharp_close_yes == pytest.approx(0.38)
    assert bet.clv_gross == pytest.approx(0.62 - 0.55)
    assert bet.clv_net == pytest.approx(0.62 - 0.55 - 0.01)
    assert bet.sharp_then_yes == pytest.approx(0.40)      # quote at decision time
    assert skip.kind == "skip" and skip.side == "yes" and skip.price == 0.45
    assert skip.clv_net == pytest.approx(0.38 - 0.45 - 0.01)  # right to skip


def test_decisions_without_metadata_or_close_fall_out_of_the_funnel(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    _decision(store, T0 - timedelta(hours=1), "skip", mid=0.5, model=0.6)
    store.conn.execute("UPDATE decisions SET market_id='UNKNOWN'")
    store.conn.commit()
    _, funnel = sh.Grader(store, sh.SharpConfig()).grade_decisions("sim")
    assert funnel == {"decisions": 1, "no_meta": 1, "no_event": 0,
                      "no_close": 0, "graded": 0}
    # a market whose only sharp quotes are in-play has no close
    store.conn.execute("UPDATE decisions SET market_id='M1'")
    store.conn.execute("DELETE FROM sharp_quotes WHERE home_fair < 0.8")
    store.conn.commit()
    _, funnel = sh.Grader(store, sh.SharpConfig()).grade_decisions("sim")
    assert funnel["no_close"] == 1 and funnel["graded"] == 0


def test_grade_bets_writes_the_sharp_close_in_the_side_frame(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    bid = store.record_bet("M1", "baseball", "no", 0.35, 0.42, 4.2, 10, 0.03,
                           "polymarket", "paper")
    store.settle_bet(bid, outcome=1, pnl=5.8, closing_price=0.41)
    grader = sh.Grader(store, sh.SharpConfig(), fee_for=lambda ex, mid: (lambda p: 0.0))
    out = grader.grade_bets("paper", write=True)
    assert len(out) == 1 and out[0].kind == "settled"
    assert out[0].sharp_close_side == pytest.approx(0.38)   # NO side of 0.62
    row = store.settled_bets()[0]
    assert row["sharp_closing_price"] == pytest.approx(0.38)
    assert row["closing_price"] == 0.41                      # venue close untouched
    # the backfill pass skips rows that already carry one
    assert grader.grade_bets("paper", write=True, only_missing=True) == []


def test_paper_fills_are_charged_the_quoted_venues_fee(tmp_path):
    """Paper bets record exchange='paper'; a Kalshi paper book must still
    be graded with Kalshi's fee, which the market metadata remembers."""
    store = _store(tmp_path)
    _seed(store)
    store.conn.execute("UPDATE market_meta SET exchange='kalshi'")
    store.conn.commit()
    store.record_bet("M1", "baseball", "yes", 0.65, 0.60, 6.0, 10, 0.03,
                     "paper", "paper")
    seen = []

    def fee_for(exchange, market_id):
        seen.append(exchange)
        return lambda p: 0.0

    sh.Grader(store, sh.SharpConfig(), fee_for=fee_for).grade_bets("paper")
    assert seen == ["kalshi"]
    # and the default fee table knows both venues
    assert sh.default_fee_for("kalshi", "KXMLBGAME-X-LAD")(0.5) == pytest.approx(0.07 * 0.5 * 0.25)
    assert sh.default_fee_for("polymarket", "0xabc")(0.5) == pytest.approx(0.05 * 0.25)


def test_with_sharp_closes_only_swaps_rows_that_have_one():
    rows = [{"closing_price": 0.5, "sharp_closing_price": 0.4},
            {"closing_price": 0.5, "sharp_closing_price": None}]
    out = sh.with_sharp_closes(rows)
    assert [r["closing_price"] for r in out] == [0.4, 0.5]
    assert rows[0]["closing_price"] == 0.5                   # input not mutated


def test_sharp_close_feeds_the_tighten_only_layer():
    """A sport positive against its own venue close but negative against
    Pinnacle must tighten -- and the swap can never loosen a sport."""
    from sportsbot.bot.positions import adaptive_overrides

    rows = [{"sport": "baseball", "entry_price": 0.50, "closing_price": 0.52,
             "sharp_closing_price": 0.48} for _ in range(40)]
    edge, stake, tight = adaptive_overrides(rows, {}, 0.03, {}, 50.0)
    assert tight == []
    edge, stake, tight = adaptive_overrides(sh.with_sharp_closes(rows), {}, 0.03, {}, 50.0)
    assert tight == ["baseball"] and edge["baseball"] == pytest.approx(0.05)
    assert stake["baseball"] == 25.0


# ---------------------------------------------------------------------------
# Statistics and the verdict
# ---------------------------------------------------------------------------
def test_cluster_bootstrap_widens_when_rows_share_an_event():
    vals = [0.02] * 50 + [-0.02] * 50
    independent = sh.cluster_bootstrap_mean(vals, [str(i) for i in range(100)], seed=1)
    paired = sh.cluster_bootstrap_mean(vals, [str(i // 50) for i in range(100)], seed=1)
    assert independent["mean"] == pytest.approx(0.0)
    assert independent["hi"] - independent["lo"] < 0.02
    assert paired["lo"] is None or paired["hi"] - paired["lo"] > independent["hi"] - independent["lo"]
    assert sh.cluster_bootstrap_mean([], []) is None


def test_ols_recovers_a_known_slope():
    xs = [0.0, 0.1, 0.2, 0.3, 0.4]
    ys = [0.0, 0.05, 0.10, 0.15, 0.20]
    fit = sh.ols(xs, ys)
    assert fit["beta"] == pytest.approx(0.5) and fit["se"] == pytest.approx(0.0)
    assert sh.ols([1.0, 1.0, 1.0], [1, 2, 3]) is None


def _g(i, kind, clv, event=None, model=None):
    return sh.Graded(kind=kind, ref_id=i, ts="t", sport="baseball", market_id=f"M{i}",
                     event_id=event or f"e{i}", side="yes", price=0.5,
                     venue_mid_yes=0.5, model_yes=model, sharp_close_yes=0.5 + clv + 0.01,
                     sharp_then_yes=0.5 + clv / 2, fee=0.01)


def test_verdict_is_insufficient_below_the_preregistered_sample():
    rep = sh.summarise([_g(i, "bet", 0.03) for i in range(20)], [], {})
    assert rep["verdict"].startswith("INSUFFICIENT")
    assert rep["taken"]["mean"] == pytest.approx(0.03)


def test_verdict_passes_only_when_the_ci_clears_zero():
    pos = [_g(i, "bet", 0.03 if i % 3 else -0.01) for i in range(1000)]
    rep = sh.summarise(pos, [], {})
    assert rep["verdict"].startswith("PASS")
    noise = [_g(i, "bet", 0.03 if i % 2 else -0.03) for i in range(1000)]
    rep = sh.summarise(noise, [], {})
    assert rep["verdict"].startswith("FAIL")
    assert rep["gap"]["n"] == 1000 and rep["gap"]["beyond_fee"] == 1000


def test_summary_reports_beta_and_expected_vs_realised():
    dec = [_g(i, "skip", 0.02 * (i % 5 - 2), model=0.5 + 0.04 * (i % 5 - 2))
           for i in range(50)]
    bets = [sh.Graded(kind="settled", ref_id=i, ts="t", sport="baseball",
                      market_id=f"M{i}", event_id=f"e{i}", side="yes", price=0.5,
                      venue_mid_yes=0.5, model_yes=0.6, sharp_close_yes=0.55,
                      sharp_then_yes=0.5, fee=0.0, stake=5.0,
                      pnl=(5.0 if i % 2 else -5.0), outcome=i % 2) for i in range(10)]
    rep = sh.summarise(dec, bets, {})
    assert rep["beta"]["beta"] == pytest.approx(0.5)
    e = rep["expected_vs_realised"]
    assert e["n"] == 10 and e["expected_roi"] == pytest.approx(0.1)
    assert e["realised_roi"] == pytest.approx(0.0)
    text = sh.format_report(rep, sh.SharpConfig())
    assert "beta of" in text and "verdict:" in text


# ---------------------------------------------------------------------------
# Runner integration
# ---------------------------------------------------------------------------
def test_runner_tick_records_metadata_and_snapshots_only_scanned_sports(tmp_path):
    from types import SimpleNamespace

    from sportsbot.bot.runner import Runner

    store = _store(tmp_path)
    client = _FakeClient()
    cfg = sh.SharpConfig()
    stub = SimpleNamespace(
        store=store, sharp_cfg=cfg, mode="paper",
        sharp=sh.SharpCollector(store, cfg, client),
        _sharp_tick=Runner._sharp_tick, _sharp_backfill=Runner._sharp_backfill,
        _sharp_fee_for=lambda ex, mid: (lambda p: 0.0),
    )
    m = MarketInfo(exchange=Exchange.POLYMARKET, market_id="M1", sport=Sport.BASEBALL,
                   home="San Francisco Giants", away="Los Angeles Dodgers",
                   start_time=T0)
    summary = stub._sharp_tick(stub, [SimpleNamespace(market=m)])
    assert summary["calls"] == 1 and client.calls == ["baseball_mlb"]
    assert store.market_meta("M1")["home"] == "San Francisco Giants"
    assert stub._sharp_backfill(stub) == 0        # no bets yet, never raises


def test_runner_tick_is_a_noop_when_disabled(tmp_path):
    from types import SimpleNamespace

    from sportsbot.bot.runner import Runner

    store = _store(tmp_path)
    cfg = sh.SharpConfig(enabled=False)
    stub = SimpleNamespace(store=store, sharp_cfg=cfg,
                           sharp=sh.SharpCollector(store, cfg, _FakeClient()),
                           _sharp_tick=Runner._sharp_tick)
    m = MarketInfo(exchange=Exchange.POLYMARKET, market_id="M1", sport=Sport.BASEBALL,
                   home="A", away="B")
    assert stub._sharp_tick(stub, [SimpleNamespace(market=m)]) is None
    assert store.market_meta("M1") is None


def test_config_parses_bookmakers_from_list_or_string():
    assert sh.SharpConfig.from_cfg({"sharp": {"bookmakers": "pinnacle, betfair_ex_eu"}}).bookmakers \
        == ("pinnacle", "betfair_ex_eu")
    assert sh.SharpConfig.from_cfg({}).bookmakers == ("pinnacle",)
    assert sh.SharpConfig.from_cfg({"sharp": {"enabled": False}}).enabled is False
