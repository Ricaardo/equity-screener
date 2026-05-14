from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

import pandas as pd

from ah_screener.config import get_settings
from ah_screener.expert_model import STRATEGY_NAME, refine_candidates, run_expert_model
from ah_screener.fundamentals import fetch_fundamentals
from ah_screener.reporting import generate_report
from ah_screener.scoring import score_snapshot
from ah_screener.sources.akshare_client import fetch_a_board_tags, fetch_history, fetch_spot
from ah_screener.storage import Store
from ah_screener.technical import compute_technical_indicators


MarketArg = Literal["A", "HK", "all"]


def get_store() -> Store:
    return Store(get_settings().db_path)


def init_db() -> None:
    get_store().init_db()


def sync_spot(market: MarketArg) -> dict[str, int]:
    store = get_store()
    store.init_db()
    markets = ["A", "HK"] if market == "all" else [market]
    result: dict[str, int] = {}
    for item in markets:
        securities, snapshots = fetch_spot(item)  # type: ignore[arg-type]
        result[f"{item}_securities"] = store.upsert_dataframe("securities", securities)
        result[f"{item}_snapshots"] = store.upsert_dataframe("market_snapshots", snapshots)
    return result


def sync_a_tags(kind: Literal["industry", "concept"], limit: int | None) -> int:
    store = get_store()
    store.init_db()
    tags = fetch_a_board_tags(kind=kind, limit=limit)
    return store.upsert_dataframe("company_tags", tags)


def run_scores() -> int:
    store = get_store()
    store.init_db()
    snapshots = store.query_df("SELECT * FROM market_snapshots")
    tags = store.query_df("SELECT * FROM company_tags")
    scores = score_snapshot(snapshots=snapshots, tags=tags, settings=get_settings())
    return store.upsert_dataframe("screening_scores", scores)


def _latest_snapshots(store: Store) -> pd.DataFrame:
    snapshots = store.query_df("SELECT * FROM market_snapshots")
    if snapshots.empty:
        return snapshots
    latest_date = snapshots["trade_date"].max()
    return snapshots[snapshots["trade_date"] == latest_date].drop_duplicates(
        ["market", "symbol"], keep="last"
    )


def sync_history(market: MarketArg, top: int = 150, lookback_days: int = 420) -> dict[str, int]:
    store = get_store()
    store.init_db()
    latest = _latest_snapshots(store)
    if latest.empty:
        raise RuntimeError("No market snapshots found. Run `ah-screener sync-spot --market all` first.")

    markets = ["A", "HK"] if market == "all" else [market]
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    result: dict[str, int] = {}
    for item in markets:
        universe = (
            latest[latest["market"] == item]
            .assign(amount_num=lambda df: pd.to_numeric(df["amount"], errors="coerce").fillna(0))
            .sort_values("amount_num", ascending=False)
            .head(top)
        )
        inserted = 0
        failed = 0
        for symbol in universe["symbol"].astype(str):
            try:
                history = fetch_history(item, symbol=symbol, start_date=start, end_date=end)
            except Exception:
                failed += 1
                continue
            inserted += store.upsert_dataframe("daily_prices", history)
        result[f"{item}_history_rows"] = inserted
        result[f"{item}_history_failed_symbols"] = failed
    return result


def run_technical_indicators() -> int:
    store = get_store()
    store.init_db()
    daily_prices = store.query_df("SELECT * FROM daily_prices")
    snapshots = store.query_df("SELECT * FROM market_snapshots")
    indicators = compute_technical_indicators(daily_prices=daily_prices, snapshots=snapshots)
    return store.upsert_dataframe("technical_indicators", indicators)


def run_expert_scores() -> dict[str, int]:
    store = get_store()
    store.init_db()
    snapshots = store.query_df("SELECT * FROM market_snapshots")
    tags = store.query_df("SELECT * FROM company_tags")
    technicals = store.query_df("SELECT * FROM technical_indicators")
    fundamentals = store.query_df("SELECT * FROM financial_metrics")
    results, themes = run_expert_model(
        snapshots=snapshots,
        tags=tags,
        technicals=technicals,
        fundamentals=fundamentals,
        settings=get_settings(),
    )
    refined = refine_candidates(results, max_per_bucket=3)
    if not results.empty:
        latest_date = results["snapshot_date"].max()
        for strategy in results["strategy"].dropna().unique():
            store.execute(
                "DELETE FROM refined_candidates WHERE snapshot_date = ? AND strategy = ?",
                [latest_date, strategy],
            )
    return {
        "expert_screening_results": store.upsert_dataframe("expert_screening_results", results),
        "hot_theme_definitions": store.upsert_dataframe("hot_theme_definitions", themes),
        "refined_candidates": store.upsert_dataframe("refined_candidates", refined),
    }


def sync_fundamentals(market: MarketArg, top: int = 120) -> dict[str, int]:
    store = get_store()
    store.init_db()
    latest = _latest_snapshots(store)
    if latest.empty:
        raise RuntimeError("No market snapshots found. Run `ah-screener sync-spot --market all` first.")

    markets = ["A", "HK"] if market == "all" else [market]
    snapshot_date = latest["trade_date"].max()
    result: dict[str, int] = {}
    for item in markets:
        universe = (
            latest[latest["market"] == item]
            .assign(amount_num=lambda df: pd.to_numeric(df["amount"], errors="coerce").fillna(0))
            .sort_values("amount_num", ascending=False)
            .head(top)
        )
        statement_rows = 0
        metric_rows = 0
        failed = 0
        for symbol in universe["symbol"].astype(str):
            try:
                items, metrics = fetch_fundamentals(item, symbol, snapshot_date=snapshot_date)  # type: ignore[arg-type]
            except Exception:
                failed += 1
                continue
            statement_rows += store.upsert_dataframe("financial_statement_items", items)
            metric_rows += store.upsert_dataframe("financial_metrics", metrics)
        result[f"{item}_financial_statement_items"] = statement_rows
        result[f"{item}_financial_metric_rows"] = metric_rows
        result[f"{item}_financial_failed_symbols"] = failed
    return result


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
    markets = pd.DataFrame({"market": ["A", "HK"]})
    status = markets.merge(metrics, on="market", how="left").merge(items, on="market", how="left")
    status["metric_rows"] = status["metric_rows"].fillna(0).astype(int)
    status["statement_items"] = status["statement_items"].fillna(0).astype(int)
    status["target"] = top
    status["remaining_estimate"] = (status["target"] - status["metric_rows"]).clip(lower=0)
    status["progress_pct"] = (status["metric_rows"] / status["target"] * 100).clip(upper=100).round(1)
    return status[
        ["market", "metric_rows", "target", "remaining_estimate", "progress_pct", "statement_items"]
    ]


def export_scores(top: int = 100, decision: str | None = None) -> pd.DataFrame:
    store = get_store()
    where = ""
    params: list[object] = []
    if decision:
        where = "WHERE decision = ?"
        params.append(decision)
    return store.query_df(
        f"""
        SELECT *
        FROM screening_scores
        {where}
        ORDER BY snapshot_date DESC, total_score DESC
        LIMIT ?
        """,
        [*params, top],
    )


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


def run_full_update(
    top: int = 120,
    lookback_days: int = 430,
    industry_limit: int | None = 50,
    concept_limit: int | None = 120,
    include_fundamentals: bool = True,
    include_report: bool = True,
) -> dict[str, object]:
    result: dict[str, object] = {}
    result["sync_spot"] = sync_spot("all")
    if industry_limit is not None:
        result["a_industry_tags"] = sync_a_tags("industry", limit=industry_limit)
    if concept_limit is not None:
        result["a_concept_tags"] = sync_a_tags("concept", limit=concept_limit)
    result["basic_scores"] = run_scores()
    result["history"] = sync_history("all", top=top, lookback_days=lookback_days)
    result["technical_rows"] = run_technical_indicators()
    if include_fundamentals:
        result["fundamentals"] = sync_fundamentals("all", top=top)
    result["expert_scores"] = run_expert_scores()
    if include_report:
        result["report"] = str(generate_report())
    return result
