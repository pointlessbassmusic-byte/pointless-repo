# Is Kalshi tennis beaten by Pinnacle's closing line? — pre-registration (2026-10-08)

Both research reports rank this first: grade everything against a sharp
de-vigged close. Committed before any Pinnacle data was obtained.

## Data
- **Pinnacle closing odds:** tennis-data.co.uk 2026 files (ATP `2026/2026.xlsx`,
  WTA `2026w/2026.xlsx`), columns PSW/PSL. Shin de-vig
  (`sportsbot.core.odds.remove_vig_shin`).
- **Kalshi:** the settled KXATPMATCH/KXWTAMATCH markets, Aug 5 – Oct 5 2026,
  whose full trade tapes were already pulled for SETTLE_VPIN (about 2,900
  markets).
  - Kalshi pre-match close = the last trade before the tape-defined in-play cut
    (`prematch_cut`).
  - Ask proxy = the last YES-taker trade price before the cut; bid proxy = the
    last NO-taker trade price before the cut.
- **Matching:** same date ±1 day, with the player surname and first initial
  matching both players.
- **Disclosed blind spot:** neither source time-stamps its close to the second.
  Both are "just before the start", so they are compared as near-contemporaneous.

## H1 — accuracy (descriptive, decides nothing on its own)
- Brier score of Pinnacle-Shin vs Kalshi's pre-match last trade, on the same
  matched matches (one per match, from the YES market).
- Paired difference with t-stat. Also reported: a 50/50 blend.

## H2 — tradable (decision)
- **Rule:** at Kalshi's last pre-match quote, buy YES if
  pin_fair − ask_proxy − fee ≥ 0.02, or NO if
  (1 − pin_fair) − (1 − bid_proxy) − fee ≥ 0.02.
  - fee = 0.07·p(1−p), taker. Held to settlement. One trade per match.
- **Split:** matches before Sep 5 vs from Sep 5.
- **Pass:** ≥ 100 trades in each half; mean P&L > 0 with t ≥ 2 in the second half;
  first half mean > 0; ROI ≥ 1%.
- **Caveat:** the Pinnacle *close* is used as fair value at Kalshi's close, so H2
  is an upper bound for a bot that only sees Pinnacle a few minutes earlier.
  A FAIL is therefore decisive; a PASS needs a forward test with live Pinnacle
  snapshots taken at least 10 minutes out.

## Amendment 1 (2026-10-08, before any Pinnacle data was obtained)
These are harness fixes from a smoke test on a *synthetic* odds file built from
Kalshi's own prices. No real Pinnacle number had been seen.
- **Scope:** "Kalshi ATP/WTA" means exactly the KXATPMATCH-/KXWTAMATCH- tickers.
  The tape directories also hold Challenger/ITF markets, which tennis-data does
  not cover, so those are excluded.
- **Opponent check:** newer Kalshi titles read "<Player> wins" with no opponent.
  The opponent counts as matched if their surname is in the title, or the first 3
  letters of their surname form one half of the event code (`…26OCT05WONHEW`).
- **Names:** initials are read only after the last surname token
  ("O'Connell C." → connell/c). Exact-duplicate tennis-data rows are collapsed.
- **Smoke result:** 45/45 synthetic matches; Brier gap −0.0004 (the expected
  ≈0, since "Pinnacle" was Kalshi there). Plumbing check only, not evidence.

## Amendment 2 (2026-10-08, before either odds file was downloaded): data window
tennis-data.co.uk blocks datacenter IPs, and the user cannot open it either. The
only copies obtainable are Internet Archive snapshots, both captured 2026-08-03:
- `web.archive.org/web/20260803192930/http://www.tennis-data.co.uk/2026/2026.xlsx` (ATP)
- `web.archive.org/web/20260803192940/http://www.tennis-data.co.uk/2026w/2026.xlsx` (WTA)

They end before the original Aug 5 – Oct 5 window, so the test moves to the
period they cover. Everything else (rules, fee, pass bars) is unchanged.
- **Kalshi markets:** KXATPMATCH/KXWTAMATCH from `/historical/markets`, closing
  2026-01-01 to 2026-08-01 inclusive, with volume > 0. This is 11,060 sides by
  close month (counted from Kalshi metadata only).
  - One side per event, chosen at random. Then a random sample of 1,600 events.
    Both draws use `random.Random(7)` over the ticker-sorted list.
  - Trade tapes come from `/historical/trades`.
- **Split:** close before 2026-04-16 vs from 2026-04-16. That is about half the
  sides each, by the month counts above.
- **Status:** this run is the decision test. If later tennis-data files become
  obtainable, the original Aug–Oct window becomes a second out-of-sample check,
  and that rerun cannot rescue a FAIL here.
- **Disclosed:** an archived file can miss corrections the site made after
  Aug 3. Rows without PSW/PSL are dropped, as before.
