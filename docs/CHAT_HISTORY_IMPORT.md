# Importing old chat history

Claude sessions are isolated — past sports-betting and prediction-engine chats can't be pulled
automatically into this repo. To preserve them:

## How to import

1. Open the old chat (Claude app/web → conversation history).
2. Copy the conversation (or use the export feature if available).
3. Save it as one markdown file per chat in `docs/chat-imports/`, named like:

   ```
   docs/chat-imports/2026-08-14-polymarket-edge-thresholds.md
   docs/chat-imports/2026-08-20-kalshi-substrate-design.md
   ```

4. Add a short header at the top of each file:

   ```markdown
   # <topic>
   - Date: YYYY-MM-DD
   - Engine: polymarket-bot | kalshi-engine | both
   - Key decisions: <one-line summary>
   ```

5. Commit and push. Chats are then versioned in GitHub and land on the server with every
   `./deploy/deploy.sh` (repo is cloned to `/opt/pointless-repo`, so imports live at
   `/opt/pointless-repo/docs/chat-imports/`).

## Why bother

Those chats contain parameters and decisions already worked out (edge thresholds, market filters,
sizing rules, API quirks). Once imported, paste the relevant file into a new Claude Code session and
ask it to fold the decisions into the configs/models here.
