from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
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
    ingest_failure_status,
    import_custom_tags,
    import_industry_mapping,
    init_db,
    run_full_update,
    run_expert_scores,
    run_expert_validation,
    run_potential_scan,
    run_potential_threshold_sweep,
    run_potential_validation,
    run_potential_walk_forward,
    run_technical_indicators,
    sync_a_tags,
    sync_benchmarks,
    sync_curated_theme_tags,
    sync_delisted_universe,
    sync_etf_exposures,
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


def _is_na(value: object) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return value is None


def _text(value: object) -> str:
    return "" if _is_na(value) else str(value)


def _clip(value: object, length: int = 42) -> str:
    text = _text(value)
    return text if len(text) <= length else text[: length - 1] + "…"


def _int(value: object) -> str:
    return "" if _is_na(value) else str(int(value))


def _yi(value: object) -> str:
    """Format a raw amount/market-cap as 亿 (hundred-millions)."""
    return "" if _is_na(value) else f"{float(value) / 100_000_000:.2f}亿"


@dataclass
class Col:
    """One rendered column: header, source row key, and a cell formatter."""

    header: str
    key: str
    fmt: Callable[[object], str] = field(default=_text)


def _print_df(df: pd.DataFrame, cols: list[Col], *, empty_msg: str = "No rows.") -> None:
    """Render a DataFrame as a rich table — replaces the per-command boilerplate."""
    if df is None or df.empty:
        if empty_msg:
            console.print(empty_msg)
        return
    table = Table(show_header=True, header_style="bold")
    for col in cols:
        table.add_column(col.header)
    for _, row in df.iterrows():
        table.add_row(*[col.fmt(row.get(col.key)) for col in cols])
    console.print(table)


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


@app.command("sync-etf-exposures")
def sync_etf_exposures_command(
    market: str = typer.Option("A", help="A or all. Free holdings source currently covers A-listed funds."),
    year: Optional[str] = typer.Option(None, help="Disclosure year, default current year."),
    limit: Optional[int] = typer.Option(None, help="Maximum fund codes to fetch, sorted by turnover."),
    min_amount: float = typer.Option(0.0, help="Minimum latest turnover in CNY before fetching."),
) -> None:
    """Sync ETF/LOF holdings and allocation disclosures for exposure-aware de-dup."""
    normalized = market.upper()
    if normalized not in {"A", "ALL"}:
        raise typer.BadParameter("market must be A or all")
    result = sync_etf_exposures(
        market="all" if normalized == "ALL" else "A",
        year=year,
        limit=limit,
        min_amount=min_amount,
    )
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


@app.command("sync-delisted-universe")
def sync_delisted_universe_command() -> None:
    """Sync A/HK/US delisting lifecycle records for survivorship-bias audits."""
    result = sync_delisted_universe()
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
    path: Path = typer.Option(
        Path("data/custom_tags.csv"), help="CSV path with market,symbol,tag_name columns."
    ),
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
    source: str = typer.Option(
        "industry_mapping_csv", help="Source label stored with imported mappings."
    ),
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
    full: bool = typer.Option(
        False, "--full", help="Force full backfill (ignore incremental skip)."
    ),
) -> None:
    """Sync historical daily prices incrementally (skips names already current)."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "US", "ALL"}:
        raise typer.BadParameter("market must be A, HK, US, or all")
    result = sync_history(
        "all" if normalized == "ALL" else normalized,  # type: ignore[arg-type]
        top=top,
        lookback_days=lookback_days,
        include_etf=include_etf,
        etf_top=etf_top,
        full=full,
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
    full: bool = typer.Option(
        False, "--full", help="Force full backfill (ignore incremental skip)."
    ),
) -> None:
    """Sync free A/H benchmark index history into daily_prices (incremental)."""
    items = [item.strip() for item in benchmarks.split(",") if item.strip()] if benchmarks else None
    result = sync_benchmarks(benchmarks=items, lookback_days=lookback_days, full=full)
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
    force: bool = typer.Option(
        False, "--force", help="Re-fetch all (ignore freshness/carry-forward)."
    ),
) -> None:
    """Sync fundamentals incrementally (carries forward fresh metrics; --force refetches)."""
    normalized = market.upper()
    if normalized not in {"A", "HK", "US", "ALL"}:
        raise typer.BadParameter("market must be A, HK, US, or all")
    result = sync_fundamentals(
        "all" if normalized == "ALL" else normalized,  # type: ignore[arg-type]
        top=top,
        force=force,
    )
    for key, count in result.items():
        console.print(f"{key}: {count}")


@app.command("fundamentals-status")
def fundamentals_status_command(
    top: int = typer.Option(120, help="Target top liquid names per market."),
) -> None:
    """Show estimated fundamentals sync progress from rows already stored."""
    df = fundamentals_status(top=top)
    _print_df(
        df,
        [
            Col("market", "market"),
            Col("metric_rows", "metric_rows"),
            Col("target", "target"),
            Col("remaining_estimate", "remaining_estimate"),
            Col("progress_pct", "progress_pct", lambda v: _fmt_optional_float(v, 1, "%")),
            Col("statement_items", "statement_items"),
        ],
    )


@app.command("coverage-status")
def coverage_status_command() -> None:
    """Show full-market coverage by market, asset type, and board."""
    df = coverage_status()
    if df.empty:
        console.print("No market snapshots found. Run `ah-screener sync-spot --market all` first.")
        return
    pct = lambda v: _fmt_optional_float(v, 1, "%")  # noqa: E731
    _print_df(
        df,
        [
            Col("market", "market"),
            Col("asset_type", "asset_type"),
            Col("board", "board"),
            Col("universe", "universe"),
            Col("technical_covered", "technical_covered"),
            Col("technical_pct", "technical_pct", pct),
            Col("fundamental_covered", "fundamental_covered"),
            Col("fundamental_pct", "fundamental_pct", pct),
            Col("expert_covered", "expert_covered"),
            Col("expert_pct", "expert_pct", pct),
        ],
    )


@app.command("ingest-status")
def ingest_status_command(
    limit: int = typer.Option(30, help="Most recent ingest failures to show."),
) -> None:
    """Show recent ingest-step failures (why coverage may have shrunk)."""
    df = ingest_failure_status(limit=limit)
    if df.empty:
        console.print("No ingest failures recorded. Latest refreshes completed every step.")
        return
    _print_df(
        df,
        [
            Col("run_date", "run_date"),
            Col("step", "step"),
            Col("message", "message"),
            Col("occurred_at", "occurred_at"),
        ],
    )


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

    _print_df(
        df,
        [
            Col("snapshot_date", "snapshot_date"),
            Col("market", "market"),
            Col("symbol", "symbol"),
            Col("name", "name"),
            Col("expert_score", "expert_score", _fmt_optional_float),
            Col("detailed_industry", "detailed_industry"),
            Col("valuation_percentile", "valuation_percentile", _fmt_optional_float),
            Col("peer_score", "peer_score", _fmt_optional_float),
            Col("industry_fit_score", "industry_fit_score", _fmt_optional_float),
            Col("decision", "decision"),
            Col("theme_matches", "theme_matches"),
        ],
    )


@app.command("refined-export")
def refined_export_command(
    top: int = typer.Option(50, help="Rows to show."),
) -> None:
    """Print deduplicated best candidates by theme bucket."""
    df = export_refined_candidates(top=top)
    if df.empty:
        console.print("No refined candidates found. Run `ah-screener expert-score` first.")
        return

    _print_df(
        df,
        [
            Col("bucket", "bucket"),
            Col("rank_in_bucket", "rank_in_bucket"),
            Col("style_bucket", "style_bucket"),
            Col("market", "market"),
            Col("symbol", "symbol"),
            Col("name", "name"),
            Col("expert_score", "expert_score", _fmt_optional_float),
            Col("fundamental_score", "fundamental_score", _fmt_optional_float),
            Col("technical_score", "technical_score", _fmt_optional_float),
            Col("detailed_industry", "detailed_industry"),
            Col("valuation_percentile", "valuation_percentile", _fmt_optional_float),
            Col("peer_score", "peer_score", _fmt_optional_float),
            Col("industry_fit_score", "industry_fit_score", _fmt_optional_float),
            Col("selection_note", "selection_note"),
        ],
    )


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
    dedup_by: str = typer.Option(
        "rules",
        help="Grouped mode only: rules or exposure. exposure uses synced holdings/allocation data.",
    ),
) -> None:
    """Print classified and scored ETF candidates."""
    normalized_market = market.upper()
    if normalized_market not in {"A", "HK", "ALL"}:
        raise typer.BadParameter("market must be A, HK, or all")
    normalized_dedup = dedup_by.lower()
    if normalized_dedup not in {"rules", "exposure"}:
        raise typer.BadParameter("dedup-by must be rules or exposure")
    df = export_etf_candidates(
        top=top,
        category=category,
        grouped=grouped,
        market=normalized_market,
        dedup_by=normalized_dedup,
    )
    if df.empty:
        console.print("No ETF rows found. Run `uv run ah-screener sync-spot --market ETF` first.")
        return

    cols = [
        Col("market", "market"),
        Col("symbol", "symbol"),
        Col("name", "name"),
        Col("etf_category", "etf_category"),
        Col("etf_track", "etf_track"),
        Col("etf_peer_group", "etf_peer_group"),
        Col("etf_score", "etf_score", _fmt_optional_float),
        Col("etf_recommendation", "etf_recommendation"),
    ]
    if grouped:
        cols += [
            Col("peer_count", "peer_count", lambda v: str(int(v or 1))),
            Col("peer_alternatives", "peer_alternatives"),
        ]
    if grouped and normalized_dedup == "exposure":
        cols += [
            Col("etf_dedup_basis", "etf_dedup_basis"),
            Col("etf_holding_coverage_pct", "holding_coverage", _fmt_optional_float),
            Col("etf_top_holdings", "top_holdings", _clip),
            Col("etf_primary_allocation", "primary_allocation", _clip),
        ]
    cols += [
        Col("pct_change", "pct_change", lambda v: _fmt_optional_float(v, 2, "%")),
        Col("amount", "amount", _yi),
        Col("market_cap", "market_cap", _yi),
    ]
    _print_df(df, cols)


@app.command("potential-validate")
def potential_validate_command() -> None:
    """Validate price-only potential setup signals on stored history."""
    f3 = lambda v: _fmt_optional_float(v, 3)  # noqa: E731
    _print_df(
        run_potential_validation(),
        [
            Col("signal", "signal"),
            Col("sample_count", "sample_count", _int),
            Col("win_rate", "win_rate", lambda v: _fmt_optional_float(v, 1, "%")),
            Col("median_excess_40d", "median_excess_40d", f3),
            Col("p25_excess_40d", "p25_excess_40d", f3),
            Col("p75_excess_40d", "p75_excess_40d", f3),
            Col("bias_note", "bias_note"),
        ],
        empty_msg="No validation rows. Run sync-history first.",
    )


@app.command("potential-sweep")
def potential_sweep_command() -> None:
    """Grid-search rs_quiet thresholds over history (stage 9 calibration)."""
    f3 = lambda v: _fmt_optional_float(v, 3)  # noqa: E731
    _print_df(
        run_potential_threshold_sweep(),
        [
            Col("rs_rank_cut", "rs_rank_cut", lambda v: _fmt_optional_float(v, 0)),
            Col("ret_60d_cap", "ret_60d_cap", lambda v: _fmt_optional_float(v, 2)),
            Col("sample_count", "sample_count", _int),
            Col("win_rate", "win_rate", lambda v: _fmt_optional_float(v, 1, "%")),
            Col("median_excess_40d", "median_excess_40d", f3),
            Col("p25_excess_40d", "p25_excess_40d", f3),
            Col("p75_excess_40d", "p75_excess_40d", f3),
        ],
        empty_msg="No sweep rows. Run sync-history first.",
    )


@app.command("potential-walk-forward")
def potential_walk_forward_command() -> None:
    """Validate rs_quiet thresholds with chronological walk-forward splits."""
    f3 = lambda v: _fmt_optional_float(v, 3)  # noqa: E731
    _print_df(
        run_potential_walk_forward(),
        [
            Col("fold", "fold", _int),
            Col("train_start", "train_start"),
            Col("train_end", "train_end"),
            Col("test_start", "test_start"),
            Col("test_end", "test_end"),
            Col(
                "selected_rs_rank_cut", "selected_rs_rank_cut", lambda v: _fmt_optional_float(v, 0)
            ),
            Col(
                "selected_ret_60d_cap", "selected_ret_60d_cap", lambda v: _fmt_optional_float(v, 2)
            ),
            Col("train_sample_count", "train_sample_count", _int),
            Col("train_win_rate", "train_win_rate", lambda v: _fmt_optional_float(v, 1, "%")),
            Col("train_median_excess_40d", "train_median_excess_40d", f3),
            Col("test_sample_count", "test_sample_count", _int),
            Col("test_win_rate", "test_win_rate", lambda v: _fmt_optional_float(v, 1, "%")),
            Col("test_median_excess_40d", "test_median_excess_40d", f3),
            Col("bias_note", "bias_note"),
        ],
        empty_msg="No walk-forward rows. Need longer stored history with enough setup samples.",
    )


@app.command("expert-validate")
def expert_validate_command(
    forward_days: int = typer.Option(40, help="Forward trading days for the return label."),
) -> None:
    """Validate expert decision buckets against forward excess returns (P2-4)."""
    f3 = lambda v: _fmt_optional_float(v, 3)  # noqa: E731
    stats, summary = run_expert_validation(forward_days=forward_days)
    console.print(
        f"samples={summary.get('sample_count', 0)} "
        f"snapshots={summary.get('snapshot_count', 0)} "
        f"median-excess monotonic in decision order: {summary.get('monotonic')}"
    )
    console.print(f"bias: {summary.get('bias_note', '')}")
    _print_df(
        stats,
        [
            Col("decision", "decision"),
            Col("sample_count", "sample_count", _int),
            Col("win_rate", "win_rate", lambda v: _fmt_optional_float(v, 1, "%")),
            Col("median_excess", "median_excess", f3),
            Col("p25_excess", "p25_excess", f3),
            Col("p75_excess", "p75_excess", f3),
            Col("mean_expert_score", "mean_expert_score", _fmt_optional_float),
        ],
        empty_msg=(
            "No expert-validation rows. Need natural expert snapshots with forward "
            "price history (only a couple of natural snapshots exist so far)."
        ),
    )


@app.command("potential-scan")
def potential_scan_command(top: int = typer.Option(80, help="Rows to persist and show.")) -> None:
    """Run potential-stock scan and persist scenario cards."""
    result = run_potential_scan(top=top)
    console.print(f"potential_candidates: {result.get('potential_candidates', 0)}")
    _print_df(
        export_potential_candidates(top=top).head(top),
        [
            Col("market", "market"),
            Col("symbol", "symbol"),
            Col("name", "name"),
            Col("potential_score", "potential_score", _fmt_optional_float),
            Col("technical_setup_score", "technical_setup_score", _fmt_optional_float),
            Col("relative_strength_score", "relative_strength_score", _fmt_optional_float),
            Col("pivot_price", "pivot_price", _fmt_optional_float),
            Col("target_price", "target_price", _fmt_optional_float),
            Col("stop_price", "stop_price", _fmt_optional_float),
            Col("rr_ratio", "rr_ratio", _fmt_optional_float),
            Col("hist_win_rate", "hist_win_rate", lambda v: _fmt_optional_float(v, 1, "%")),
        ],
        empty_msg="",
    )


@app.command("etf-cluster-validate")
def etf_cluster_validate_command(
    min_corr: float = typer.Option(0.9, help="Correlation threshold for fold/merge flags."),
) -> None:
    """Empirically validate the ETF cluster table against return correlations (stage 9)."""
    _print_df(
        validate_etf_cluster_table(min_corr=min_corr),
        [
            Col("track_a", "track_a"),
            Col("track_b", "track_b"),
            Col("cluster_a", "cluster_a"),
            Col("cluster_b", "cluster_b"),
            Col("corr", "corr", lambda v: _fmt_optional_float(v, 3)),
            Col("overlap_days", "overlap_days", _int),
            Col("relation", "relation"),
        ],
        empty_msg="No cluster flags — manual grouping agrees with correlations (or no history).",
    )


@app.command("candidate-changes")
def candidate_changes_command() -> None:
    """Compare latest refined candidates with the previous snapshot."""
    _print_df(
        candidate_changes(),
        [
            Col("status", "status"),
            Col("bucket", "bucket"),
            Col("market", "market"),
            Col("symbol", "symbol"),
            Col("name", "name"),
            Col("latest_score", "latest_score", _fmt_optional_float),
            Col("previous_score", "previous_score", _fmt_optional_float),
            Col("score_delta", "score_delta", _fmt_signed_float),
        ],
        empty_msg="No previous refined snapshot found yet.",
    )


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
    document_type: str = typer.Option(
        "annual_report", help="annual_report, announcement, filing, etc."
    ),
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
    output_dir: Path = typer.Option(
        Path("data/hkex_documents"), help="Directory for downloaded PDFs."
    ),
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
    keyword_items = (
        [item.strip() for item in keywords.split(",") if item.strip()] if keywords else None
    )
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
    max_per_group: int = typer.Option(
        2, help="Maximum holdings per peer group when industry-neutral."
    ),
    benchmark: Optional[str] = typer.Option(
        None,
        help="Optional benchmark in MARKET:SYMBOL format, for example A:000300, HK:HSI, or US:SPY.",
    ),
    include_replay: bool = typer.Option(
        False,
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
    f0 = lambda v: _fmt_optional_float(v, 0)  # noqa: E731
    cols = [
        Col("period_start", "period_start"),
        Col("period_end", "period_end"),
        Col("signal_date", "signal_date"),
        Col("holdings", "holdings"),
        Col("gross_return", "gross_return", _fmt_optional_pct),
        Col("turnover", "turnover", lambda v: _fmt_optional_float(v, 2)),
        Col("cost_rate", "cost_rate", _fmt_optional_pct),
        Col("period_return", "period_return", _fmt_optional_pct),
        Col("equity", "equity", f0),
    ]
    if benchmark:
        cols += [
            Col("benchmark", "benchmark"),
            Col("benchmark_return", "benchmark_return", _fmt_optional_pct),
            Col("benchmark_equity", "benchmark_equity", f0),
            Col("excess_return", "excess_return", _fmt_optional_pct),
            Col("excess_equity", "excess_equity", f0),
        ]
    _print_df(
        df,
        cols,
        empty_msg="No backtest rows yet. Need daily prices plus refined snapshots with future price data.",
    )


@app.command("report")
def report_command(
    output_dir: Path = typer.Option(Path("reports"), help="Directory for report artifacts."),
) -> None:
    """Generate the daily brief, appendix, and JSON report from the current database."""
    path = generate_report(output_dir=output_dir)
    console.print(f"Report generated: {path}")


@app.command("update-all")
def update_all_command(
    top: int = typer.Option(120, help="Top liquid names per market for daily-price history."),
    lookback_days: int = typer.Option(430, help="Calendar lookback days for daily price history."),
    industry_limit: Optional[int] = typer.Option(50, help="A-share industry board limit."),
    concept_limit: Optional[int] = typer.Option(120, help="A-share concept board limit."),
    skip_fundamentals: bool = typer.Option(
        False, help="Skip financial statements for faster refresh."
    ),
    skip_report: bool = typer.Option(False, help="Skip Markdown report generation."),
    fundamentals_top: Optional[int] = typer.Option(
        None,
        help="Top names per market for fundamentals (defaults to --top; incremental so can go deep).",
    ),
) -> None:
    """Run the full refresh pipeline and regenerate expert outputs."""
    result = run_full_update(
        top=top,
        lookback_days=lookback_days,
        industry_limit=industry_limit,
        concept_limit=concept_limit,
        include_fundamentals=not skip_fundamentals,
        include_report=not skip_report,
        fundamentals_top=fundamentals_top,
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
    subprocess.run(
        ["launchctl", "bootout", target, str(plist_path)], check=False, capture_output=True
    )
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
    subprocess.run(
        ["launchctl", "bootout", target, str(plist_path)], check=False, capture_output=True
    )
    removed = uninstall_launchd_schedule(label=label)
    console.print(f"Removed schedule: {removed}")


if __name__ == "__main__":
    app()
