from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from ah_screener.config import get_settings
from ah_screener.pipeline import (
    backfill_refined_candidate_snapshots,
    backtest_refined_candidates,
    candidate_changes,
    classify_existing_securities,
    compute_industry_valuation_stats,
    coverage_status,
    export_etf_candidates,
    export_expert_scores,
    export_potential_candidates,
    export_refined_candidates,
    fundamentals_status,
    ingest_company_document,
    import_custom_tags,
    import_industry_mapping,
    init_db,
    run_full_update,
    run_expert_scores,
    run_potential_scan,
    run_potential_threshold_sweep,
    run_potential_validation,
    run_technical_indicators,
    sync_a_tags,
    sync_benchmarks,
    sync_curated_theme_tags,
    sync_fundamentals,
    sync_hkex_documents,
    sync_history,
    sync_identity_mappings,
    sync_spot,
    sync_us_spot,
    sync_us_spot_batch,
    validate_etf_cluster_table,
)
from ah_screener.reporting import generate_report
from ah_screener.scheduler import install_launchd_schedule, uninstall_launchd_schedule


app = typer.Typer(help="A/H/US stock screener built on free data sources.")
console = Console()


def _fmt_optional_float(value: object, digits: int = 1, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.{digits}f}{suffix}"


def _fmt_signed_float(value: object, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):+.{digits}f}"


def _fmt_optional_pct(value: object, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:.{digits}f}%"


@app.command("init-db")
def init_db_command() -> None:
    """Create the local DuckDB schema."""
    init_db()
    console.print(f"Initialized database: {get_settings().db_path}")


@app.command("sync-spot")
def sync_spot_command(
    market: str = typer.Option("all", help="A, HK, US, ETF, or all."),
) -> None:
    """Sync A-share, Hong Kong, US, and/or A/HK ETF market snapshots."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "US", "ETF", "ALL"}:
        raise typer.BadParameter("market must be A, HK, US, ETF, or all")
    result = sync_spot("all" if normalized == "ALL" else normalized)  # type: ignore[arg-type]
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("sync-us-spot")
def sync_us_spot_command(
    symbols: str = typer.Option(
        "AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,BABA,SPY,QQQ",
        help="Comma-separated US symbols for a focused free-source sync.",
    ),
    lookback_days: int = typer.Option(14, help="Recent calendar days used to find latest close."),
) -> None:
    """Sync a focused US symbol set from Nasdaq Trader metadata and free daily prices."""
    items = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    result = sync_us_spot(items, lookback_days=lookback_days)
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("sync-us-batch")
def sync_us_batch_command(
    offset: int = typer.Option(0, help="Starting offset in the Nasdaq Trader symbol directory."),
    limit: int = typer.Option(100, help="Maximum symbols to sync in this batch."),
    include_etf: bool = typer.Option(
        False,
        "--include-etf/--stocks-only",
        help="Include US ETFs in the batch.",
    ),
    etf_only: bool = typer.Option(False, "--etf-only", help="Sync only US ETFs."),
    lookback_days: int = typer.Option(14, help="Recent calendar days used to find latest close."),
) -> None:
    """Sync a Nasdaq Trader full-list batch using Futu/OpenD first, then free fallbacks."""
    result = sync_us_spot_batch(
        offset=offset,
        limit=limit,
        include_etf=True if etf_only else include_etf,
        lookback_days=lookback_days,
        asset_type="etf" if etf_only else None,
    )
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("classify-securities")
def classify_securities_command() -> None:
    """Backfill board, asset type, ST, and HK-connect metadata."""
    result = classify_existing_securities()
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


@app.command("sync-curated-tags")
def sync_curated_tags_command() -> None:
    """Write built-in curated A/H theme tags into company_tags."""
    count = sync_curated_theme_tags()
    console.print(f"Curated theme tags: {count}")


@app.command("sync-identity-mappings")
def sync_identity_mappings_command() -> None:
    """Write curated A/H/US same-company mappings into company_identity_mappings."""
    count = sync_identity_mappings()
    console.print(f"Identity mappings: {count}")


@app.command("import-tags")
def import_tags_command(
    path: Path = typer.Option(Path("data/custom_tags.csv"), help="CSV path with market,symbol,tag_name columns."),
    source: str = typer.Option("custom_csv", help="Source label stored with imported tags."),
) -> None:
    """Import user-maintained industry, concept, theme, or risk tags from CSV."""
    count = import_custom_tags(path=path, source=source)
    console.print(f"Imported custom tags: {count}")


@app.command("import-industry-map")
def import_industry_map_command(
    path: Path = typer.Option(
        Path("data/industry_mapping.csv"),
        help="CSV path with market,symbol,detailed_industry or industry columns.",
    ),
    source: str = typer.Option("industry_mapping_csv", help="Source label stored with imported mappings."),
) -> None:
    """Import editable fine-grained industry mappings into company_tags."""
    count = import_industry_mapping(path=path, source=source)
    console.print(f"Imported industry mappings: {count}")


@app.command("sync-history")
def sync_history_command(
    market: str = typer.Option("all", help="A, HK, US, or all."),
    top: int = typer.Option(150, help="Top liquid stocks per market to fetch."),
    lookback_days: int = typer.Option(420, help="Calendar lookback days."),
    include_etf: bool = typer.Option(
        True, "--include-etf/--stocks-only", help="Also fetch top ETF daily history."
    ),
    etf_top: int = typer.Option(120, help="Top liquid ETFs per market to fetch."),
) -> None:
    """Sync historical daily prices for the most liquid names (stocks + ETFs)."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "US", "ALL"}:
        raise typer.BadParameter("market must be A, HK, US, or all")
    result = sync_history(
        "all" if normalized == "ALL" else normalized,  # type: ignore[arg-type]
        top=top,
        lookback_days=lookback_days,
        include_etf=include_etf,
        etf_top=etf_top,
    )
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("sync-benchmarks")
def sync_benchmarks_command(
    benchmarks: Optional[str] = typer.Option(
        None,
        help="Comma-separated benchmarks, for example A:000300,HK:HSI,US:SPY.",
    ),
    lookback_days: int = typer.Option(430, help="Calendar lookback days."),
) -> None:
    """Sync free A/H benchmark index history into daily_prices."""
    items = [item.strip() for item in benchmarks.split(",") if item.strip()] if benchmarks else None
    result = sync_benchmarks(benchmarks=items, lookback_days=lookback_days)
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("technical")
def technical_command() -> None:
    """Compute technical indicators from stored daily prices."""
    count = run_technical_indicators()
    console.print(f"Technical indicator rows: {count}")


@app.command("sync-fundamentals")
def sync_fundamentals_command(
    market: str = typer.Option("all", help="A, HK, US, or all."),
    top: int = typer.Option(120, help="Top liquid names per market to fetch fundamentals."),
) -> None:
    """Sync financial statements and standardized fundamental metrics."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "US", "ALL"}:
        raise typer.BadParameter("market must be A, HK, US, or all")
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


@app.command("coverage-status")
def coverage_status_command() -> None:
    """Show full-market coverage by market, asset type, and board."""
    df = coverage_status()
    if df.empty:
        console.print("No market snapshots found. Run `ah-screener sync-spot --market all` first.")
        return
    table = Table(show_header=True, header_style="bold")
    for column in [
        "market",
        "asset_type",
        "board",
        "universe",
        "technical_covered",
        "technical_pct",
        "fundamental_covered",
        "fundamental_pct",
        "expert_covered",
        "expert_pct",
    ]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            str(row["market"]),
            str(row["asset_type"]),
            str(row["board"]),
            str(row["universe"]),
            str(row["technical_covered"]),
            f"{float(row['technical_pct']):.1f}%",
            str(row["fundamental_covered"]),
            f"{float(row['fundamental_pct']):.1f}%",
            str(row["expert_covered"]),
            f"{float(row['expert_pct']):.1f}%",
        )
    console.print(table)


@app.command("expert-score")
def expert_score_command() -> None:
    """Run the built-in expert theme + master + technical model."""
    result = run_expert_scores()
    for key, count in result.items():
        console.print(f"{key}: {count}")


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
        "detailed_industry",
        "valuation_percentile",
        "peer_score",
        "industry_fit_score",
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
            str(row.get("detailed_industry") or ""),
            _fmt_optional_float(row.get("valuation_percentile")),
            _fmt_optional_float(row.get("peer_score")),
            _fmt_optional_float(row.get("industry_fit_score")),
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
        "detailed_industry",
        "valuation_percentile",
        "peer_score",
        "industry_fit_score",
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
            str(row.get("detailed_industry") or ""),
            _fmt_optional_float(row.get("valuation_percentile")),
            _fmt_optional_float(row.get("peer_score")),
            _fmt_optional_float(row.get("industry_fit_score")),
            str(row["selection_note"]),
        )
    console.print(table)


@app.command("etf-export")
def etf_export_command(
    top: int = typer.Option(100, help="Rows to show."),
    category: Optional[str] = typer.Option(None, help="Filter by ETF category."),
    market: str = typer.Option("all", help="A, HK, or all."),
    grouped: bool = typer.Option(
        True,
        "--grouped/--raw",
        help="Merge same-index or same-theme ETFs and show the best candidate per group.",
    ),
) -> None:
    """Print classified and scored ETF candidates."""
    normalized_market = market.upper()
    if normalized_market not in {"A", "HK", "ALL"}:
        raise typer.BadParameter("market must be A, HK, or all")
    df = export_etf_candidates(
        top=top,
        category=category,
        grouped=grouped,
        market=normalized_market,
    )
    if df.empty:
        console.print("No ETF rows found. Run `uv run ah-screener sync-spot --market ETF` first.")
        return

    table = Table(show_header=True, header_style="bold")
    columns = [
        "market",
        "symbol",
        "name",
        "etf_category",
        "etf_track",
        "etf_peer_group",
        "etf_score",
        "etf_recommendation",
    ]
    if grouped:
        columns.extend(["peer_count", "peer_alternatives"])
    columns.extend(
        [
            "pct_change",
            "amount",
            "market_cap",
        ]
    )
    for column in columns:
        table.add_column(column)
    for _, row in df.iterrows():
        values = [
            str(row["market"]),
            str(row["symbol"]),
            str(row["name"]),
            str(row["etf_category"]),
            str(row.get("etf_track") or ""),
            str(row.get("etf_peer_group") or ""),
            f"{float(row['etf_score']):.1f}",
            str(row["etf_recommendation"]),
        ]
        if grouped:
            values.extend(
                [
                    str(int(row.get("peer_count") or 1)),
                    str(row.get("peer_alternatives") or ""),
                ]
            )
        values.extend(
            [
                _fmt_optional_float(row.get("pct_change"), digits=2, suffix="%"),
                _fmt_optional_float(
                    float(row["amount"]) / 100_000_000 if pd.notna(row.get("amount")) else None,
                    digits=2,
                    suffix="亿",
                ),
                _fmt_optional_float(
                    float(row["market_cap"]) / 100_000_000 if pd.notna(row.get("market_cap")) else None,
                    digits=2,
                    suffix="亿",
                ),
            ]
        )
        table.add_row(*values)
    console.print(table)


@app.command("potential-validate")
def potential_validate_command() -> None:
    """Validate price-only potential setup signals on stored history."""
    df = run_potential_validation()
    if df.empty:
        console.print("No validation rows. Run sync-history first.")
        return
    table = Table(show_header=True, header_style="bold")
    for column in ["signal", "sample_count", "win_rate", "median_excess_40d", "p25_excess_40d", "p75_excess_40d", "bias_note"]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            str(row["signal"]),
            str(int(row["sample_count"])),
            _fmt_optional_float(row["win_rate"], digits=1, suffix="%"),
            _fmt_optional_float(row["median_excess_40d"], digits=3),
            _fmt_optional_float(row["p25_excess_40d"], digits=3),
            _fmt_optional_float(row["p75_excess_40d"], digits=3),
            str(row["bias_note"]),
        )
    console.print(table)


@app.command("potential-sweep")
def potential_sweep_command() -> None:
    """Grid-search rs_quiet thresholds over history (stage 9 calibration)."""
    df = run_potential_threshold_sweep()
    if df.empty:
        console.print("No sweep rows. Run sync-history first.")
        return
    table = Table(show_header=True, header_style="bold")
    for column in ["rs_rank_cut", "ret_60d_cap", "sample_count", "win_rate", "median_excess_40d", "p25_excess_40d", "p75_excess_40d"]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            f"{float(row['rs_rank_cut']):.0f}",
            f"{float(row['ret_60d_cap']):.2f}",
            str(int(row["sample_count"])),
            _fmt_optional_float(row["win_rate"], digits=1, suffix="%"),
            _fmt_optional_float(row["median_excess_40d"], digits=3),
            _fmt_optional_float(row["p25_excess_40d"], digits=3),
            _fmt_optional_float(row["p75_excess_40d"], digits=3),
        )
    console.print(table)


@app.command("potential-scan")
def potential_scan_command(top: int = typer.Option(80, help="Rows to persist and show.")) -> None:
    """Run potential-stock scan and persist scenario cards."""
    result = run_potential_scan(top=top)
    console.print(f"potential_candidates: {result.get('potential_candidates', 0)}")
    df = export_potential_candidates(top=top)
    if df.empty:
        return
    table = Table(show_header=True, header_style="bold")
    for column in ["market", "symbol", "name", "potential_score", "technical_setup_score", "relative_strength_score", "pivot_price", "target_price", "stop_price", "rr_ratio", "hist_win_rate"]:
        table.add_column(column)
    for _, row in df.head(top).iterrows():
        table.add_row(
            str(row["market"]),
            str(row["symbol"]),
            str(row.get("name") or ""),
            _fmt_optional_float(row["potential_score"]),
            _fmt_optional_float(row["technical_setup_score"]),
            _fmt_optional_float(row["relative_strength_score"]),
            _fmt_optional_float(row["pivot_price"]),
            _fmt_optional_float(row["target_price"]),
            _fmt_optional_float(row["stop_price"]),
            _fmt_optional_float(row["rr_ratio"]),
            _fmt_optional_float(row["hist_win_rate"], digits=1, suffix="%"),
        )
    console.print(table)


@app.command("etf-cluster-validate")
def etf_cluster_validate_command(
    min_corr: float = typer.Option(0.9, help="Correlation threshold for fold/merge flags."),
) -> None:
    """Empirically validate the ETF cluster table against return correlations (stage 9)."""
    df = validate_etf_cluster_table(min_corr=min_corr)
    if df.empty:
        console.print("No cluster flags — manual grouping agrees with correlations (or no history).")
        return
    table = Table(show_header=True, header_style="bold")
    for column in ["track_a", "track_b", "cluster_a", "cluster_b", "corr", "overlap_days", "relation"]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            str(row["track_a"]),
            str(row["track_b"]),
            str(row["cluster_a"]),
            str(row["cluster_b"]),
            f"{float(row['corr']):.3f}",
            str(int(row["overlap_days"])),
            str(row["relation"]),
        )
    console.print(table)


@app.command("candidate-changes")
def candidate_changes_command() -> None:
    """Compare latest refined candidates with the previous snapshot."""
    df = candidate_changes()
    if df.empty:
        console.print("No previous refined snapshot found yet.")
        return
    table = Table(show_header=True, header_style="bold")
    for column in [
        "status",
        "bucket",
        "market",
        "symbol",
        "name",
        "latest_score",
        "previous_score",
        "score_delta",
    ]:
        table.add_column(column)
    for _, row in df.iterrows():
        table.add_row(
            str(row["status"]),
            str(row["bucket"]),
            str(row["market"]),
            str(row["symbol"]),
            str(row["name"]),
            _fmt_optional_float(row["latest_score"]),
            _fmt_optional_float(row["previous_score"]),
            _fmt_signed_float(row["score_delta"]),
        )
    console.print(table)


@app.command("industry-valuation-stats")
def industry_valuation_stats_command() -> None:
    """Compute latest fine-grained industry valuation percentile summary."""
    count = compute_industry_valuation_stats()
    console.print(f"Industry valuation stats: {count}")


@app.command("ingest-document")
def ingest_document_command(
    market: str = typer.Option(..., help="A, HK, or US."),
    symbol: str = typer.Option(..., help="Security symbol."),
    path: Path = typer.Option(..., help="Local PDF/TXT/MD annual report or announcement path."),
    document_type: str = typer.Option("annual_report", help="annual_report, announcement, filing, etc."),
    report_date: Optional[str] = typer.Option(None, help="Report date in YYYY-MM-DD format."),
    title: Optional[str] = typer.Option(None, help="Document title."),
    source_url: Optional[str] = typer.Option(None, help="Official source URL, such as HKEXnews."),
    source: str = typer.Option("official_pdf", help="Source label stored with extracted evidence."),
) -> None:
    """Parse an official PDF/announcement and write evidence tags plus extracted signals."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "US"}:
        raise typer.BadParameter("market must be A, HK, or US")
    result = ingest_company_document(
        market=normalized,
        symbol=symbol,
        path=path,
        document_type=document_type,
        report_date=report_date,
        title=title,
        source_url=source_url,
        source=source,
    )
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("sync-hkex-documents")
def sync_hkex_documents_command(
    symbol: str = typer.Option(..., help="Hong Kong stock code, for example 00700."),
    output_dir: Path = typer.Option(Path("data/hkex_documents"), help="Directory for downloaded PDFs."),
    from_date: Optional[str] = typer.Option(None, help="Search start date in YYYY-MM-DD format."),
    to_date: Optional[str] = typer.Option(None, help="Search end date in YYYY-MM-DD format."),
    keywords: str = typer.Option(
        "annual report,annual results,interim results,quarterly results,announcement",
        help="Comma-separated title/category keywords. Empty string downloads latest PDFs.",
    ),
    limit: int = typer.Option(10, help="Maximum matching announcements to download and ingest."),
    lang: str = typer.Option("EN", help="HKEXnews language, EN or ZH."),
) -> None:
    """Search HKEXnews, download matching PDFs, and ingest extracted evidence."""
    keyword_items = [item.strip() for item in keywords.split(",") if item.strip()] if keywords else None
    result = sync_hkex_documents(
        symbol=symbol,
        output_dir=output_dir,
        from_date=from_date,
        to_date=to_date,
        keywords=keyword_items,
        limit=limit,
        lang=lang,
    )
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("backfill-refined-snapshots")
def backfill_refined_snapshots_command(
    min_snapshots: int = typer.Option(6, help="Minimum refined snapshots to keep for backtests."),
    rebalance: str = typer.Option("quarterly", help="snapshot, monthly, or quarterly."),
    max_per_bucket: int = typer.Option(3, help="Maximum candidates per theme bucket."),
    max_per_style: int = typer.Option(2, help="Maximum candidates per style bucket before fill."),
) -> None:
    """Create historical replay refined snapshots from stored real daily prices."""
    normalized = rebalance.lower()
    if normalized not in {"snapshot", "monthly", "quarterly"}:
        raise typer.BadParameter("rebalance must be snapshot, monthly, or quarterly")
    count = backfill_refined_candidate_snapshots(
        min_snapshots=min_snapshots,
        rebalance=normalized,  # type: ignore[arg-type]
        max_per_bucket=max_per_bucket,
        max_per_style=max_per_style,
    )
    console.print(f"Backfilled refined candidate rows: {count}")


@app.command("backtest")
def backtest_command(
    initial_capital: float = typer.Option(1_000_000, help="Starting capital."),
    max_names: int = typer.Option(12, help="Maximum holdings per rebalance snapshot."),
    rebalance: str = typer.Option("snapshot", help="snapshot, monthly, or quarterly."),
    fee_bps: float = typer.Option(5.0, help="One-way commission/tax estimate in basis points."),
    slippage_bps: float = typer.Option(10.0, help="One-way slippage estimate in basis points."),
    industry_neutral: bool = typer.Option(
        False,
        "--industry-neutral/--no-industry-neutral",
        help="Limit holdings per industry peer group.",
    ),
    max_per_group: int = typer.Option(2, help="Maximum holdings per peer group when industry-neutral."),
    benchmark: Optional[str] = typer.Option(
        None,
        help="Optional benchmark in MARKET:SYMBOL format, for example A:000300, HK:HSI, or US:SPY.",
    ),
    include_replay: bool = typer.Option(
        True,
        "--include-replay/--natural-only",
        help="Include historical replay snapshots generated by backfill-refined-snapshots.",
    ),
) -> None:
    """Run an equal-weight backtest over stored refined snapshots."""
    normalized = rebalance.lower()
    if normalized not in {"snapshot", "monthly", "quarterly"}:
        raise typer.BadParameter("rebalance must be snapshot, monthly, or quarterly")
    df = backtest_refined_candidates(
        initial_capital=initial_capital,
        max_names=max_names,
        rebalance=normalized,  # type: ignore[arg-type]
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        industry_neutral=industry_neutral,
        max_per_group=max_per_group,
        benchmark=benchmark,
        include_replay=include_replay,
    )
    if df.empty:
        console.print("No backtest rows yet. Need daily prices plus refined snapshots with future price data.")
        return
    table = Table(show_header=True, header_style="bold")
    for column in [
        "period_start",
        "period_end",
        "signal_date",
        "holdings",
        "gross_return",
        "turnover",
        "cost_rate",
        "period_return",
        "equity",
    ]:
        table.add_column(column)
    if benchmark:
        for column in [
            "benchmark",
            "benchmark_return",
            "benchmark_equity",
            "excess_return",
            "excess_equity",
        ]:
            table.add_column(column)
    for _, row in df.iterrows():
        values = [
            str(row["period_start"]),
            str(row["period_end"]),
            str(row["signal_date"]),
            str(row["holdings"]),
            _fmt_optional_pct(row["gross_return"]),
            _fmt_optional_float(row["turnover"], digits=2),
            _fmt_optional_pct(row["cost_rate"]),
            _fmt_optional_pct(row["period_return"]),
            _fmt_optional_float(row["equity"], digits=0),
        ]
        if benchmark:
            values.extend(
                [
                    str(row["benchmark"] or ""),
                    _fmt_optional_pct(row["benchmark_return"]),
                    _fmt_optional_float(row["benchmark_equity"], digits=0),
                    _fmt_optional_pct(row["excess_return"]),
                    _fmt_optional_float(row["excess_equity"], digits=0),
                ]
            )
        table.add_row(*values)
    console.print(table)


@app.command("report")
def report_command(
    output_dir: Path = typer.Option(Path("reports"), help="Directory for the Markdown report."),
) -> None:
    """Generate a Markdown research report from the current local database."""
    path = generate_report(output_dir=output_dir)
    console.print(f"Report generated: {path}")


@app.command("update-all")
def update_all_command(
    top: int = typer.Option(120, help="Top liquid names per market for history and fundamentals."),
    lookback_days: int = typer.Option(430, help="Calendar lookback days for daily price history."),
    industry_limit: Optional[int] = typer.Option(50, help="A-share industry board limit."),
    concept_limit: Optional[int] = typer.Option(120, help="A-share concept board limit."),
    skip_fundamentals: bool = typer.Option(False, help="Skip financial statements for faster refresh."),
    skip_report: bool = typer.Option(False, help="Skip Markdown report generation."),
) -> None:
    """Run the full refresh pipeline and regenerate expert outputs."""
    result = run_full_update(
        top=top,
        lookback_days=lookback_days,
        industry_limit=industry_limit,
        concept_limit=concept_limit,
        include_fundamentals=not skip_fundamentals,
        include_report=not skip_report,
    )
    for key, value in result.items():
        console.print(f"{key}: {value}")


@app.command("install-schedule")
def install_schedule_command(
    hour: int = typer.Option(18, help="Local hour for daily scheduled update."),
    minute: int = typer.Option(30, help="Local minute for daily scheduled update."),
    top: int = typer.Option(120, help="Top liquid names per market."),
    lookback_days: int = typer.Option(430, help="Calendar lookback days for price history."),
    load: bool = typer.Option(True, help="Load the LaunchAgent immediately."),
) -> None:
    """Install a macOS LaunchAgent to refresh the screener every day."""
    repo_dir = Path.cwd()
    script_path, plist_path = install_launchd_schedule(
        repo_dir=repo_dir,
        hour=hour,
        minute=minute,
        top=top,
        lookback_days=lookback_days,
    )
    console.print(f"Update script: {script_path}")
    console.print(f"LaunchAgent plist: {plist_path}")
    if not load:
        return

    target = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", target, str(plist_path)], check=False, capture_output=True)
    completed = subprocess.run(
        ["launchctl", "bootstrap", target, str(plist_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        console.print(f"Loaded schedule: daily {hour:02d}:{minute:02d}")
    else:
        console.print("LaunchAgent file was written, but launchctl did not load it automatically.")
        console.print(completed.stderr.strip())
        console.print(f"Manual command: launchctl bootstrap {target} {plist_path}")


@app.command("uninstall-schedule")
def uninstall_schedule_command() -> None:
    """Remove the macOS LaunchAgent installed by install-schedule."""
    label = "com.ah-screener.update"
    target = f"gui/{os.getuid()}"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    subprocess.run(["launchctl", "bootout", target, str(plist_path)], check=False, capture_output=True)
    removed = uninstall_launchd_schedule(label=label)
    console.print(f"Removed schedule: {removed}")


if __name__ == "__main__":
    app()
