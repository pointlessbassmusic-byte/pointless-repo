"""sportsbot CLI.

  sportsbot fit tennis            # download data, train ratings, save
  sportsbot fit baseball
  sportsbot fit table_tennis
  sportsbot backtest tennis       # walk-forward evaluation
  sportsbot scan                  # one discovery+prediction pass, no orders
  sportsbot run                   # the live/paper loop (what systemd runs)
  sportsbot status                # exposure, PnL, calibration, kill switch
  sportsbot reset-kill-switch
"""

from __future__ import annotations

import logging
import os
import sys

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Sports prediction engine + exchange trading bot")
console = Console()

CONFIG_OPT = typer.Option("config/default.yaml", "--config", "-c")


def _setup(config_path: str) -> dict:
    load_dotenv()
    from sportsbot.bot.runner import load_config

    cfg = load_config(config_path)
    log_cfg = cfg.get("logging", {})
    log_file = log_cfg.get("file")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    return cfg


@app.command()
def fit(sport: str, config: str = CONFIG_OPT,
        start_year: int = typer.Option(2018, help="tennis: first year of history"),
        seasons: int = typer.Option(3, help="baseball: seasons of history"),
        tt_csv: str = typer.Option("", help="table_tennis: results CSV path"),
        tt_days: int = typer.Option(90, help="table_tennis: days of Polymarket history")):
    """Train ratings for one sport and save them to the ratings dir."""
    cfg = _setup(config)
    ratings_dir = cfg.get("storage", {}).get("ratings_dir", "data/ratings")
    os.makedirs(ratings_dir, exist_ok=True)

    if sport == "tennis":
        from sportsbot.data.tennis_data import fetch_history
        from sportsbot.engine.tennis import TennisModel

        console.print(f"downloading ATP+WTA history {start_year}-present…")
        matches = fetch_history(start_year=start_year)
        console.print(f"training on {len(matches)} matches")
        model = TennisModel(
            surface_weight=float(cfg["sports"]["tennis"].get("surface_weight", 0.5)))
        model.fit(matches)
        path = os.path.join(ratings_dir, "tennis.json")
        model.save(path)
        console.print(f"[green]saved {len(model.overall.ratings)} player ratings -> {path}")
    elif sport == "baseball":
        from sportsbot.data.mlb_data import MLBStatsClient
        from sportsbot.engine.baseball import BaseballModel

        console.print(f"downloading {seasons} MLB seasons from statsapi.mlb.com…")
        games = MLBStatsClient().history(seasons=seasons)
        console.print(f"training on {len(games)} games")
        model = BaseballModel(
            home_advantage=float(cfg["sports"]["baseball"].get("home_advantage_elo", 24.0)))
        model.fit(games)
        path = os.path.join(ratings_dir, "baseball.json")
        model.save(path)
        console.print(f"[green]saved {len(model.elo.ratings)} team ratings -> {path}")
    elif sport == "table_tennis":
        from sportsbot.data.tabletennis_data import load_csv, results_from_polymarket
        from sportsbot.engine.tabletennis import TableTennisModel

        if tt_csv:
            results = load_csv(tt_csv)
            console.print(f"loaded {len(results)} results from {tt_csv}")
        else:
            console.print(f"bootstrapping from resolved Polymarket markets ({tt_days}d)…")
            results = results_from_polymarket(days_back=tt_days)
            console.print(f"collected {len(results)} resolved matches")
        model = TableTennisModel()
        model.fit(results)
        path = os.path.join(ratings_dir, "tabletennis.json")
        model.save(path)
        console.print(f"[green]saved {len(model.elo.ratings)} player ratings -> {path}")
    else:
        raise typer.BadParameter("sport must be tennis | baseball | table_tennis")


@app.command()
def backtest(sport: str, config: str = CONFIG_OPT,
             start_year: int = typer.Option(2015),
             seasons: int = typer.Option(4),
             tt_csv: str = typer.Option("")):
    """Walk-forward evaluation with realistic benchmarks."""
    _setup(config)
    if sport == "tennis":
        from sportsbot.backtest import walk_forward_tennis
        from sportsbot.data.tennis_data import fetch_history

        matches = fetch_history(start_year=start_year)
        rpt = walk_forward_tennis(matches)
        console.print(f"[bold]tennis[/bold] {rpt.summary()}")
        console.print("benchmarks: Elo ~0.60-0.63 log loss / 64-66% acc; "
                      "Pinnacle close ~0.58-0.60")
    elif sport == "baseball":
        from sportsbot.backtest import walk_forward_mlb
        from sportsbot.data.mlb_data import MLBStatsClient

        games = MLBStatsClient().history(seasons=seasons)
        rpt = walk_forward_mlb(games)
        console.print(f"[bold]mlb[/bold] {rpt.summary()}")
        console.print("benchmarks: always-home 0.689; good models 0.66-0.68; "
                      "closing lines 0.65-0.67")
    elif sport == "table_tennis":
        from sportsbot.backtest import walk_forward_tt
        from sportsbot.data.tabletennis_data import load_csv, results_from_polymarket

        results = load_csv(tt_csv) if tt_csv else results_from_polymarket(days_back=120)
        rpt = walk_forward_tt(results, warmup=min(500, len(results) // 3))
        console.print(f"[bold]table tennis[/bold] {rpt.summary()}")
    else:
        raise typer.BadParameter("sport must be tennis | baseball | table_tennis")
    for b in rpt.bins:
        console.print(f"  bin {b['lo']:.1f}-{b['hi']:.1f}: n={b['n']:>5} "
                      f"pred={b['mean_pred']:.3f} obs={b['observed']:.3f}")


@app.command()
def scan(config: str = CONFIG_OPT):
    """One discovery+prediction pass; prints opportunities, places no orders."""
    cfg = _setup(config)
    from sportsbot.bot.runner import Runner
    from sportsbot.bot.strategy import evaluate_market

    runner = Runner(cfg)
    sports = cfg.get("scan", {}).get("sports", [])
    table = Table(title="scan results")
    for col in ("market", "sport", "model p", "mid", "edge", "side", "stake"):
        table.add_column(col)
    markets = []
    for s in sports:
        try:
            markets.extend(runner.data_client.list_sports_markets(s))
        except Exception as exc:
            console.print(f"[red]{s} discovery failed: {exc}")
    scanned = runner.scanner.scan(markets)
    console.print(f"{len(markets)} markets, {len(scanned)} matched to models")
    exposure = runner.store.exposure_by()
    for sm in scanned:
        try:
            quote = runner.exchange.get_quote(sm.market)
        except Exception:
            continue
        intent = evaluate_market(sm.market, quote, sm.prediction, runner.staking,
                                 runner.strategy, runner.fee_fn, exposure)
        mid = quote.mid
        table.add_row(
            (sm.market.slug or sm.market.market_id)[:48],
            sm.market.sport.value if sm.market.sport else "?",
            f"{sm.prediction.prob_yes:.3f}",
            f"{mid:.3f}" if mid is not None else "-",
            f"{intent.edge:.3f}" if intent else "-",
            intent.side.value if intent else "-",
            f"${intent.price * intent.size:.2f}" if intent else "-",
        )
    console.print(table)


@app.command()
def run(config: str = CONFIG_OPT):
    """The main loop (systemd entry point)."""
    cfg = _setup(config)
    from sportsbot.bot.runner import Runner

    Runner(cfg).run_forever()


def _category_report(cfg: dict, store) -> dict:
    """Per-category performance + adaptive-layer state from config + store."""
    from sportsbot.bot.positions import category_report

    bank = cfg.get("bankroll", {})
    sports_cfg = cfg.get("sports", {})
    return category_report(
        store.settled_bets(),
        cfg.get("adaptive", {}),
        float(bank.get("min_edge", 0.03)),
        float(bank.get("max_stake_per_market", 50.0)),
        {k: float(v["min_edge_override"]) for k, v in sports_cfg.items()
         if isinstance(v, dict) and "min_edge_override" in v},
        {k: float(v["max_stake_override"]) for k, v in sports_cfg.items()
         if isinstance(v, dict) and "max_stake_override" in v},
        float(bank.get("kelly_multiplier", 0.25)),
        float(cfg.get("risk", {}).get("max_drawdown", 250.0)),
    )


@app.command()
def status(config: str = CONFIG_OPT):
    """Exposure, PnL, calibration, per-category performance, kill switch."""
    cfg = _setup(config)
    from sportsbot.core.calibration import BetRecord, PerformanceTracker
    from sportsbot.data.store import Store

    store = Store(cfg.get("storage", {}).get("sqlite_path", "data/sportsbot.sqlite"))
    exp = store.exposure_by()
    console.print(f"open exposure: ${exp['total']:.2f} across "
                  f"{exp['open_positions']} positions {exp['by_sport']}")
    tracker = PerformanceTracker()
    for r in store.settled_bets():
        tracker.add(BetRecord(
            market_id=r["market_id"], side=r["side"], model_prob=r["model_prob"],
            entry_price=r["entry_price"], stake=r["stake"],
            closing_price=r.get("closing_price"), outcome=r.get("outcome"),
            pnl=r.get("pnl"),
        ))
    console.print(tracker.summary())
    console.print(f"max drawdown: ${tracker.drawdown():.2f}")

    rep = _category_report(cfg, store)
    if rep["by_sport"]:
        table = Table(title="per category (adaptive layer state)")
        for col in ("sport", "bets", "PnL", "mean CLV", "hit", "state",
                    "min edge", "stake cap"):
            table.add_column(col)
        for sport, d in rep["by_sport"].items():
            table.add_row(
                sport, str(d["n"]), f"${d['pnl']:.2f}",
                "—" if d["mean_clv"] is None else f"{d['mean_clv']:+.4f}",
                "—" if d["hit_rate"] is None else f"{d['hit_rate']:.1%}",
                "[yellow]tightened[/yellow]" if d["tightened"] else "normal",
                f"{d['min_edge']:.3f}", f"${d['max_stake']:.0f}",
            )
        console.print(table)
    console.print(f"effective Kelly multiplier: {rep['effective_kelly']:.4f} "
                  f"(drawdown ${rep['current_drawdown']:.2f} below peak)")
    ks = store.get_kv("kill_switch_tripped", False)
    console.print(f"kill switch: {ks or 'clear'}")


@app.command("substrate-export")
def substrate_export(config: str = CONFIG_OPT,
                     out: str = typer.Option("data/substrate_events.csv"),
                     resolve: bool = typer.Option(True, help="fetch outcomes for unbet markets")):
    """Export bot predictions/outcomes to the substrate ingest.py schema
    (substrate/ milestone 1)."""
    cfg = _setup(config)
    from sportsbot.bot.runner import build_exchange
    from sportsbot.data.store import Store
    from sportsbot.substrate_bridge import export_events_csv

    store = Store(cfg.get("storage", {}).get("sqlite_path", "data/sportsbot.sqlite"))
    data_client = build_exchange(cfg)[1] if resolve else None
    summary = export_events_csv(store, out, data_client=data_client)
    console.print(summary)


@app.command("weather-snapshot")
def weather_snapshot(config: str = CONFIG_OPT,
                     loop: int = typer.Option(0, help="seconds between passes; 0 = once"),
                     export: str = typer.Option("", help="also export ingest CSV to this path")):
    """Kalshi weather-dailies decision-time snapshots (substrate milestone 2).
    Read-only public data; shadow mode by protocol."""
    _setup(config)
    from sportsbot.substrate_bridge import WeatherSnapshotService

    svc = WeatherSnapshotService()
    if loop > 0:
        svc.run_loop(interval_seconds=loop)
    else:
        console.print(svc.snapshot_once())
    if export:
        console.print(svc.export_ingest_csv(export))


def _write_ops_json(cfg: dict, store, path: str) -> None:
    """Bot-operations summary (mirrors `sportsbot status`) for the dashboard's
    ops panel."""
    import json
    import time as _time

    from sportsbot.core.calibration import BetRecord, PerformanceTracker

    tracker = PerformanceTracker()
    cum, cum_pnl = 0.0, []
    for r in store.settled_bets():
        tracker.add(BetRecord(
            market_id=r["market_id"], side=r["side"], model_prob=r["model_prob"],
            entry_price=r["entry_price"], stake=r["stake"],
            closing_price=r.get("closing_price"), outcome=r.get("outcome"),
            pnl=r.get("pnl"),
        ))
        cum += r.get("pnl") or 0.0
        cum_pnl.append(round(cum, 2))
    ops = {
        "generated": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "mode": f"{cfg.get('mode', 'paper')}/{cfg.get('exchange', 'polymarket')}",
        "bankroll": cfg.get("bankroll", {}).get("amount"),
        "exposure": store.exposure_by(),
        "summary": tracker.summary(),
        "max_drawdown": tracker.drawdown(),
        "kill_switch": bool(store.get_kv("kill_switch_tripped", False)),
        "cum_pnl": cum_pnl,
        "categories": _category_report(cfg, store),
    }
    with open(path, "w") as fh:
        json.dump(ops, fh, indent=1)


@app.command("dashboard")
def dashboard(config: str = CONFIG_OPT,
              out: str = typer.Option("data/dashboard.html"),
              arv_db: str = typer.Option("arv_sessions.sqlite",
                                         help="arv_cli session DB (skipped if missing)"),
              resolve: bool = typer.Option(False,
                                           help="fetch outcomes for unbet markets (network)"),
              weather_db: str = typer.Option("data/weather_snapshots.sqlite",
                                             help="weather snapshot DB (skipped if missing)")):
    """One-shot substrate dashboard (milestone 4): export bot events (plus the
    Kalshi weather ingest when snapshots exist) and build the self-contained
    HTML via substrate/dashboard.py."""
    import subprocess
    from pathlib import Path

    cfg = _setup(config)
    from sportsbot.bot.runner import build_exchange
    from sportsbot.data.store import Store
    from sportsbot.substrate_bridge import export_events_csv

    dash = Path(__file__).resolve().parents[1] / "substrate" / "dashboard.py"
    if not dash.exists():
        console.print("[red]substrate/dashboard.py not found — run from a full "
                      "repo checkout (pip install -e .)")
        raise typer.Exit(1)

    out_dir = os.path.dirname(out) or "."
    os.makedirs(out_dir, exist_ok=True)
    store = Store(cfg.get("storage", {}).get("sqlite_path", "data/sportsbot.sqlite"))
    data_client = build_exchange(cfg)[1] if resolve else None
    events_csv = os.path.join(out_dir, "substrate_events.csv")
    console.print(export_events_csv(store, events_csv, data_client=data_client))
    csvs = [events_csv]
    ops_json = os.path.join(out_dir, "ops.json")
    _write_ops_json(cfg, store, ops_json)

    if os.path.exists(weather_db):
        from sportsbot.substrate_bridge import WeatherSnapshotService

        weather_csv = os.path.join(out_dir, "weather_ingest.csv")
        console.print(WeatherSnapshotService(db_path=weather_db)
                      .export_ingest_csv(weather_csv))
        csvs.append(weather_csv)

    cmd = [sys.executable, str(dash), "--out", os.path.abspath(out),
           "--arv-db", os.path.abspath(arv_db),
           "--ops", os.path.abspath(ops_json)]
    for path in csvs:
        cmd += ["--events", os.path.abspath(path)]
    proc = subprocess.run(cmd, cwd=dash.parent, capture_output=True, text=True)
    console.print((proc.stdout + proc.stderr).strip())
    if proc.returncode != 0:
        raise typer.Exit(proc.returncode)


@app.command("signals-scan")
def signals_scan(
    config: str = CONFIG_OPT,
    entities: str = typer.Option("", help="comma-separated team/player names to search"),
    from_events: str = typer.Option("", help="derive entities from an ingest CSV's event ids"),
    window_hours: float = typer.Option(24.0, help="lookback window for chatter"),
    db: str = typer.Option("data/chatter.sqlite"),
):
    """Collect public social chatter (Bluesky) for entities; store timestamped
    counts. Data only — nothing is wired into trading."""
    _setup(config)
    from sportsbot.signals.chatter import BlueskyChatter, entities_from_events_csv

    names = [e for e in entities.split(",") if e.strip()]
    if from_events:
        names += entities_from_events_csv(from_events)
    if not names:
        raise typer.BadParameter("pass --entities and/or --from-events")
    rows = BlueskyChatter(db).scan(names, window_hours=window_hours)
    table = Table("entity", "posts", "flagged", "top terms")
    for r in rows:
        top = ", ".join(f"{k}×{v}" for k, v in sorted(
            r["flagged_terms"].items(), key=lambda kv: -kv[1])[:3])
        table.add_row(r["entity"], str(r["posts"]), str(r["flagged"]), top)
    console.print(table)


@app.command("signals-report")
def signals_report(
    config: str = CONFIG_OPT,
    events: str = typer.Option(..., help="ingest-schema CSV with resolved events"),
    db: str = typer.Option("data/chatter.sqlite"),
):
    """Correlate pre-decision chatter with market residuals (evidence harness)."""
    _setup(config)
    from sportsbot.signals.chatter import correlation_report

    console.print(correlation_report(db, events))


@app.command("reset-kill-switch")
def reset_kill_switch(config: str = CONFIG_OPT):
    cfg = _setup(config)
    from sportsbot.data.store import Store

    Store(cfg.get("storage", {}).get("sqlite_path", "data/sportsbot.sqlite")).set_kv(
        "kill_switch_tripped", False)
    console.print("[green]kill switch reset")


if __name__ == "__main__":
    app()
