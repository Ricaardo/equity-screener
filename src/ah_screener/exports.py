"""Read-only view / export layer over the local database.

Stateless queries that shape stored data for the CLI, report and UI. Separated from
``pipeline`` (ingestion + orchestration) so reads and side-effecting syncs are not
tangled in one module. ``pipeline`` re-exports these for backward compatibility.
"""

from __future__ import annotations

import pandas as pd

from ah_screener.aggregations import candidate_diff
from ah_screener.classification import enrich_security_metadata
from ah_screener.db import get_store, latest_table
from ah_screener.etf_model import consolidate_etf_candidates, enrich_etf_snapshot
from ah_screener.expert_model import STRATEGY_NAME
from ah_screener.universe import ETFS, select_assets


def export_expert_scores(top: int = 100, decision: str | None = None) -> pd.DataFrame:
    store = get_store()
    where = "WHERE strategy = ?"
    params: list[object] = [STRATEGY_NAME]
    if decision:
        where += " AND decision = ?"
        params.append(decision)
    return store.query_df(
        f"""
        SELECT *
        FROM expert_screening_results
        {where}
        ORDER BY snapshot_date DESC, expert_score DESC
        LIMIT ?
        """,
        [*params, top],
    )


def export_refined_candidates(top: int = 50) -> pd.DataFrame:
    store = get_store()
    return store.query_df(
        """
        SELECT *
        FROM refined_candidates
        ORDER BY snapshot_date DESC, bucket, rank_in_bucket
        LIMIT ?
        """,
        [top],
    )


def export_potential_candidates(top: int = 80) -> pd.DataFrame:
    store = get_store()
    return store.query_df(
        """
        SELECT *
        FROM potential_candidates
        ORDER BY snapshot_date DESC, potential_score DESC
        LIMIT ?
        """,
        [top],
    )


def export_etf_candidates(
    top: int = 100,
    category: str | None = None,
    grouped: bool = True,
    market: str | None = None,
) -> pd.DataFrame:
    store = get_store()
    snapshots = latest_table(store, "market_snapshots", "trade_date")
    if snapshots.empty or "asset_type" not in snapshots.columns:
        return pd.DataFrame()
    etfs = select_assets(snapshots, ETFS).copy()
    if market and market.upper() != "ALL":
        etfs = etfs[etfs["market"].astype(str).str.upper().eq(market.upper())]
    # Pass real technical scores so CLI etf_score matches the report/UI (review #1).
    technicals = store.query_df("SELECT * FROM technical_indicators")
    if grouped:
        return consolidate_etf_candidates(etfs, top=top, category=category, technicals=technicals)
    etfs = enrich_etf_snapshot(etfs, technicals=technicals)
    if category:
        etfs = etfs[etfs["etf_category"].eq(category)]
    return etfs.sort_values(["etf_score", "amount"], ascending=[False, False]).head(top)


def candidate_changes() -> pd.DataFrame:
    refined = get_store().query_df(
        "SELECT * FROM refined_candidates WHERE strategy = ?",
        [STRATEGY_NAME],
    )
    return candidate_diff(refined)


def coverage_status() -> pd.DataFrame:
    store = get_store()
    snapshots = store.query_df("SELECT * FROM market_snapshots")
    if snapshots.empty:
        return pd.DataFrame(
            columns=[
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
            ]
        )

    snapshots = snapshots.copy()
    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"], errors="coerce")
    snapshots = snapshots.sort_values("trade_date").drop_duplicates(
        ["market", "symbol"], keep="last"
    )
    securities = store.query_df("SELECT * FROM securities")
    if not securities.empty:
        securities = enrich_security_metadata(securities)
        metadata_columns = [
            column
            for column in ["asset_type", "board", "exchange", "status", "is_st", "is_hk_connect"]
            if column in securities.columns
        ]
        snapshots = snapshots.drop(
            columns=[column for column in metadata_columns if column in snapshots.columns]
        )
        snapshots = snapshots.merge(
            securities[["market", "symbol", *metadata_columns]].drop_duplicates(
                ["market", "symbol"]
            ),
            on=["market", "symbol"],
            how="left",
        )

    if "asset_type" not in snapshots.columns:
        snapshots["asset_type"] = "stock"
    if "board" not in snapshots.columns:
        snapshots["board"] = "未分类"
    snapshots["asset_type"] = snapshots["asset_type"].fillna("stock")
    snapshots["board"] = snapshots["board"].fillna("未分类")

    technicals = latest_table(store, "technical_indicators", "snapshot_date")
    fundamentals = latest_table(store, "financial_metrics", "snapshot_date")
    expert = latest_table(store, "expert_screening_results", "snapshot_date")
    if not expert.empty and "strategy" in expert.columns:
        expert = expert[expert["strategy"] == STRATEGY_NAME]

    for name, df in [
        ("technical", technicals),
        ("fundamental", fundamentals),
        ("expert", expert),
    ]:
        flag = f"has_{name}"
        keys = (
            df[["market", "symbol"]].drop_duplicates().assign(**{flag: True})
            if not df.empty
            else pd.DataFrame(columns=["market", "symbol", flag])
        )
        snapshots = snapshots.merge(keys, on=["market", "symbol"], how="left")
        snapshots[flag] = snapshots[flag].eq(True)

    status = (
        snapshots.groupby(["market", "asset_type", "board"], dropna=False)
        .agg(
            universe=("symbol", "count"),
            technical_covered=("has_technical", "sum"),
            fundamental_covered=("has_fundamental", "sum"),
            expert_covered=("has_expert", "sum"),
        )
        .reset_index()
    )
    for prefix in ["technical", "fundamental", "expert"]:
        status[f"{prefix}_pct"] = (
            (status[f"{prefix}_covered"] / status["universe"].replace(0, pd.NA) * 100)
            .fillna(0)
            .round(1)
        )

    return status[
        [
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
        ]
    ].sort_values(["market", "asset_type", "universe"], ascending=[True, True, False])


def fundamentals_status(top: int = 120) -> pd.DataFrame:
    store = get_store()
    metrics = store.query_df(
        """
        SELECT market, COUNT(*) AS metric_rows
        FROM financial_metrics
        GROUP BY market
        """
    )
    items = store.query_df(
        """
        SELECT market, COUNT(*) AS statement_items
        FROM financial_statement_items
        GROUP BY market
        """
    )
    markets = pd.DataFrame({"market": ["A", "HK", "US"]})
    status = markets.merge(metrics, on="market", how="left").merge(items, on="market", how="left")
    status["metric_rows"] = status["metric_rows"].fillna(0).astype(int)
    status["statement_items"] = status["statement_items"].fillna(0).astype(int)
    status["target"] = top
    status["remaining_estimate"] = (status["target"] - status["metric_rows"]).clip(lower=0)
    status["progress_pct"] = (
        (status["metric_rows"] / status["target"] * 100).clip(upper=100).round(1)
    )
    return status[
        ["market", "metric_rows", "target", "remaining_estimate", "progress_pct", "statement_items"]
    ]


def ingest_failure_status(limit: int = 30) -> pd.DataFrame:
    """Recent ingest-step failures, newest first."""
    store = get_store()
    store.init_db()
    return store.query_df(
        """
        SELECT run_date, step, message, occurred_at
        FROM ingest_failures
        ORDER BY occurred_at DESC
        LIMIT ?
        """,
        [limit],
    )
