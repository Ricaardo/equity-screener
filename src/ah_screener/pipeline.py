from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

import pandas as pd

from ah_screener.classification import enrich_security_metadata
from ah_screener.config import get_settings
from ah_screener.etf_model import enrich_etf_snapshot
from ah_screener.expert_model import STRATEGY_NAME, refine_candidates, run_expert_model
from ah_screener.fundamentals import fetch_fundamentals
from ah_screener.reporting import generate_report
from ah_screener.scoring import score_snapshot
from ah_screener.sources.akshare_client import fetch_a_board_tags, fetch_a_etf_spot, fetch_history, fetch_spot
from ah_screener.storage import Store
from ah_screener.technical import compute_technical_indicators


MarketArg = Literal["A", "HK", "ETF", "all"]


def get_store() -> Store:
    return Store(get_settings().db_path)


def init_db() -> None:
    get_store().init_db()


def sync_spot(market: MarketArg) -> dict[str, int]:
    store = get_store()
    store.init_db()
    result: dict[str, int] = {}
    markets = ["A", "HK"] if market == "all" else ([] if market == "ETF" else [market])
    for item in markets:
        securities, snapshots = fetch_spot(item)  # type: ignore[arg-type]
        result[f"{item}_securities"] = store.upsert_dataframe("securities", securities)
        result[f"{item}_snapshots"] = store.upsert_dataframe("market_snapshots", snapshots)
    if market in {"A", "ETF", "all"}:
        etf_securities, etf_snapshots = fetch_a_etf_spot()
        result["A_etf_securities"] = store.upsert_dataframe("securities", etf_securities)
        result["A_etf_snapshots"] = store.upsert_dataframe("market_snapshots", etf_snapshots)
    return result


def classify_existing_securities() -> dict[str, int]:
    store = get_store()
    store.init_db()
    securities = store.query_df("SELECT * FROM securities")
    if securities.empty:
        return {"securities": 0, "snapshots": 0}
    enriched = enrich_security_metadata(securities)
    security_rows = store.upsert_dataframe("securities", enriched)
    snapshot_count = int(store.query_df("SELECT COUNT(*) AS count FROM market_snapshots")["count"].iloc[0])
    return {"securities": security_rows, "snapshots": snapshot_count}


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
    if "asset_type" in snapshots.columns:
        snapshots = snapshots[snapshots["asset_type"].fillna("stock") == "stock"]
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
    if "asset_type" in snapshots.columns:
        snapshots = snapshots[snapshots["asset_type"].fillna("stock") == "stock"]
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
        store.execute(
            "DELETE FROM financial_metrics WHERE snapshot_date = ? AND market = ?",
            [snapshot_date, item],
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


def _latest_table(store: Store, table: str, date_column: str) -> pd.DataFrame:
    df = store.query_df(f"SELECT * FROM {table}")
    if df.empty or date_column not in df.columns:
        return df
    return df[df[date_column] == df[date_column].max()].copy()


def coverage_status() -> pd.DataFrame:
    store = get_store()
    snapshots = _latest_table(store, "market_snapshots", "trade_date")
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

    snapshots = snapshots.drop_duplicates(["market", "symbol"], keep="last").copy()
    securities = store.query_df("SELECT * FROM securities")
    if not securities.empty:
        securities = enrich_security_metadata(securities)
        metadata_columns = [
            column
            for column in ["asset_type", "board", "exchange", "status", "is_st", "is_hk_connect"]
            if column in securities.columns
        ]
        snapshots = snapshots.drop(columns=[column for column in metadata_columns if column in snapshots.columns])
        snapshots = snapshots.merge(
            securities[["market", "symbol", *metadata_columns]].drop_duplicates(["market", "symbol"]),
            on=["market", "symbol"],
            how="left",
        )

    if "asset_type" not in snapshots.columns:
        snapshots["asset_type"] = "stock"
    if "board" not in snapshots.columns:
        snapshots["board"] = "未分类"
    snapshots["asset_type"] = snapshots["asset_type"].fillna("stock")
    snapshots["board"] = snapshots["board"].fillna("未分类")

    technicals = _latest_table(store, "technical_indicators", "snapshot_date")
    fundamentals = _latest_table(store, "financial_metrics", "snapshot_date")
    expert = _latest_table(store, "expert_screening_results", "snapshot_date")
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
            status[f"{prefix}_covered"] / status["universe"].replace(0, pd.NA) * 100
        ).fillna(0).round(1)

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


def export_etf_candidates(top: int = 100, category: str | None = None) -> pd.DataFrame:
    store = get_store()
    snapshots = _latest_table(store, "market_snapshots", "trade_date")
    if snapshots.empty or "asset_type" not in snapshots.columns:
        return pd.DataFrame()
    etfs = snapshots[snapshots["asset_type"].fillna("stock").eq("etf")].copy()
    etfs = enrich_etf_snapshot(etfs)
    if category:
        etfs = etfs[etfs["etf_category"].eq(category)]
    return etfs.sort_values(["etf_score", "amount"], ascending=[False, False]).head(top)


def candidate_changes() -> pd.DataFrame:
    store = get_store()
    refined = store.query_df(
        """
        SELECT *
        FROM refined_candidates
        WHERE strategy = ?
        """,
        [STRATEGY_NAME],
    )
    if refined.empty or refined["snapshot_date"].nunique() < 2:
        return pd.DataFrame(
            columns=[
                "status",
                "bucket",
                "market",
                "symbol",
                "name",
                "latest_score",
                "previous_score",
                "score_delta",
            ]
        )

    dates = sorted(refined["snapshot_date"].dropna().unique())
    previous_date, latest_date = dates[-2], dates[-1]
    previous = refined[refined["snapshot_date"] == previous_date].copy()
    latest = refined[refined["snapshot_date"] == latest_date].copy()
    key_columns = ["bucket", "market", "symbol"]
    merged = latest.merge(
        previous[key_columns + ["expert_score"]].rename(columns={"expert_score": "previous_score"}),
        on=key_columns,
        how="outer",
        indicator=True,
        suffixes=("", "_previous"),
    )
    merged["status"] = merged["_merge"].map({"left_only": "new", "right_only": "removed", "both": "kept"})
    merged["latest_score"] = pd.to_numeric(merged.get("expert_score"), errors="coerce")
    merged["previous_score"] = pd.to_numeric(merged.get("previous_score"), errors="coerce")
    merged["score_delta"] = (merged["latest_score"] - merged["previous_score"]).round(1)
    merged["name"] = merged["name"].fillna(merged.get("name_previous"))
    return merged[
        [
            "status",
            "bucket",
            "market",
            "symbol",
            "name",
            "latest_score",
            "previous_score",
            "score_delta",
        ]
    ].sort_values(["status", "bucket", "latest_score"], ascending=[True, True, False])


def backtest_refined_candidates(
    initial_capital: float = 1_000_000,
    max_names: int = 12,
) -> pd.DataFrame:
    store = get_store()
    refined = store.query_df(
        """
        SELECT *
        FROM refined_candidates
        WHERE strategy = ?
        """,
        [STRATEGY_NAME],
    )
    prices = store.query_df("SELECT * FROM daily_prices")
    if refined.empty or prices.empty:
        return pd.DataFrame(
            columns=["period_start", "period_end", "holdings", "period_return", "equity"]
        )

    refined["snapshot_date"] = pd.to_datetime(refined["snapshot_date"])
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    dates = sorted(refined["snapshot_date"].dropna().unique())
    final_price_date = prices["trade_date"].max()
    if not dates or final_price_date <= dates[0]:
        return pd.DataFrame(
            columns=["period_start", "period_end", "holdings", "period_return", "equity"]
        )

    rows: list[dict[str, object]] = []
    equity = float(initial_capital)
    for index, start_date in enumerate(dates):
        end_date = dates[index + 1] if index + 1 < len(dates) else final_price_date
        if end_date <= start_date:
            continue
        picks = (
            refined[refined["snapshot_date"] == start_date]
            .sort_values(["expert_score", "fundamental_score", "technical_score"], ascending=False)
            .head(max_names)
        )
        returns: list[float] = []
        for _, pick in picks.iterrows():
            history = prices[
                (prices["market"] == pick["market"])
                & (prices["symbol"].astype(str) == str(pick["symbol"]))
                & (prices["trade_date"] >= start_date)
                & (prices["trade_date"] <= end_date)
            ].sort_values("trade_date")
            if len(history) < 2:
                continue
            start_close = pd.to_numeric(history["close"].iloc[0], errors="coerce")
            end_close = pd.to_numeric(history["close"].iloc[-1], errors="coerce")
            if pd.notna(start_close) and pd.notna(end_close) and float(start_close) > 0:
                returns.append(float(end_close) / float(start_close) - 1)
        if not returns:
            continue
        period_return = float(pd.Series(returns).mean())
        equity *= 1 + period_return
        rows.append(
            {
                "period_start": pd.Timestamp(start_date).date(),
                "period_end": pd.Timestamp(end_date).date(),
                "holdings": len(returns),
                "period_return": period_return,
                "equity": equity,
            }
        )
    return pd.DataFrame(rows)


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
