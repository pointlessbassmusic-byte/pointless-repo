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
| substrate dashboard (HTML) | `... sportsbot dashboard` → `/opt/sportsbot/data/dashboard.html` |
| deploy new code | `git -C /opt/sportsbot pull && systemctl restart sportsbot weather-snapshot` |
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
market data, with every dollar knob scaled to a $100 bankroll (the $1,000
defaults would put half the account in one market). `sportsbot-board.service`
rebuilds `data/board.html` every minute: equity, where the money is allowed to
go and the evidence for it, every decision including the passes and why, and
the go-live gate.

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

## Server hardening included in setup

- UFW: inbound SSH only; fail2ban; unattended security upgrades.
- chrony time sync (API signatures and order timestamps need a true clock).
- Dedicated non-login `sportsbot` user; systemd sandboxing
  (ProtectSystem=strict, MemoryMax=1500M).
- Recommended next: disable SSH password auth (`PasswordAuthentication no`)
  and use keys only; consider moving SSH off port 22.
