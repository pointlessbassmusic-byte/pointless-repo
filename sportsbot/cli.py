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


@app.command()
def status(config: str = CONFIG_OPT):
    """Exposure, PnL, calibration, kill-switch state."""
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


@app.command("reset-kill-switch")
def reset_kill_switch(config: str = CONFIG_OPT):
    cfg = _setup(config)
    from sportsbot.data.store import Store

    Store(cfg.get("storage", {}).get("sqlite_path", "data/sportsbot.sqlite")).set_kv(
        "kill_switch_tripped", False)
    console.print("[green]kill switch reset")


if __name__ == "__main__":
    app()
