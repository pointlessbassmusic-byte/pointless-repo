FINAL DASHBOARD-SAFE PAPER TRADER

Corrections from the pasted reversal build:
- Keeps /root/sim_metrics.csv and its ORIGINAL 9-column dashboard schema.
- Writes metric history separately to /root/paper_metrics_history.csv.
- Trades only pre-match Tennis H2H.
- Baseball/Table Tennis remain visible on the dashboard but are disabled for entries.
- No 0.50 or midpoint anchor fallback.
- Fuzzy SharpOracle matching requires >=0.92 plus an ambiguity margin.
- No synthetic short positions.
- No automatic stop-and-reverse.
- Entry uses executable ask; exit uses executable bid.
- $15 notional is converted to shares before P&L.
- Models per-market sports taker fees and cost-adjusted edge.
- Rejects wide spreads and weak raw/net edge.
- REST is only an exit safety fallback.
- Uses current WebSocket book, price_change and best_bid_ask events.
- Uses dynamic subscribe/unsubscribe and application PING every 10 seconds.
- Rolling negative-performance gate stops new entries.
- Contains no live-order code.

DEPLOYMENT
1. Rotate the SharpOracle key that appeared in source/chat.
2. Upload this ZIP anywhere on the server and extract it.
3. Run deploy_final_dashboard_safe.sh.
4. Enter the NEW key when prompted.
5. Verify the URL dashboard; generic web/listener processes are never killed.


MULTIPLIER EXIT UPDATE
- Minimum take-profit target: 1.09x initial cash outlay.
- Maximum take-profit target: 20.00x initial cash outlay.
- Target is selected from SharpOracle-supported upside, clamped to 1.09x-20x.
- If SharpOracle does not support at least 1.09x after modeled fees, no entry.
- Stop-loss: exit when net executable liquidation value reaches 0.88x initial cash outlay.
- Initial cash outlay = $15 trade notional + modeled entry fee.
- Because Polymarket shares cannot exceed $1.00, 20x requires a sufficiently low entry price.
- MIN_ENTRY_PRICE is therefore 0.01 in this research build.
- Fast price gaps can realize below the 0.88x trigger despite the stop rule.
