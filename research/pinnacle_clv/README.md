# Kalshi vs Pinnacle close (tennis): how to run

Rules: `docs/PINNACLE_TENNIS_PREREG_2026-10-08.md`.

1. **Pinnacle odds.** tennis-data.co.uk is behind Cloudflare and blocks datacenter
   IPs, so download from a normal browser:
   - http://www.tennis-data.co.uk/2026/2026.xlsx (ATP)
   - http://www.tennis-data.co.uk/2026w/2026.xlsx (WTA)

   Commit them as `research/pinnacle_clv/data/atp2026.xlsx` and `wta2026.xlsx`.
   A `.csv` saved from the same sheet also works.
2. **Kalshi trade tapes** (about 700 MB, not committed). Rebuild with
   `research/settle_vpin/collect.py`, which writes `tr/`, and `collect2.py`,
   which writes `tr2/`. Run both from one working directory.
3. `pip install openpyxl` (xlsx only), then:
   `python research/pinnacle_clv/run.py data/atp2026.xlsx data/wta2026.xlsx tr tr2`
   (paths relative to where you run it). It prints H1 (Brier: Kalshi close vs
   Pinnacle-Shin), H2 (the tradable rule, by half) and the pre-registered verdict.
