# Weather nowcast vs Kalshi daily-high brackets — pre-registration (2026-10-02)

Committed before any price or observation data for this test was pulled.

## Hypothesis
Kalshi daily-high bracket prices during the settlement day under-use the running
maximum already observed at the settlement station. A model that conditions on
it beats the quotes by more than spread + fee.

## Data
- US daily-high series whose rules name a station code `(CLIxxx)` or a mappable
  NWS station. ASOS station = the code without `CLI`.
- All settled events closing 2026-06-01 … 2026-09-30, from `/markets` and
  `/historical/markets`.
- Truth = the high implied by Kalshi's settled brackets (the YES bracket's range;
  for a tail bracket, its bound). This uses Kalshi's own settlement value, which
  holds through the CLI → Weather Company source change.
- Observations: IEM ASOS `tmpf` (routine and special METARs). Running max
  M(t) = max over observations from local-standard-time midnight to t − 10 min,
  allowing for publication latency.
- Quotes: 60-min candles. The quote at checkpoint t is the candle ending exactly at
  t; bid and ask must both be present and 0 < bid < ask < 1.

## Model (frozen on the fit half)
D = H − round(M(t)), by station × checkpoint, as an empirical integer
distribution with add-one smoothing over the support observed in the fit half,
widened ±3. P(bracket) = Σ_d P(D = d) · 1[round(M(t)) + d ∈ bracket].
No forecast input (the forecast-blended version is a separate, later test).

## Checkpoints
Local standard time 11:00, 13:00, 15:00, 17:00 (converted to UTC per station).

## Trades (taker only)
- Buy YES if p − ask − fee ≥ m.
- Buy NO if (1 − p) − (1 − bid) − fee ≥ m.
- fee = 0.07 · mult · price · (1 − price), from the series fee multiplier.
- Primary margin m = 0.05. Secondary m ∈ {0.03, 0.10}, reported but not used for
  the pass/fail decision.
- Unit: one observation per event × checkpoint = the sum of P&L over all brackets
  traded at that checkpoint, per contract.

## Split and bar
- Fit: events 2026-06-01 … 2026-07-31. Test: 2026-08-01 … 2026-09-30 (frozen model).
- Pass (test half, m = 0.05, all checkpoints pooled): ≥ 100 event-checkpoints,
  mean > 0, t ≥ 2 clustered by date, ROI ≥ 2% of cost.
- Report per checkpoint and per station, but the decision is the pooled number
  only.
- Settlement-source caveat: test-half events after the source switch are flagged
  and reported separately.
