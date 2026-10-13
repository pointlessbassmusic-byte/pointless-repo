"""Two-book logger: pairing across venues and the lead-lag report."""
import math
import sqlite3
import time

from sportsbot.core.types import Exchange, MarketInfo, Sport
from sportsbot.substrate_bridge import twobook as tb


def _mi(exchange, mid, home, away, sport, token="", vol=0.0):
    return MarketInfo(exchange=exchange, market_id=mid, yes_token_id=token, no_token_id="",
                      question="", slug="", sport=sport, home=home, away=away,
                      start_time=None, close_time=None, active=True, tick_size=0.01,
                      min_order_size=1, neg_risk=False, meta={"volume24hr": vol})


def test_pairs_match_on_names_and_record_orientation():
    k = [_mi(Exchange.KALSHI, "KXATPMATCH-1-SIN", "Jannik Sinner", "Carlos Alcaraz", Sport.TENNIS),
         _mi(Exchange.KALSHI, "KXWTAMATCH-2-SWI", "Iga Swiatek", "Aryna Sabalenka", Sport.TENNIS)]
    p = [_mi(Exchange.POLYMARKET, "c1", "Carlos Alcaraz", "Jannik Sinner", Sport.TENNIS, "t1", 500.0),
         _mi(Exchange.POLYMARKET, "c2", "Iga Swiatek", "Aryna Sabalenka", Sport.TENNIS, "t2", 900.0),
         _mi(Exchange.POLYMARKET, "c3", "Nobody", "Else", Sport.TENNIS, "t3", 100.0)]
    pairs = tb.match_pairs("tennis", k, p)
    by = {pr.kalshi_ticker: pr for pr in pairs}
    assert set(by) == {"KXATPMATCH-1-SIN", "KXWTAMATCH-2-SWI"}
    assert by["KXATPMATCH-1-SIN"].pm_yes_is_kalshi_yes is False   # PM YES = Alcaraz, Kalshi YES = Sinner
    assert by["KXWTAMATCH-2-SWI"].pm_yes_is_kalshi_yes is True
    assert pairs[0].pm_condition == "c2"                            # highest Polymarket volume first


def _synthetic_db(path, lag_steps=2, step=20.0, n=400, flip=False):
    """Kalshi mid random-walks; Polymarket's book equals Kalshi's `lag_steps`
    steps ago. If flip, Polymarket is stored in the opposite orientation."""
    import random
    random.seed(7)
    conn = sqlite3.connect(path)
    conn.executescript(tb.SCHEMA)
    conn.execute("INSERT INTO pairs VALUES (1,'tennis','KXATPMATCH-X-A','A','B','c','t','%s','%s',?,?)"
                 % (("B", "A") if flip else ("A", "B")), (0 if flip else 1, time.time()))
    t0 = 1_800_000_000.0
    mids = [0.55]
    for _ in range(n - 1):
        mids.append(min(0.9, max(0.1, mids[-1] + random.choice((-0.01, 0.0, 0.0, 0.01)))))
    rows = []
    for i, m in enumerate(mids):
        t = t0 + i * step + 1.0
        rows.append((t, 1, "kalshi", round(m - 0.005, 4), round(m + 0.005, 4), 100, 100))
        pmm = mids[max(0, i - lag_steps)]
        b, a = round(pmm - 0.005, 4), round(pmm + 0.005, 4)
        if flip:
            b, a = round(1 - a, 4), round(1 - b, 4)
        rows.append((t + 0.5, 1, "polymarket", b, a, 100, 100))
    conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_report_finds_the_lag_and_only_in_the_lagging_direction(tmp_path):
    db = str(tmp_path / "tb.sqlite")
    _synthetic_db(db, lag_steps=2)
    r = tb.report(db, step=20.0, horizons=(1, 2, 4), thetas=(0.01,))
    assert r["pairs_usable"] == 1
    b_pm, _ = r["leadlag"][2]["pm_toward_kalshi"]
    b_k, se_k = r["leadlag"][2]["kalshi_toward_pm"]
    assert b_pm > 0.8                       # Polymarket closes the whole gap within its lag
    assert abs(b_k) < 3 * se_k + 0.05       # Kalshi does not move toward Polymarket
    cell = r["markout"][("buy_pm", 2, 0.01)]
    assert cell["n"] > 10 and cell["mean"] > 0   # hitting the lagging ask pays before fees swamp it
    assert "Polymarket->Kalshi" in tb.format_report(r)


def test_report_reorients_a_flipped_polymarket_book(tmp_path):
    db = str(tmp_path / "tb.sqlite")
    _synthetic_db(db, lag_steps=2, flip=True)
    r = tb.report(db, step=20.0, horizons=(2,), thetas=(0.01,))
    b_pm, _ = r["leadlag"][2]["pm_toward_kalshi"]
    assert b_pm > 0.8 and not math.isnan(b_pm)


def test_default_sports_are_keys_the_venues_know():
    """`discover` passed 'mlb' to clients whose sport keys are 'tennis',
    'baseball' and 'table_tennis', so every default run logged tennis only
    and MLB silently never entered a two-book sample."""
    import inspect

    from sportsbot.bot.runner import SPORT_KEYS
    from sportsbot.cli import twobook_log

    for fn in (tb.TwoBookLogger.discover, tb.TwoBookLogger.run):
        default = inspect.signature(fn).parameters["sports"].default
        assert set(default) <= set(SPORT_KEYS), (fn.__name__, default)
    cli_default = inspect.signature(twobook_log).parameters["sports"].default
    assert set(cli_default.default.split(",")) <= set(SPORT_KEYS)
