# Pre-registration: the Reddit news veto on touch-maker fills (2026-10-13)

Committed before the recorder has stored a single post and before the
live maker test (`docs/MAKER_TEST.md`) has a single fill.

## Why this test, and why only this one

Thirteen pre-registered tests in `docs/research_ledger.json` failed. Every
one asked whether some public signal predicts the outcome better than the
price, and the venues price within about 1¢ of the sportsbook close. Public
chatter is a weaker version of the same signal, already in the price by the
time it is posted, so "bet on what Reddit says" is not tested here at all.

The one place news can still matter is **who fills a resting order**. The
touch-maker rests $1 at the best bid or ask. If the people who fill it are
mostly reacting to news (a scratched starter, a goalie change) that the bot
has not seen, those fills lose at the close, and the fix is to **stop
quoting** that market for a while. A veto only removes trades, so it fits
the loss-response rule in `CLAUDE.md`.

## Data

- **Posts:** `sportsbot reddit-watch` (`sportsbot/signals/reddit.py`) polls
  `/new` on r/baseball, r/hockey, r/nfl, r/fantasybaseball, r/fantasyhockey
  and r/fantasyfootball every 2 minutes through Reddit's official OAuth API
  (application-only, read-only). Stored per post: id, subreddit, created
  time, **first-seen time on this server**, title, and the news terms in the
  title (`NEWS_TERMS`: injury, lineup, scratch, IL, goalie and similar).
- **Fills:** the live maker test's touch-maker fills
  (`data/maker_test.sqlite`, arm `*/touch/maker`, `mode=live`), each
  graded against the venue close exactly as `sportsbot maker-report` grades
  it (`maker_report.fill_rows`).

## Exposure, fixed now

A fill at time *t* is **flagged** if a stored post exists with a news term in
its title that names either team in the market, was created in
[*t* − 60 min, *t*], **and was first seen by the recorder at or before
*t*.** The last condition matters: the bot must have been able to know.

Team names come from the market's recorded home and away teams: the label,
its canonical full name (`data/teams.py`) and its nickname, with anything
under 4 characters dropped. A city-only match (for example "Cleveland" in a
Browns post) counts. That can only add false flags, which pulls the two
groups together and works against a PASS.

Exclusions, reported with every result:
- fills with no venue close;
- fills on markets with no recorded teams;
- fills outside the span the recorder was running, including the first hour
  of that span.

## Decision rule

The test statistic is mean venue CLV per fill for **quiet** fills minus
**flagged** fills. Its 95% interval comes from a market-clustered bootstrap:
2,000 draws, seed 13, with markets resampled within each group.

- **PASS:** each group has at least 20 graded fills, and the interval's
  lower bound is above 0. In plain terms, flagged fills are reliably worse.
- **FAIL:** each group has at least 20 graded fills, and the lower bound is
  0 or below.
- **PENDING:** either group has fewer than 20 graded fills.

The result is read once, at the maker test's own read-out (200 fills or 30
days). If PENDING then, it stays PENDING until the next pre-registered read
date added as an amendment below. There are no interim looks with a verdict.
`sportsbot news-veto-report -c config/maker_test.local.yaml` prints it.

## What a PASS buys, and what it does not

A PASS moves the veto into a **paper shadow** only. The runner would log
"would have skipped" for each market for 30 days of fills, and the veto
would then need its own CLV improvement on those shadow decisions. A PASS
never switches a live veto on directly. A FAIL or PENDING retires Reddit as
an input. Either way the row goes into `docs/research_ledger.json`.

## Known weaknesses, accepted now

- **Small flagged group.** With about 200 fills, the flagged group may
  never reach 20. That gives PENDING, not a PASS.
- **Titles only.** News that appears only in comments or post bodies is
  missed. This biases toward the null.
- **Source policy.** Only the official API is used, and only these six
  subreddits. Adding a subreddit after data collection starts needs an
  amendment here first.
