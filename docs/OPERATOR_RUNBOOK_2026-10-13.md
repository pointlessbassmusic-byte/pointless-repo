# Operator runbook: from a blank laptop to a self-updating pilot (2026-10-13)

This is the start-to-finish path. Phases 0–3 are one-time setup (about an
hour). After that the system updates itself: Claude pushes code, CI tests it,
the server deploys it, telemetry flows back, and the next Claude session (or
a scheduled Routine) reads the results and proposes the next change. The
only things that stay manual are the ones that should: funding, keys, and
the decision to enable a real-money unit.

Server: Linode `ubuntu-pointless`, Dedicated 4 GB, Newark, `97.107.138.196`
(`ssh root@97.107.138.196`; LISH console `ssh -t PunchyBison@lish-us-east.linode.com ubuntu-pointless`
is the back door if SSH is ever locked out). `/opt/sportsbot` is the bot;
`/opt/pointless-repo` is the older engine suite.

## What you need on your computer

Nothing to write code. Claude Code on the web (this session) writes, tests
and pushes to GitHub by itself. You need exactly two things:

1. **A terminal with `ssh`.** macOS: Terminal (built in). Windows: PowerShell
   (`ssh` is built in on Windows 10/11). Linux: any terminal.
2. **Your server's root password or SSH key** for `97.107.138.196`.

Optional, nice to have: a GitHub account logged in on your browser (you have
this already), and the Claude app on your phone to read boards and approve
PRs from anywhere.

## Phase 0 — One-time: server can pull from GitHub (5 min)

The server needs to fetch the repo on its own. If the repo is private, give
the server a read-only deploy key.

```
ssh root@97.107.138.196
ssh-keygen -t ed25519 -f /root/.ssh/github_deploy -N "" -C sportsbot-server
cat /root/.ssh/github_deploy.pub
```

Copy the printed line. In GitHub: repo → **Settings → Deploy keys → Add
deploy key**, paste it, leave "Allow write access" **unchecked**, save. Then,
still on the server:

```
cat >> /root/.ssh/config <<'EOF'
Host github.com
  IdentityFile /root/.ssh/github_deploy
  IdentitiesOnly yes
EOF
git -C /opt/sportsbot remote set-url origin git@github.com:pointlessbassmusic-byte/pointless-repo.git
git -C /opt/sportsbot fetch origin && echo OK
```

If it prints `OK`, the server can pull. (If `/opt/sportsbot` does not exist
yet, run `bash <(curl -sL https://raw.githubusercontent.com/pointlessbassmusic-byte/pointless-repo/main/deploy/setup_server.sh)`
first, then come back here.)

**Linode Cloud Firewall.** The Linode has a firewall ("Fire") on its public
interface. GitHub Actions connects over SSH from changing IP addresses, so
the inbound rule for TCP 22 must allow **all** sources (`0.0.0.0/0`), not
just your home IP. Linode → Firewalls → Fire → Inbound Rules. Keep
everything else closed; `fail2ban` is already installed by `setup_server.sh`.

## Phase 1 — One-time: bring the server up to this branch (5 min)

```
ssh root@97.107.138.196
git -C /opt/sportsbot fetch origin claude/sports-betting-engine-consolidate-m5x0m9
git -C /opt/sportsbot checkout claude/sports-betting-engine-consolidate-m5x0m9
bash /opt/sportsbot/deploy/update_sportsbot.sh
```

What you will see, and what it means:
- `==> data-only recorders` — the pre-match quote recorder, the LIP recorder,
  weather snapshots, the $100 sim book and its board are now running. None
  of them can place an order.
- `==> preflight on both pilot configs` — two `doctor` tables. Expect every
  row PASS except `secrets.*` FAIL (no keys yet). That is correct for now.
- `==> v3 paper logs` — either "pushed", "NOT pushed (no git credentials)",
  or "no logs". If NOT pushed, the files are in
  `/opt/sportsbot/polymarket-bot/v3-logs/`; the nightly telemetry sync in
  Phase 2 will carry them to GitHub anyway.

Paste the whole output into a Claude session. Claude reads the doctor rows
and tells you exactly what is still missing.

## Phase 2 — One-time: hands-off deploy and telemetry (10 min)

This is what removes you from the loop for code changes.

**2a. A deploy key GitHub can use to reach the server.** On your laptop:

```
ssh-keygen -t ed25519 -f ~/sportsbot_deploy -N "" -C github-actions
cat ~/sportsbot_deploy.pub
```

On the server, append that public line to `/root/.ssh/authorized_keys`:

```
ssh root@97.107.138.196 "echo '<paste the .pub line>' >> /root/.ssh/authorized_keys"
```

**2b. Two repository secrets.** GitHub → repo → **Settings → Secrets and
variables → Actions → New repository secret**:
- `SERVER_HOST` = `97.107.138.196`
- `SERVER_SSH_KEY` = the full contents of `~/sportsbot_deploy` (the private
  file, including the BEGIN/END lines). `cat ~/sportsbot_deploy` prints it.

Then delete the private file from your laptop if you like; GitHub holds it.

**2c. Test it.** GitHub → **Actions → deploy → Run workflow**, choose the
branch `claude/sports-betting-engine-consolidate-m5x0m9` → Run. Green
means the whole chain works. Then **Actions → telemetry → Run workflow**
(same branch); green means a `telemetry` branch now exists with the
server's reports under `telemetry/latest/`.

**2d. Put the workflows on `main`.** GitHub only fires *automatic* triggers
(the after-tests deploy, the nightly telemetry schedule) for workflow files
that exist on the default branch. Manual runs work from any branch, which is
why 2c works, but hands-off operation needs this branch merged into `main`.
Open the pull request GitHub offers ("Compare & pull request" on the repo
page, or ask Claude to open it), check CI is green, merge. From then on:
every push that passes tests is on the server within about two minutes,
and telemetry refreshes nightly at 09:17 UTC, with no action from you.

What the automation can and cannot do, by design:
- It CAN pull code, reinstall, refresh units, restart the data-only
  recorders and the paper sim, and export reports.
- It CANNOT enable a real-money unit, write a key, or change `.env`. Those
  three are yours (Phase 3), every time.

## Phase 3 — Real money, Kalshi first (30 min, then a week of waiting)

Kalshi is first because its client has traded before, it has a demo
exchange for a dry run, and the US-legal path is unambiguous.

1. **Fund.** Kalshi → deposit $150 (the pilot config's `bankroll.amount`; if
   you deposit a different amount, change `accounts.real.starting_balance`
   and `bankroll.amount` in `/opt/sportsbot/config/local.yaml` to match, or
   ask Claude to).
2. **Keys.** Kalshi → Account → API keys → Create. Choose **RSA**. Save the
   private key file. Copy it to the server and lock it down:
   ```
   scp kalshi_private.pem root@97.107.138.196:/opt/sportsbot/kalshi.pem
   ssh root@97.107.138.196 "chown sportsbot:sportsbot /opt/sportsbot/kalshi.pem && chmod 600 /opt/sportsbot/kalshi.pem"
   ```
3. **.env.** On the server, `nano /opt/sportsbot/.env` and set:
   ```
   KALSHI_API_KEY_ID=<the key id shown on the Kalshi page>
   KALSHI_PRIVATE_KEY_PATH=/opt/sportsbot/kalshi.pem
   KALSHI_ENV=demo
   ```
   Keys go in this file only. Never paste them into chat, config files or
   issues.
4. **Preflight.** `sudo -u sportsbot env SPORTSBOT_LIVE=1 /opt/sportsbot/.venv/bin/sportsbot doctor -c /opt/sportsbot/config/pilot.yaml`
   must end with `overall: PASS`. If not, paste the table into Claude.
5. **Fee check.** `sudo -u sportsbot /opt/sportsbot/.venv/bin/sportsbot verify-fees --note "checked kalshi fee schedule <date>"`
6. **Demo week.** `systemctl enable --now sportsbot-pilot sportsbot-pilot-board`.
   For one week the pilot trades Kalshi's demo exchange (fake prices, real
   plumbing). Watch `journalctl -u sportsbot-pilot -f` once; after that the
   nightly telemetry shows you orders, fills, markouts and allocation changes
   in `telemetry/latest/board_pilot.html`. Demo results are NOT evidence of
   edge; they prove orders, cancels, fills and settlement work end to end.
7. **Prod.** Change `KALSHI_ENV=prod` in `.env`, run doctor again,
   `systemctl restart sportsbot-pilot`. Real money is now at risk, capped by
   the config: $6 per market, $60 open, $10/day loss, $40 drawdown kill
   switch. The bot cannot raise any of these on its own.

### Polymarket US (after Kalshi is running)

1. Deposit $150 at polymarket.us. Generate an API key at
   polymarket.us/developer (the secret is shown once).
2. On the server, add to `.env`: `POLYMARKET_US_KEY_ID=...` and
   `POLYMARKET_US_SECRET_KEY=...`.
3. Run the five one-contract checks in
   `docs/POLYMARKET_US_CLIENT_2026-10-09.md` (each is one small order; ask
   Claude for the exact commands in a fresh session and paste back the
   outputs). This is the only part of the Polymarket US client that was
   built from documentation rather than verified live.
4. `doctor -c config/pilot_pmus.yaml` → PASS, `verify-fees`, then
   `systemctl enable --now sportsbot-pilot-pmus`.

## Phase 4 — Steady state: what runs without you

| Loop | Cadence | Who | What it does |
|---|---|---|---|
| Trading | every 5 min | the bot | scans, prices, sizes, places post-only orders, manages exits |
| Allocation | every cycle | the bot | moves budget toward sleeves with measured positive CLV, away from negative; logs every change |
| Risk | every cycle | the bot | daily loss stop, drawdown kill switch, stale-quote and in-play guards; all fail closed |
| Fill markouts | +5s/+60s/+5m/+30m per fill | the bot | measures adverse selection on every real fill |
| Forward test | every 5 min | recorder | T−30 quotes on both venues; verdict at n = 150 |
| Deploy | on every green push | GitHub Actions | pulls to the server, restarts recorders |
| Telemetry | nightly | GitHub Actions | boards, status, markouts, forward test → `telemetry` branch |
| Review + code changes | weekly (see below) | Claude | reads telemetry, proposes changes as a PR |

### Making Claude's review automatic

In a fresh Claude Code session on this branch, say:

> Create a weekly Routine that: checks out the `telemetry` branch, reads
> `telemetry/latest/`, compares fill markouts, CLV and the allocation log
> against the promote/demote bars in `docs/LIVE_PILOT_PLAN_2026-10-08.md`,
> updates `docs/research_ledger.json` if a pre-registered test reached its
> n, and opens a pull request with any config or code change it recommends,
> with the evidence in the PR body.

Claude can create that Routine itself (it runs in the cloud on a schedule).
You then get one email a week with a PR. **Merging it is the one click that
stays yours**, deliberately: a code change to a bot holding real money
should have a human look at the diff. Merge → CI → auto-deploy, no further
action.

If you want even that click removed: ask Claude to enable GitHub
auto-merge for PRs that only touch `config/*.yaml` and `docs/`, with CI
required. Code changes under `sportsbot/` should keep the click.

### What "learning" means here, concretely

- **Within the bot (automatic, continuous):** sleeve budgets follow measured
  closing-line value; stakes shrink in drawdown; a sleeve with negative CLV
  gets a higher edge bar and half the stake cap. Losses only ever reduce
  risk. This is the loop that runs with no human and no Claude.
- **Across the system (weekly, via PR):** new pre-registered tests, config
  retuning, venue decisions, model changes. Each one lands with its evidence
  and a test, through CI, with a human merge.
- **Hard rules that never move, whatever the results say:** no
  martingale, no loss-chasing, nothing funded that failed its pre-registered
  test, live mode only with both switches.

## When something goes wrong

| Symptom | Where to look | What to do |
|---|---|---|
| Kill switch tripped | `telemetry/latest/status_pilot.txt`, board banner | Read the drawdown; if you want to continue, `sportsbot reset-kill-switch -c config/pilot.yaml` on the server. Ask Claude to review first. |
| Deploy workflow red | GitHub → Actions → deploy | Usually the SSH secret, the Linode firewall, or the server's GitHub deploy key. Paste the log into Claude. |
| Doctor FAIL | its table | Paste the table into Claude. |
| Recorder stopped | `telemetry/latest/services.txt` | `systemctl restart sportsbot-prematch` on the server; Claude reads `log_sportsbot-prematch.txt`. |
| Want to pause everything | server | `systemctl stop sportsbot-pilot sportsbot-pilot-pmus`. Recorders can keep running. |
| Want to change a sleeve by hand | `/opt/sportsbot/config/local.yaml` | `allocation: {manual: {tennis: 0.6, baseball: 0.4}, paused: [table_tennis]}`; restart the pilot unit. |
| Locked out of SSH | Linode LISH console | `ssh -t PunchyBison@lish-us-east.linode.com ubuntu-pointless`, then fix `ufw`/the firewall. |

## Honesty note

Thirteen pre-registered strategy tests have failed. The pilot is a
measurement of real fills and adverse selection at a hard-capped cost ($40
drawdown per venue), not a deployment of a known edge. If after eight weeks
no sleeve clears the promote bar, the plan says stop, and that is a result.
