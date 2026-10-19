# Deployment (Linode Ubuntu, 2 CPU / 4GB / 80GB)

## ⚠️ Read this first: where the bot runs matters

- **Polymarket (main CLOB)**: market *reads* are open globally, but **order
  placement is geoblocked from US IPs** (and UK/FR/DE/SG/AU and ~30 other
  jurisdictions). A US-region Linode (e.g. us-east/Newark) can run the full
  pipeline in **paper mode** — real market data, simulated fills — but live
  orders on the main CLOB will not go through from a US IP, and this project
  does not attempt to evade the geoblock. For US-based live trading the
  compliant paths are **Polymarket US** (separate CFTC-regulated platform
  and API, with its own state-level restrictions) or **Kalshi** (CFTC-
  regulated, US-legal; this repo's Kalshi client implements the same
  interface — switching venue is `exchange: kalshi` in config).
- **Kalshi** has a demo environment (`KALSHI_ENV=demo`) — use it for all
  integration testing before production keys.

## Steps

```bash
ssh root@YOUR_SERVER_IP
git clone https://github.com/pointlessbassmusic-byte/pointless-repo.git /opt/sportsbot
bash /opt/sportsbot/deploy/setup_server.sh
```

Then:

1. `nano /opt/sportsbot/.env` — add keys (never commit this file).
   Use a **dedicated hot wallet** for Polymarket funded only with what the
   bot may lose; keep the Kalshi RSA key at a path readable by the
   `sportsbot` user, `chmod 600`.
2. Optional overrides in `/opt/sportsbot/config/local.yaml`
   (gitignored) — bankroll, sports on/off, thresholds.
3. `systemctl start sportsbot` and watch `journalctl -u sportsbot -f`.

## Operations

Run every `sportsbot` command below from `/opt/sportsbot` (`cd /opt/sportsbot`
first): the CLI resolves `config/default.yaml` against the working
directory, and the service user cannot read `/root`.

| task | command |
|---|---|
| status / PnL / calibration | `sudo -u sportsbot /opt/sportsbot/.venv/bin/sportsbot status` |
| one-off scan (no orders) | `... sportsbot scan` |
| refresh ratings (cron this daily) | `... sportsbot fit baseball && ... fit tennis && ... fit table_tennis` |
| backtest | `... sportsbot backtest tennis` |
| reset kill switch after review | `... sportsbot reset-kill-switch` |
| go-live preflight | `... sportsbot doctor` — config/gate/DB/ratings age/keys/venues/clock; exits non-zero on FAIL |
| trading dashboard (sim/real) | `... sportsbot board` → `/opt/sportsbot/data/board.html`; runs every minute as `sportsbot-board.service` |
| $100 sim book | runs as `sportsbot-sim.service` (paper mode, live market data); logs via `journalctl -u sportsbot-sim` |
| record fee verification | `... sportsbot verify-fees --note "bought 1 share, fee $0.02"` |
| Polymarket US trade tape | `sportsbot-ustape.service` (installed by `update.sh`; exits cleanly until `POLYMARKET_US_KEY_ID` / `POLYMARKET_US_SECRET_KEY` are in `.env`, then `systemctl restart sportsbot-ustape`). Records the authenticated markets websocket's trades into `data/us_tape.sqlite`, reads only. The paper venue and the live maker test's shadow read it. Check it with `sportsbot us-tape --status`; `sportsbot doctor` reports it for `exchange: polymarket_us` |
| fresh-wallet fade forward test | `sportsbot-freshwatch.service` (installed and started by the daily `update.sh` the first time it sees the unit; public reads, a paper record in `data/fresh_watch.sqlite`, no orders); read it with `sportsbot fresh-watch --report --db /opt/sportsbot/data/fresh_watch.sqlite` (EDGE_VERDICT Result 21b) |
| Polymarket US keys | `POLYMARKET_US_KEY_ID` / `POLYMARKET_US_SECRET_KEY` in `.env` (polymarket.us/developer); `sportsbot doctor -c config/maker_test.yaml` with `exchange: polymarket_us` checks them and pings the gateway |
| live maker fill-share test | `config/maker_test.yaml` + the runbook in `docs/MAKER_TEST.md` (inert until `mode: live`, `SPORTSBOT_LIVE=1`, keys, and a learning budget in `config/allocation.yaml`) |
| substrate dashboard (HTML) | `... sportsbot dashboard` → `/opt/sportsbot/data/dashboard.html` |
| deploy new code (paper units) | automatic: `sportsbot-update.timer` runs `deploy/update.sh` daily at 14:17 UTC (pull `--ff-only`, venv refresh, `fit baseball`, restart sim/board/boardserve/weather); by hand: `bash /opt/sportsbot/deploy/update.sh` |
| deploy new code (LIVE unit) | never automatic: read the change, then `systemctl restart sportsbot` yourself |
| fix `insufficient permission ... .git/objects` | `chown -R sportsbot:sportsbot /opt/sportsbot` (a root-run `git pull` left root-owned objects; `update.sh` now does this itself) |
| weather arm scores (coin/climo/NWS/market) | `... sportsbot weather-score` — decision-time Brier per arm; offline |
| weather snapshots (substrate m2) | runs as `weather-snapshot.service` (read-only, every 30 min); logs via `journalctl -u weather-snapshot` |

Daily ratings refresh via cron (as the sportsbot user):

```
17 9 * * * /opt/sportsbot/.venv/bin/sportsbot fit baseball >> /opt/sportsbot/logs/fit.log 2>&1
27 9 * * * /opt/sportsbot/.venv/bin/sportsbot fit table_tennis >> /opt/sportsbot/logs/fit.log 2>&1
37 9 * * 1 /opt/sportsbot/.venv/bin/sportsbot fit tennis >> /opt/sportsbot/logs/fit.log 2>&1
47 */6 * * * /opt/sportsbot/.venv/bin/sportsbot signals-scan --from-events /opt/sportsbot/data/substrate_events.csv >> /opt/sportsbot/logs/signals.log 2>&1
```

## Substrate dashboard

`sportsbot dashboard` exports the bot's predictions/outcomes (plus Kalshi
weather snapshots and ARV sessions when their DBs exist) and builds one
self-contained HTML file — e-process wealth curves per arm, fusion weights,
score tables, trial counts. Regenerate it every 10 minutes via cron:

```
*/10 * * * * cd /opt/sportsbot && /opt/sportsbot/.venv/bin/sportsbot dashboard >> logs/dashboard.log 2>&1
```

The firewall stays SSH-only on purpose — don't open a web port for this.
View it through an SSH tunnel from your machine:

```bash
ssh -L 8000:localhost:8000 root@YOUR_SERVER_IP \
    "cd /opt/sportsbot/data && python3 -m http.server 8000 --bind 127.0.0.1"
# then open http://localhost:8000/dashboard.html
```

or just copy it down: `scp root@YOUR_SERVER_IP:/opt/sportsbot/data/dashboard.html .`

## The $100 sim book and the dashboard

`sportsbot-sim.service` runs `config/sim.yaml` — paper mode against live
**Polymarket** market data (the venue the file names; the 2026-09-22 move to
Kalshi rested on a discovery bug, corrected in `docs/EDGE_VERDICT_2026-09-23.md`),
with every dollar knob scaled to a $100 bankroll (the $1,000 defaults would
put half the account in one market). Market data on both venues is public, so
the sim needs no API keys; `ODDS_API_KEY` in `.env` additionally turns on the
sharp-line harness and prices NBA/NFL/NHL. The sim also runs the touch-maker
arm (`touch:` block, $1 a side on MLB) whose paper fills are tape-verified.
Real money is a separate question: the US-legal venues are Kalshi and
Polymarket US (`exchange: kalshi`), never the main CLOB from a US IP.
`sportsbot-board.service` rebuilds `data/board.html` every minute: equity,
where the money is allowed to go and the evidence for it, every decision
including the passes and why, and the go-live gate. `sportsbot maker-report`
reads out the touch arm (`docs/MAKER_TEST.md`).

Durability: this is the home for the sim's SQLite store. A cloud Claude
session that runs one cycle a day keeps its `data/` only until its container
is reclaimed; the server's `data/` persists, and a nightly
`sqlite3 data/sportsbot.sqlite ".backup backups/sportsbot-$(date +%F).sqlite"`
in the `sportsbot` user's crontab costs nothing.

View it the same way as the substrate dashboard — through an SSH tunnel, not
an open web port:

```bash
ssh -L 8000:localhost:8000 root@YOUR_SERVER_IP \
    "cd /opt/sportsbot/data && python3 -m http.server 8000 --bind 127.0.0.1"
# then open http://localhost:8000/board.html
```

The page's sim/real switch is a **view** switch. It shows you a different
book; it cannot start real trading, and it deliberately has no control that
could. Real orders still require `mode: live` in config AND `SPORTSBOT_LIVE=1`
in the environment, both set by hand on the host — see the gate below.

## Restarts lose no maker evidence

The runner handles SIGTERM (what `systemctl restart`/`stop` sends): it
finishes the cycle, cancels every resting order with its tape shadow and
`canceled_reason: shutdown`, then exits. On the next start it sweeps order
rows a previous process left resting (a crash, a kill -9): paper rows are
closed as `orphaned`; in live mode the venue's resting orders are fetched
and ours are cancelled there first, while any venue order the store does
not know is logged and left alone. `sportsbot maker-report` prints the
orphaned count and excludes those rows from the fill share.

## If `fit baseball` says the Stats API refused this host

Measured on the Linode (2026-10-13): `/api/v1/schedule` answers `406 Not
Acceptable` from that address on every variant tried (plain or browser
headers, IPv4, HTTP/1.1), while `/api/v1/teams` answers 200 and the same
schedule request succeeds from other networks. `sportsbot fit baseball`
then installs the committed seed (`config/seeds/baseball.json`, refreshed by
the daily check-in where the API answers) and says so in yellow. The sim's
starting-pitcher and rest context also comes from that endpoint; when it
fails the cycle logs `MLB context fetch failed` and prices without it. Both
are degraded modes, not outages: the touch arm and the team sports are
unaffected.

## One address for phone and desktop (with the arbitrage desk's Tailscale node)

The arbitrage desk on the same server already runs Tailscale Serve
(`./desk.sh remote` in its repo), which gives one private HTTPS address
that only devices signed into your tailnet can open. sportsbot's board
can ride the same node on a path, so one bookmark covers both desks:

```bash
systemctl status sportsbot-boardserve      # board.html on 127.0.0.1:8001, whitelist only
sudo tailscale serve --bg --set-path /sportsbot http://127.0.0.1:8001
tailscale serve status                     # prints the https://<node>.<tailnet>.ts.net address
# the board is then at https://<node>.<tailnet>.ts.net/sportsbot/
```

`sportsbot-boardserve.service` serves only `board.html` and
`dashboard.html` from `data/` (never the SQLite store, never a listing),
binds to localhost only, and adds no auth of its own: Tailscale is the
auth. Without the desk's node, `sudo tailscale up` once on the server
does the same. The SSH tunnel above keeps working either way.

## Alerts on the phone

Put a Discord webhook URL in `.env` as `SPORTSBOT_DISCORD_WEBHOOK` (the
arbitrage desk can post to the same one). The bot then posts each
cycle's fills (arm, side, size, price), any risk halt (once per six
hours while it holds), and `sportsbot digest --post` sends the daily
three lines per book: equity and CLV, the touch arm's posts and fills,
and the distance to the bar. Unset, nothing is sent and nothing
changes. A failed post is logged and never stops a cycle.

## Going live — the gate, not a suggestion

Stay in paper mode until **all** of these hold, then flip `mode: live` in
config AND `SPORTSBOT_LIVE=1` in `.env` (both are required on purpose):

1. ≥ 200 settled paper bets or 4+ weeks of paper trading;
2. positive mean CLV (`sportsbot status` shows it) — CLV, not PnL, is the
   early signal that edges are real;
3. rolling Brier score under 0.25 and the calibration bins roughly on the
   diagonal;
4. you have verified fees on the venue with one tiny manual trade.

Start live with ~10% of intended bankroll. The kill switches (drawdown,
daily loss, calibration decay) will stop the bot; investigate before
resetting them.

The gate is now applied **per strategy arm** (`sport/signal/style`) by
`bot/portfolio.py`, and real money reaches an arm three ways only: a
manual weight in `config/allocation.yaml`, a green per-arm gate (200
CLV-graded fills, CLV interval above zero, Brier < 0.25, fees verified),
or a bounded learning budget for arms marked `learn: true`. See
`docs/ALLOCATION.md` for the $100–250 starting path.

## Server hardening included in setup

- UFW: inbound SSH only; fail2ban; unattended security upgrades.
- chrony time sync (API signatures and order timestamps need a true clock).
- Dedicated non-login `sportsbot` user; systemd sandboxing
  (ProtectSystem=strict, MemoryMax=1500M).
- Recommended next: disable SSH password auth (`PasswordAuthentication no`)
  and use keys only; consider moving SSH off port 22.
