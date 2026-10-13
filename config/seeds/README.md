# Seed ratings

`baseball.json` is a committed copy of the MLB ratings `sportsbot fit baseball`
produces (Elo per team, starting-pitcher adjustments, last season), with a
`seed_meta` block saying when and on how many games it was fitted.

It exists because the MLB Stats API refuses `/api/v1/schedule` from some
hosts (406 Not Acceptable from the Linode address on 2026-10-13, while
`/api/v1/teams` answers 200 and the identical request succeeds from other
networks). On such a host `sportsbot fit baseball` catches the HTTP error
and installs this file into the ratings dir with a warning, so the sim keeps
pricing MLB instead of dropping every market as "model unfit".

Refresh it where the API answers: `sportsbot fit baseball --seed-out
config/seeds/baseball.json`, then commit. The daily check-in does this. In
the off-season (November to March) the file does not change.
