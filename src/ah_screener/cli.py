from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ah_screener.config import get_settings
from ah_screener.pipeline import (
    export_expert_scores,
    export_refined_candidates,
    export_scores,
    fundamentals_status,
    init_db,
    run_expert_scores,
    run_scores,
    run_technical_indicators,
    sync_a_tags,
    sync_fundamentals,
    sync_history,
    sync_spot,
)


app = typer.Typer(help="A/H stock screener built on free data sources.")
console = Console()


@app.command("init-db")
def init_db_command() -> None:
    """Create the local DuckDB schema."""
    init_db()
    console.print(f"Initialized database: {get_settings().db_path}")


@app.command("sync-spot")
def sync_spot_command(
    market: str = typer.Option("all", help="A, HK, or all."),
) -> None:
    """Sync A-share and/or Hong Kong market snapshots."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "ALL"}:
        raise typer.BadParameter("market must be A, HK, or all")
    result = sync_spot("all" if normalized == "ALL" else normalized)  # type: ignore[arg-type]
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("sync-a-tags")
def sync_a_tags_command(
    kind: str = typer.Option("industry", help="industry or concept."),
    limit: Optional[int] = typer.Option(None, help="Limit board count for a faster first run."),
) -> None:
    """Sync A-share industry or concept board membership."""
    normalized = kind.lower()
    if normalized not in {"industry", "concept"}:
        raise typer.BadParameter("kind must be industry or concept")
    count = sync_a_tags(normalized, limit=limit)  # type: ignore[arg-type]
    console.print(f"A-share {normalized} tags: {count}")


@app.command("score")
def score_command() -> None:
    """Run the screening score model on the latest snapshot."""
    count = run_scores()
    console.print(f"Scored securities: {count}")


@app.command("sync-history")
def sync_history_command(
    market: str = typer.Option("all", help="A, HK, or all."),
    top: int = typer.Option(150, help="Top liquid names per market to fetch."),
    lookback_days: int = typer.Option(420, help="Calendar lookback days."),
) -> None:
    """Sync historical daily prices for the most liquid names."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "ALL"}:
        raise typer.BadParameter("market must be A, HK, or all")
    result = sync_history(
        "all" if normalized == "ALL" else normalized,  # type: ignore[arg-type]
        top=top,
        lookback_days=lookback_days,
    )
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("technical")
def technical_command() -> None:
    """Compute technical indicators from stored daily prices."""
    count = run_technical_indicators()
    console.print(f"Technical indicator rows: {count}")


@app.command("sync-fundamentals")
def sync_fundamentals_command(
    market: str = typer.Option("all", help="A, HK, or all."),
    top: int = typer.Option(120, help="Top liquid names per market to fetch fundamentals."),
) -> None:
    """Sync financial statements and standardized fundamental metrics."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "ALL"}:
        raise typer.BadParameter("market must be A, HK, or all")
    result = sync_fundamentals(
        "all" if normalized == "ALL" else normalized,  # type: ignore[arg-type]
        top=top,
    )
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("fundamentals-status")
def fundamentals_status_command(
    top: int = typer.Option(120, help="Target top liquid names per market."),
) -> None:
    """Show estimated fundamentals sync progress from rows already stored."""
    df = fundamentals_status(top=top)
    table = Table(show_header=True, header_style="bold")
    for column in [
        "market",
        "metric_rows",
        "target",
        "remaining_estimate",
        "progress_pct",
        "statement_items",
    ]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            str(row["market"]),
            str(row["metric_rows"]),
            str(row["target"]),
            str(row["remaining_estimate"]),
            f"{float(row['progress_pct']):.1f}%",
            str(row["statement_items"]),
        )
    console.print(table)


@app.command("expert-score")
def expert_score_command() -> None:
    """Run the built-in expert theme + master + technical model."""
    result = run_expert_scores()
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("export")
def export_command(
    top: int = typer.Option(100, help="Rows to show."),
    decision: Optional[str] = typer.Option(None, help="Filter by keep, watch, or reject."),
) -> None:
    """Print top screening results."""
    df = export_scores(top=top, decision=decision)
    if df.empty:
        console.print("No scores found. Run `ah-screener score` first.")
        return

    table = Table(show_header=True, header_style="bold")
    for column in ["snapshot_date", "market", "symbol", "name", "total_score", "decision"]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            str(row["snapshot_date"]),
            str(row["market"]),
            str(row["symbol"]),
            str(row["name"]),
            f"{float(row['total_score']):.1f}",
            str(row["decision"]),
        )
    console.print(table)


@app.command("expert-export")
def expert_export_command(
    top: int = typer.Option(100, help="Rows to show."),
    decision: Optional[str] = typer.Option(
        None, help="Filter by core_candidate, watchlist, reserve, or reject."
    ),
) -> None:
    """Print top expert-screening results."""
    df = export_expert_scores(top=top, decision=decision)
    if df.empty:
        console.print("No expert scores found. Run `ah-screener expert-score` first.")
        return

    table = Table(show_header=True, header_style="bold")
    for column in [
        "snapshot_date",
        "market",
        "symbol",
        "name",
        "expert_score",
        "decision",
        "theme_matches",
    ]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            str(row["snapshot_date"]),
            str(row["market"]),
            str(row["symbol"]),
            str(row["name"]),
            f"{float(row['expert_score']):.1f}",
            str(row["decision"]),
            str(row["theme_matches"]),
        )
    console.print(table)


@app.command("refined-export")
def refined_export_command(
    top: int = typer.Option(50, help="Rows to show."),
) -> None:
    """Print deduplicated best candidates by theme bucket."""
    df = export_refined_candidates(top=top)
    if df.empty:
        console.print("No refined candidates found. Run `ah-screener expert-score` first.")
        return

    table = Table(show_header=True, header_style="bold")
    for column in [
        "bucket",
        "rank_in_bucket",
        "style_bucket",
        "market",
        "symbol",
        "name",
        "expert_score",
        "fundamental_score",
        "technical_score",
        "selection_note",
    ]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            str(row["bucket"]),
            str(row["rank_in_bucket"]),
            str(row["style_bucket"]),
            str(row["market"]),
            str(row["symbol"]),
            str(row["name"]),
            f"{float(row['expert_score']):.1f}",
            f"{float(row['fundamental_score']):.1f}",
            f"{float(row['technical_score']):.1f}",
            str(row["selection_note"]),
        )
    console.print(table)


if __name__ == "__main__":
    app()
