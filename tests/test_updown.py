import sqlite3
from sportsbot.substrate_bridge import updown as ud


def test_report_scores_the_favoured_side_against_resolution(tmp_path):
    db = str(tmp_path / "ud.sqlite")
    c = sqlite3.connect(db)
    c.executescript(ud.SCHEMA)
    ep = 1_800_000_000
    c.execute("INSERT INTO windows VALUES (?,?,?,?,?,?,?)", (ep, "BTC", "btc-updown-5m-x", "tok", 100000.0, 1, ep + 400))
    # 20 s left, spot +8 bps: UP favoured; book still 0.90/0.92 -> buying UP at 0.92 nets ~ +0.0750 after fee
    c.execute("INSERT INTO ticks VALUES (?,?,?,?,?,?,?)", (ep + 280, ep, 100080.0, 0.90, 0.92, 100, 100))
    # 200 s left, spot -3 bps: DOWN favoured; UP book 0.52/0.54 -> buy DOWN at 1-0.52=0.48; window resolved UP -> lose
    c.execute("INSERT INTO ticks VALUES (?,?,?,?,?,?,?)", (ep + 100, ep, 99970.0, 0.52, 0.54, 100, 100))
    c.commit()
    c.close()
    r = ud.report(db)
    assert r["windows_resolved"] == 1
    late = r["cells"][(30, 10)]
    assert late["n"] == 1 and late["win_rate"] == 1.0
    assert abs(late["net_per_contract"] - (1 - 0.92 - ud.fee(0.92))) < 1e-9
    early = r["cells"][(300, 5)]
    assert early["win_rate"] == 0.0 and early["net_per_contract"] < -0.48
    assert "net/contract" in ud.format_report(r)
