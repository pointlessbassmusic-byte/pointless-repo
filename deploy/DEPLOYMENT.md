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
| deploy new code | `git -C /opt/sportsbot pull && systemctl restart sportsbot` |

Daily ratings refresh via cron (as the sportsbot user):

```
17 9 * * * /opt/sportsbot/.venv/bin/sportsbot fit baseball >> /opt/sportsbot/logs/fit.log 2>&1
27 9 * * * /opt/sportsbot/.venv/bin/sportsbot fit table_tennis >> /opt/sportsbot/logs/fit.log 2>&1
37 9 * * 1 /opt/sportsbot/.venv/bin/sportsbot fit tennis >> /opt/sportsbot/logs/fit.log 2>&1
```

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
