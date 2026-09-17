# Server: Linode Ubuntu (runtime host)

| | |
|---|---|
| Public IPv4 | `97.107.138.196` |
| Public IPv6 | `2600:3c03::2000:84ff:fec1:6fd3` |
| SSH | `ssh root@97.107.138.196` |
| LISH console | `ssh -t PunchyBison@lish-us-east.linode.com ubuntu-us-east` |
| Specs | 2 CPU cores · 4 GB RAM · 80 GB storage · encrypted |
| Maintenance policy | Migrate |

## Role

The server is a **runtime host only**. No editing code on the box. It runs the two engines from
`/opt/pointless-repo` via systemd, deployed from GitHub.

## One-time setup

From the laptop, in the repo root:

```bash
./deploy/setup_server.sh
```

This SSHes in and: installs python3-venv + git, clones the repo to `/opt/pointless-repo`, creates
venvs for both engines, installs requirements, installs the systemd units, and creates empty `.env`
files for you to fill in (`/opt/pointless-repo/polymarket-bot/.env` and
`/opt/pointless-repo/kalshi-engine/.env`).

Then SSH in once to fill in the two `.env` files with real keys.

## Every deploy after that

```bash
./deploy/deploy.sh
```

Pulls latest `main` on the server, reinstalls requirements if they changed, restarts both services.

## Operating the services

```bash
systemctl status polymarket-bot kalshi-engine
journalctl -u polymarket-bot -f        # live logs
journalctl -u kalshi-engine --since today
systemctl restart polymarket-bot
```

Databases (scan/signal/order logs) live at:

- `/opt/pointless-repo/polymarket-bot/data/bot.db`
- `/opt/pointless-repo/kalshi-engine/data/engine.db`

Back them up by copying the files (they're SQLite).

## Security notes

- Consider adding a non-root deploy user and disabling root password SSH (key-only).
- `ufw allow OpenSSH && ufw enable` — nothing else needs inbound ports.
- Keys live only in the server's `.env` files, never in git.
