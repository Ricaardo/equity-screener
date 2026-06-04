"""us-screener CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer

from us_screener.config import PROJECT_ROOT, get_us_config, use_us_database

app = typer.Typer(add_completion=False, help="US-only stock auto-screener (independent DuckDB).")


def _clean(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if np.isnan(number) or np.isinf(number) else number
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.strftime("%Y-%m-%d")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, (str, int, list, dict)) else str(value)


def _records(df: pd.DataFrame, fields: list[str]) -> list[dict[str, object]]:
    if df.empty:
        return []
    present = [field for field in fields if field in df.columns]
    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        rows.append({field: _clean(row.get(field)) for field in present})
    return rows


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    else:
        for key, value in payload.items():
            typer.echo(f"{key}: {value}")


@app.command("info")
def info_command(as_json: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
    """Show resolved config (db path, LLM provider, schedule, filters)."""
    cfg = get_us_config()
    _emit(
        {
            "db_path": str(cfg.db_path),
            "reports_dir": str(cfg.reports_dir),
            "min_us_amount": cfg.min_us_amount,
            "min_market_cap": cfg.min_market_cap,
            "recommend_min_us_amount": cfg.recommend_min_us_amount,
            "recommend_min_market_cap": cfg.recommend_min_market_cap,
            "recommend_min_price": cfg.recommend_min_price,
            "exclude_china_concept": cfg.exclude_china_concept,
            "data_source": "futu" if cfg.use_futu else "free (sina/alpaca/stooq/SEC)",
            "stooq_zip": cfg.stooq_zip,
            "llm_provider": cfg.llm_provider,
            "llm_model": cfg.llm_model,
            "llm_api_key_present": bool(cfg.llm_api_key),
            "schedule": f"{cfg.schedule_hour:02d}:{cfg.schedule_minute:02d}",
        },
        as_json,
    )


@app.command("backfill")
def backfill_command(
    history_top: int = typer.Option(4000, help="Max stocks (by turnover) for history backfill."),
    lookback_days: int = typer.Option(1100, help="History window for first full backfill."),
    fundamentals_top: int = typer.Option(1500, help="Top names for SEC fundamentals."),
    include_etf: bool = typer.Option(True, help="Include US ETFs."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """First-run full localization of the US universe into the independent DuckDB."""
    use_us_database()
    from us_screener.pipeline_us import run_us_full_backfill

    result = run_us_full_backfill(
        history_top=history_top,
        lookback_days=lookback_days,
        fundamentals_top=fundamentals_top,
        include_etf=include_etf,
    )
    _emit(result, as_json)


@app.command("update")
def update_command(
    history_top: int = typer.Option(4000, help="Max stocks (by turnover) for history refresh."),
    lookback_days: int = typer.Option(430, help="History window for incremental refresh."),
    fundamentals_top: int = typer.Option(0, help="0 = skip fundamentals on incremental runs."),
    include_etf: bool = typer.Option(True, help="Include US ETFs."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Incremental daily refresh (data layer + screen + report)."""
    use_us_database()
    from us_screener.pipeline_us import run_us_premarket_update

    result = run_us_premarket_update(
        history_top=history_top,
        lookback_days=lookback_days,
        fundamentals_top=fundamentals_top or None,
        include_etf=include_etf,
    )
    _emit(result, as_json)


@app.command("load-stooq")
def load_stooq_command(
    zip_path: Path = typer.Argument(..., help="Path to a stooq daily-history ZIP (d_us_txt.zip / d_world_txt.zip / ...)."),
    since: str = typer.Option("2022-01-01", help="Earliest trade date to load (YYYY-MM-DD)."),
    markets: str = typer.Option("", help="Comma-separated markets to keep (e.g. US,HK,JP). Empty = all in the archive."),
    include_etf: bool = typer.Option(True, help="Include ETFs."),
    delete_zip: bool = typer.Option(False, help="Delete the source ZIP after a successful load."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Bulk-load a local stooq daily-history ZIP into daily_prices (any market, no API)."""
    use_us_database()
    from ah_screener.db import get_store
    from us_screener.stooq_loader import load_stooq_zip

    market_list = [m.strip().upper() for m in markets.split(",") if m.strip()] or None
    result = load_stooq_zip(
        get_store(), zip_path, since=since, markets=market_list,
        include_etf=include_etf, delete_zip=delete_zip,
    )
    _emit(result, as_json)


@app.command("load-sec-facts")
def load_sec_facts_command(
    zip_path: Path = typer.Argument(..., help="Path to the SEC companyfacts.zip bulk archive."),
    no_snapshots: bool = typer.Option(False, help="Only write financial_metrics, skip snapshot valuation fill."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Bulk-load SEC companyfacts.zip into fundamentals + snapshot market_cap/PE/PB (no API)."""
    use_us_database()
    from ah_screener.db import get_store
    from us_screener.sec_bulk_loader import load_companyfacts_zip

    result = load_companyfacts_zip(get_store(), zip_path, fill_snapshots=not no_snapshots)
    _emit(result, as_json)


@app.command("global-screen")
def global_screen_command(
    market: str = typer.Argument(..., help="Market code to screen (HK / JP / UK)."),
    db: Path = typer.Option(PROJECT_ROOT / "data" / "global_history.duckdb", help="Global history DuckDB."),
    top: int = typer.Option(25, help="Top candidates to return."),
    min_amount: float = typer.Option(0.0, help="Min latest turnover (pre-filter; higher = faster)."),
    lookback_days: int = typer.Option(420, help="Only load this much recent history (faster)."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Price-only technical screen over the banked global history (no fundamentals)."""
    from ah_screener.storage import Store
    from us_screener.global_screener import screen_market

    result = screen_market(Store(db), market, top=top, min_amount=min_amount, lookback_days=lookback_days)
    _emit(result, as_json)


@app.command("screen")
def screen_command(
    top: int = typer.Option(20, help="Number of top rows to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Run the US-tuned screen."""
    use_us_database()
    from us_screener.scoring_us import run_us_screen

    result = run_us_screen(persist=True)
    scored = result["results"]
    top_rows = scored.loc[~scored["is_filtered"]].head(top) if not scored.empty else pd.DataFrame()
    payload = {
        "snapshot_date": result["snapshot_date"],
        "persisted_rows": result["persisted_rows"],
        "macro_context": result["macro_context"],
        "top_candidates": _records(
            top_rows,
            [
                "market",
                "symbol",
                "name",
                "expert_score",
                "decision",
                "fundamental_score_final",
                "technical_score",
                "heat_score",
                "macro_score",
                "concept_boards",
                "score_components",
            ],
        ),
    }
    _emit(payload, as_json)


@app.command("report")
def report_command(
    output_dir: Path | None = typer.Option(None, help="Directory for US report artifacts."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Generate the dated and latest US pre-market report artifacts."""
    use_us_database()
    from us_screener.reporting_us import generate_us_premarket_report

    cfg = get_us_config()
    base_dir = output_dir or cfg.reports_dir
    path = generate_us_premarket_report(output_dir=base_dir)
    report_date = path.stem.replace("us-premarket-", "")
    payload = {
        "markdown_path": str(path),
        "json_path": str(base_dir / f"us-premarket-{report_date}.json"),
        "latest_markdown_path": str(base_dir / "us-premarket-latest.md"),
        "latest_json_path": str(base_dir / "us-premarket-latest.json"),
    }
    _emit(payload, as_json)


@app.command("opinion")
def opinion_command(as_json: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
    """Generate or skip the optional LLM opinion."""
    use_us_database()
    from us_screener.reporting_us import build_us_premarket_payload

    opinion = (build_us_premarket_payload()).get("llm_opinion") or {}
    _emit(opinion, as_json)


@app.command("schedule")
def schedule_command(
    hour: int | None = typer.Option(None, help="Local hour for daily US pre-market refresh."),
    minute: int | None = typer.Option(None, help="Local minute for daily US pre-market refresh."),
    history_top: int = typer.Option(4000, help="Top liquid names for history refresh."),
    lookback_days: int = typer.Option(430, help="Calendar lookback days for price history."),
    fundamentals_top: int = typer.Option(0, help="0 keeps incremental updates lightweight."),
    load: bool = typer.Option(True, help="Load the LaunchAgent immediately."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Install a macOS LaunchAgent for the US screener."""
    use_us_database()
    from us_screener.scheduler_us import LABEL, install_us_launchd_schedule

    cfg = get_us_config()
    resolved_hour = cfg.schedule_hour if hour is None else hour
    resolved_minute = cfg.schedule_minute if minute is None else minute
    script_path, plist_path = install_us_launchd_schedule(
        repo_dir=PROJECT_ROOT,
        hour=resolved_hour,
        minute=resolved_minute,
        history_top=history_top,
        lookback_days=lookback_days,
        fundamentals_top=fundamentals_top,
        label=LABEL,
    )
    payload: dict[str, Any] = {
        "script_path": str(script_path),
        "plist_path": str(plist_path),
        "loaded": False,
        "schedule": f"{resolved_hour:02d}:{resolved_minute:02d}",
    }
    if load:
        target = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", target, str(plist_path)],
            check=False,
            capture_output=True,
        )
        completed = subprocess.run(
            ["launchctl", "bootstrap", target, str(plist_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        payload["loaded"] = completed.returncode == 0
        if completed.returncode != 0:
            payload["launchctl_error"] = completed.stderr.strip()
    _emit(payload, as_json)


@app.command("mcp")
def mcp_command(as_json: bool = typer.Option(False, "--json", help="Emit JSON status instead of serving.")) -> None:
    """Run the optional FastMCP server."""
    use_us_database()
    from us_screener.mcp_server import create_mcp_server

    if as_json:
        try:
            create_mcp_server()
            payload = {"available": True, "reports_dir": str(get_us_config().reports_dir)}
        except RuntimeError as exc:
            payload = {
                "available": False,
                "reports_dir": str(get_us_config().reports_dir),
                "error": str(exc),
            }
        _emit(payload, True)
        return

    try:
        server = create_mcp_server()
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    server.run()


if __name__ == "__main__":
    app()
