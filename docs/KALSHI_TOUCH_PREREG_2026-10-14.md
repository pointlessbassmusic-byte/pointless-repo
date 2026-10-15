# Pre-registration: Result 15's touch-maker on Kalshi itself (2026-10-14)

Committed before any Kalshi trade tape was pulled for this test.

## Why

The live maker test (`docs/MAKER_TEST.md`) runs on Kalshi. The evidence
behind it, Result 15 and its addenda (about +0.5 to +0.6 points of CLV
per fill on MLB, NHL and NFL; college football and soccer more), was
measured on the international Polymarket CLOB. Kalshi has a different
fee (makers pay a small fee there), a different crowd and a different
queue. Kalshi publishes every trade with its taker side, so the same
construction can be run there.

## Data

- **Series:**
  - MLB: `KXMLBGAME`
  - NHL: `KXNHLGAME`
  - NFL: `KXNFLGAME`
  - College football: `KXNCAAFGAME`
  - Soccer: `KXEPLGAME`, `KXUCLGAME`, `KXLALIGAGAME`, `KXMLSGAME`
- **Markets:** settled yes/no with volume > 0, closing in the last 30
  days, every ticker of every game.
- **Pre-game cut (no in-play prints):**
  - MLB: the first pitch encoded in the ticker.
  - Everything else: `occurrence_datetime − 4 h`. Kalshi's occurrence
    time is the scheduled end; this cut sits at least about 30 minutes
    before kickoff in every sport checked.
- **Window:** prints in the 24 h before the cut, at most 20,000 per
  market; any truncation is reported.

## Construction (Result 15, population baseline)

- **Orders:** for every taker print at YES price p, a maker order rests
  one tick behind it on the side the taker took:
  - a taker who bought YES at p → a YES bid at p − 0.01;
  - a taker who sold YES at p → a YES ask at p + 0.01.
- **Fills:** an order fills if a later print, before the cut and within
  the deadline (120 s, 30 min, or until the cut), trades through its
  level on the opposite taker side. It fills at its own price.
- **CLV per fill:** the last print before the cut, in the fill's side
  terms, minus the fill price.
- **Realised return per $:** settlement minus the fill price minus the
  Kalshi maker fee (`kalshi_maker_fee_per_share`), divided by the fill
  price.
- **Split:** games ordered by cut time, the first 70% train and the last
  30% test. Results are reported on test, with intervals bootstrapped by
  game (2,000 draws, seed 9).

## Decision, per sport (MLB, NHL, NFL, NCAAF, soccer pooled)

- **PASS:** at the 120 s deadline on the test half, there are at least 30
  test games with fills, the 95% lower bound of CLV per fill is above 0,
  and the realised return per $ point estimate is above 0.
- **FAIL:** a test-half CLV interval entirely at or below 0.
- **INCONCLUSIVE:** anything else.

## What a result changes

- **A PASS** keeps the sport in the live test's touch arms on Kalshi; a
  sport not yet there (NCAAF, soccer) is added to the paper sim first.
- **A FAIL** removes that sport from the Kalshi touch arms. A sport
  that FAILs here cannot be run on Kalshi's book on the strength of the
  Polymarket number.

## Amendment 1 (11:24Z, before any analysis; nothing scored)

The first fetch did 11 markets in a minute, about 13,000 prints each, on
a universe of about 1,500 markets: hours. The window becomes the **6 h**
before the cut, at most **5,000 prints** per market (newest first, so a
truncated window keeps the prints closest to the start). Result 15's
pre-game fills cluster in the last hours; the 30-minute and to-cut
deadlines are unaffected inside a 6 h window. The fetch restarts clean.

## Amendment 2 (after the main read; one refinement, chosen on TRAIN only)

On the train half (70% of games), fills at prices under 0.30 had
negative CLV on MLB, NHL and NCAAF, and the worst realised returns in
every sport. Fills at 0.50 and above carried the edge. One rule is chosen
there, and nothing else will be tried: **rest only when the fill price
is ≥ 0.50.**

Confirmation is on the TEST half: the live arms' sports (MLB, NHL, NFL)
pooled, 120 s deadline, game-clustered bootstrap (2,000 draws, seed 9).

- **CONFIRMED:** the ≥ 0.50 subset has CLV lower bound > 0 **and** a
  realised return per $ above both 0 and the all-fills return on the
  same test games.
- Otherwise **NOT CONFIRMED**, and the rule is dropped.
