from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd

from ah_screener.classification import enrich_security_metadata
from ah_screener.config import get_settings
from ah_screener.documents import build_document_records
from ah_screener.etf_model import enrich_etf_snapshot
from ah_screener.expert_model import (
    CURATED_THEME_OVERRIDES,
    STRATEGY_NAME,
    refine_candidates,
    run_expert_model,
)
from ah_screener.fundamentals import fetch_fundamentals
from ah_screener.identity import default_identity_mappings
from ah_screener.reporting import generate_report
from ah_screener.scoring import score_snapshot
from ah_screener.sources.akshare_client import (
    DEFAULT_BENCHMARKS,
    fetch_a_board_tags,
    fetch_a_etf_spot,
    fetch_benchmark_history,
    fetch_history,
    fetch_spot,
    parse_benchmark,
)
from ah_screener.sources.us_client import fetch_us_spot
from ah_screener.storage import Store
from ah_screener.technical import compute_technical_indicators


MarketArg = Literal["A", "HK", "US", "ETF", "all"]
RebalanceMode = Literal["snapshot", "monthly", "quarterly"]
BACKTEST_COLUMNS = [
    "period_start",
    "period_end",
    "signal_date",
    "holdings",
    "gross_return",
    "turnover",
    "cost_rate",
    "period_return",
    "equity",
    "benchmark",
    "benchmark_return",
    "benchmark_equity",
    "excess_return",
    "excess_equity",
    "holding_symbols",
]


def get_store() -> Store:
    return Store(get_settings().db_path)


def init_db() -> None:
    get_store().init_db()


def sync_spot(market: MarketArg) -> dict[str, int]:
    store = get_store()
    store.init_db()
    result: dict[str, int] = {}
    markets = ["A", "HK", "US"] if market == "all" else ([] if market == "ETF" else [market])
    for item in markets:
        securities, snapshots = fetch_spot(item)  # type: ignore[arg-type]
        result[f"{item}_securities"] = store.upsert_dataframe("securities", securities)
        result[f"{item}_snapshots"] = store.upsert_dataframe("market_snapshots", snapshots)
    if market in {"A", "ETF", "all"}:
        etf_securities, etf_snapshots = fetch_a_etf_spot()
        result["A_etf_securities"] = store.upsert_dataframe("securities", etf_securities)
        result["A_etf_snapshots"] = store.upsert_dataframe("market_snapshots", etf_snapshots)
    return result


def sync_us_spot(symbols: list[str], lookback_days: int = 14) -> dict[str, int]:
    store = get_store()
    store.init_db()
    securities, snapshots = fetch_us_spot(symbols=symbols, lookback_days=lookback_days)
    return {
        "US_securities": store.upsert_dataframe("securities", securities),
        "US_snapshots": store.upsert_dataframe("market_snapshots", snapshots),
    }


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


def _normalize_tag_symbol(market: str, symbol: object) -> str:
    raw = str(symbol).strip().lower()
    clean = raw.replace("sh", "").replace("sz", "").replace("bj", "").replace("hk", "")
    if market == "US":
        return str(symbol).strip().upper().replace("/", ".")
    return clean.zfill(5 if market == "HK" else 6)


def _prepare_custom_tags(tags: pd.DataFrame, source: str) -> pd.DataFrame:
    required = {"market", "symbol", "tag_name"}
    missing = required.difference(tags.columns)
    if missing:
        raise ValueError(f"Missing custom tag columns: {', '.join(sorted(missing))}")

    frame = tags.copy()
    frame["market"] = frame["market"].astype(str).str.upper().str.strip()
    frame = frame[frame["market"].isin(["A", "HK", "US"])]
    frame["symbol"] = frame.apply(lambda row: _normalize_tag_symbol(str(row["market"]), row["symbol"]), axis=1)
    frame["tag_name"] = frame["tag_name"].astype(str).str.strip()
    frame["tag_type"] = (
        frame["tag_type"].astype(str).str.lower().str.strip() if "tag_type" in frame.columns else "theme"
    )
    frame["evidence_level"] = (
        frame["evidence_level"].astype(str).str.upper().str.strip() if "evidence_level" in frame.columns else "B"
    )
    frame["source"] = frame["source"].astype(str).str.strip() if "source" in frame.columns else source
    frame["source"] = frame["source"].replace("", source)
    frame["updated_at"] = pd.Timestamp(datetime.now())
    frame = frame[frame["tag_name"].ne("")]
    return frame[
        ["market", "symbol", "tag_type", "tag_name", "evidence_level", "source", "updated_at"]
    ].drop_duplicates(["market", "symbol", "tag_type", "tag_name", "source"])


def import_custom_tags(path: Path, source: str = "custom_csv") -> int:
    store = get_store()
    store.init_db()
    if not path.exists():
        raise FileNotFoundError(path)
    tags = pd.read_csv(path)
    return store.upsert_dataframe("company_tags", _prepare_custom_tags(tags, source=source))


def sync_curated_theme_tags() -> int:
    rows = [
        {
            "market": market,
            "symbol": symbol,
            "tag_type": "theme",
            "tag_name": theme,
            "evidence_level": "B",
            "source": "curated_theme_overrides",
        }
        for (market, symbol), themes in CURATED_THEME_OVERRIDES.items()
        for theme in themes
    ]
    tags = _prepare_custom_tags(pd.DataFrame(rows), source="curated_theme_overrides")
    store = get_store()
    store.init_db()
    return store.upsert_dataframe("company_tags", tags)


def sync_identity_mappings() -> int:
    store = get_store()
    store.init_db()
    return store.upsert_dataframe("company_identity_mappings", default_identity_mappings())


def _identity_mapping_frame(store: Store) -> pd.DataFrame:
    mappings = store.query_df("SELECT market, symbol, canonical_id FROM company_identity_mappings")
    if mappings.empty:
        mappings = default_identity_mappings()[["market", "symbol", "canonical_id"]]
    return mappings.drop_duplicates(["market", "symbol"], keep="first")


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
    snapshots = snapshots.copy()
    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"], errors="coerce")
    return snapshots.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")


def sync_history(market: MarketArg, top: int = 150, lookback_days: int = 420) -> dict[str, int]:
    store = get_store()
    store.init_db()
    latest = _latest_snapshots(store)
    if latest.empty:
        raise RuntimeError("No market snapshots found. Run `ah-screener sync-spot --market all` first.")

    markets = ["A", "HK", "US"] if market == "all" else [market]
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


def sync_benchmarks(
    benchmarks: list[str] | None = None,
    lookback_days: int = 430,
) -> dict[str, int]:
    store = get_store()
    store.init_db()
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    result: dict[str, int] = {}
    for benchmark in benchmarks or DEFAULT_BENCHMARKS:
        market, symbol = parse_benchmark(benchmark)
        key = f"{market}_{symbol}_benchmark_rows"
        try:
            history = fetch_benchmark_history(f"{market}:{symbol}", start_date=start, end_date=end)
        except Exception:
            result[key] = 0
            result[f"{market}_{symbol}_benchmark_failed"] = 1
            continue
        result[key] = store.upsert_dataframe("daily_prices", history)
        result[f"{market}_{symbol}_benchmark_failed"] = 0
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
    if not results.empty:
        mappings = _identity_mapping_frame(store)
        results = results.drop(columns=["canonical_id"], errors="ignore").merge(
            mappings,
            on=["market", "symbol"],
            how="left",
        )
    refined = refine_candidates(results, max_per_bucket=3)
    if not results.empty:
        latest_date = pd.Timestamp(results["snapshot_date"].max()).date()
        for strategy in results["strategy"].dropna().unique():
            store.execute(
                "DELETE FROM expert_screening_results WHERE snapshot_date = ? AND strategy = ?",
                [latest_date, strategy],
            )
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

    markets = ["A", "HK", "US"] if market == "all" else [market]
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
    markets = pd.DataFrame({"market": ["A", "HK", "US"]})
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
    snapshots = snapshots.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
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


def compute_industry_valuation_stats() -> int:
    store = get_store()
    store.init_db()
    expert = _latest_table(store, "expert_screening_results", "snapshot_date")
    snapshots = _latest_table(store, "market_snapshots", "trade_date")
    if expert.empty or snapshots.empty:
        return 0
    if "strategy" in expert.columns:
        expert = expert[expert["strategy"] == STRATEGY_NAME]
    merged = expert.merge(
        snapshots[
            [
                "market",
                "symbol",
                "pe_ttm",
                "pb",
            ]
        ].drop_duplicates(["market", "symbol"], keep="last"),
        on=["market", "symbol"],
        how="left",
    )
    if "detailed_industry" not in merged.columns:
        merged["detailed_industry"] = merged.get("industry_peer_group", "未分类")
    merged["detailed_industry"] = merged["detailed_industry"].fillna("未分类").astype(str)
    merged["valuation_percentile"] = pd.to_numeric(
        merged.get("valuation_percentile", pd.Series(pd.NA, index=merged.index)),
        errors="coerce",
    )
    stats = (
        merged.groupby(["snapshot_date", "market", "detailed_industry"], dropna=False)
        .agg(
            securities=("symbol", "nunique"),
            pe_median=("pe_ttm", "median"),
            pb_median=("pb", "median"),
            valuation_percentile_median=("valuation_percentile", "median"),
            valuation_percentile_top_quartile=("valuation_percentile", lambda value: value.quantile(0.75)),
        )
        .reset_index()
    )
    stats["source"] = "expert_screening_results.market_snapshots"
    stats["updated_at"] = pd.Timestamp(datetime.now())
    return store.upsert_dataframe("industry_valuation_stats", stats)


def ingest_company_document(
    *,
    market: str,
    symbol: str,
    path: Path,
    document_type: str = "annual_report",
    report_date: str | None = None,
    title: str | None = None,
    source_url: str | None = None,
    source: str = "official_pdf",
) -> dict[str, int]:
    store = get_store()
    store.init_db()
    document, extractions, tags = build_document_records(
        market=market,
        symbol=symbol,
        path=path,
        document_type=document_type,
        report_date=report_date,
        title=title,
        source_url=source_url,
        source=source,
    )
    return {
        "company_documents": store.upsert_dataframe("company_documents", document),
        "document_extractions": store.upsert_dataframe("document_extractions", extractions),
        "company_tags": store.upsert_dataframe("company_tags", tags),
    }


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


def _empty_backtest_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BACKTEST_COLUMNS)


def _rebalance_points(
    signal_dates: list[pd.Timestamp],
    final_price_date: pd.Timestamp,
    mode: RebalanceMode,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if not signal_dates:
        return []
    if mode == "snapshot":
        starts = signal_dates
    else:
        frequency = "MS" if mode == "monthly" else "QS"
        calendar_starts = [
            pd.Timestamp(value)
            for value in pd.date_range(signal_dates[0], final_price_date, freq=frequency)
            if pd.Timestamp(value) > signal_dates[0]
        ]
        starts = sorted(set([signal_dates[0], *calendar_starts]))

    points: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for start in starts:
        eligible = [date for date in signal_dates if date <= start]
        if eligible:
            points.append((start, eligible[-1]))
    return points


def _select_backtest_picks(
    picks: pd.DataFrame,
    max_names: int,
    industry_neutral: bool,
    max_per_group: int,
) -> pd.DataFrame:
    if picks.empty:
        return picks
    picks = picks.copy()
    for column, default in [
        ("expert_score", 0.0),
        ("peer_score", 50.0),
        ("industry_fit_score", 50.0),
        ("fundamental_score", 50.0),
        ("technical_score", 50.0),
        ("industry_peer_group", "未分类"),
    ]:
        if column not in picks.columns:
            picks[column] = default
    picks = picks.sort_values(
        ["expert_score", "industry_fit_score", "peer_score", "fundamental_score", "technical_score"],
        ascending=False,
    )
    if not industry_neutral:
        return picks.head(max_names)

    selected: list[int] = []
    group_counts: dict[str, int] = {}
    for idx, row in picks.iterrows():
        group = str(row.get("industry_peer_group") or row.get("bucket") or "未分类")
        if group_counts.get(group, 0) >= max_per_group:
            continue
        selected.append(idx)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(selected) >= max_names:
            break
    return picks.loc[selected]


def _benchmark_frame(prices: pd.DataFrame, benchmark: str) -> pd.DataFrame:
    market, symbol = parse_benchmark(benchmark)
    frame = prices[
        (prices["market"].astype(str).str.upper() == market) & (prices["symbol"].astype(str) == symbol)
    ].copy()
    if frame.empty:
        return frame
    adj = frame.get("adj_type", pd.Series("", index=frame.index)).astype(str).str.lower()
    source = frame.get("source", pd.Series("", index=frame.index)).astype(str).str.lower()
    benchmark_rows = frame[adj.eq("benchmark") | source.str.contains("index", na=False)]
    return benchmark_rows if not benchmark_rows.empty else frame


def _period_price_return(
    prices: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> float | None:
    history = prices[
        (prices["trade_date"] >= start_date) & (prices["trade_date"] <= end_date)
    ].sort_values("trade_date")
    if len(history) < 2:
        return None
    start_close = pd.to_numeric(history["close"].iloc[0], errors="coerce")
    end_close = pd.to_numeric(history["close"].iloc[-1], errors="coerce")
    if pd.notna(start_close) and pd.notna(end_close) and float(start_close) > 0:
        return float(end_close) / float(start_close) - 1
    return None


def _historical_signal_dates(
    prices: pd.DataFrame,
    rebalance: RebalanceMode,
    min_snapshots: int,
) -> list[pd.Timestamp]:
    dates = pd.to_datetime(prices["trade_date"], errors="coerce").dropna().sort_values().unique()
    if len(dates) < 2:
        return []
    start = pd.Timestamp(dates[0])
    end = pd.Timestamp(dates[-1])
    if rebalance == "snapshot":
        candidates = [pd.Timestamp(date) for date in dates[:: max(len(dates) // max(min_snapshots, 1), 1)]]
    else:
        frequency = "MS" if rebalance == "monthly" else "QS"
        candidates = [pd.Timestamp(value) for value in pd.date_range(start, end, freq=frequency)]
    trading_dates = [pd.Timestamp(date) for date in dates]
    selected: list[pd.Timestamp] = []
    for candidate in candidates:
        eligible = [date for date in trading_dates if date <= candidate]
        if eligible:
            selected.append(eligible[-1])
    selected = sorted(set(selected))
    if selected and selected[-1] == end:
        selected = selected[:-1]
    return selected[-min_snapshots:]


def _trailing_return_score(
    prices: pd.DataFrame,
    market: str,
    symbol: str,
    signal_date: pd.Timestamp,
    lookback_days: int = 90,
) -> float:
    start_date = signal_date - pd.Timedelta(days=lookback_days)
    history = prices[
        (prices["market"].astype(str) == market)
        & (prices["symbol"].astype(str) == symbol)
        & (prices["trade_date"] >= start_date)
        & (prices["trade_date"] <= signal_date)
    ].sort_values("trade_date")
    if len(history) < 2:
        return 50.0
    start_close = pd.to_numeric(history["close"].iloc[0], errors="coerce")
    end_close = pd.to_numeric(history["close"].iloc[-1], errors="coerce")
    if pd.isna(start_close) or pd.isna(end_close) or float(start_close) <= 0:
        return 50.0
    trailing_return = float(end_close) / float(start_close) - 1
    return float(max(0, min(100, 50 + trailing_return * 180)))


def _trailing_return_scores(
    prices: pd.DataFrame,
    signal_date: pd.Timestamp,
    lookback_days: int = 90,
) -> dict[tuple[str, str], float]:
    start_date = signal_date - pd.Timedelta(days=lookback_days)
    window = prices[
        (prices["trade_date"] >= start_date)
        & (prices["trade_date"] <= signal_date)
    ].sort_values(["market", "symbol", "trade_date"])
    if window.empty:
        return {}
    scores: dict[tuple[str, str], float] = {}
    for (market, symbol), group in window.groupby(["market", "symbol"], sort=False):
        if len(group) < 2:
            continue
        start_close = pd.to_numeric(group["close"].iloc[0], errors="coerce")
        end_close = pd.to_numeric(group["close"].iloc[-1], errors="coerce")
        if pd.isna(start_close) or pd.isna(end_close) or float(start_close) <= 0:
            continue
        trailing_return = float(end_close) / float(start_close) - 1
        scores[(str(market), str(symbol))] = float(max(0, min(100, 50 + trailing_return * 180)))
    return scores


def backfill_refined_candidate_snapshots(
    min_snapshots: int = 6,
    rebalance: RebalanceMode = "quarterly",
    max_per_bucket: int = 3,
    max_per_style: int = 2,
) -> int:
    store = get_store()
    store.init_db()
    existing = store.query_df(
        """
        SELECT DISTINCT snapshot_date
        FROM refined_candidates
        WHERE strategy = ?
        ORDER BY snapshot_date
        """,
        [STRATEGY_NAME],
    )
    existing_dates = (
        set(pd.to_datetime(existing["snapshot_date"]).dt.normalize())
        if not existing.empty
        else set()
    )
    if len(existing_dates) >= min_snapshots:
        return 0

    expert = store.query_df(
        """
        SELECT *
        FROM expert_screening_results
        WHERE strategy = ?
        """,
        [STRATEGY_NAME],
    )
    prices = store.query_df("SELECT * FROM daily_prices")
    if expert.empty or prices.empty:
        return 0
    expert["snapshot_date"] = pd.to_datetime(expert["snapshot_date"])
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["symbol"] = prices["symbol"].astype(str)
    latest_signal = expert["snapshot_date"].max()
    template = expert[expert["snapshot_date"] == latest_signal].copy()
    if template.empty:
        return 0

    target_count = max(min_snapshots - len(existing_dates), 0)
    signal_dates = _historical_signal_dates(prices, rebalance, min_snapshots + 2)
    signal_dates = [
        date.normalize()
        for date in signal_dates
        if date.normalize() not in existing_dates and date.normalize() < latest_signal.normalize()
    ][-target_count:]
    inserted = 0
    for signal_date in signal_dates:
        replay = template.copy()
        replay["snapshot_date"] = signal_date
        trailing_scores = _trailing_return_scores(prices, signal_date)
        replay["technical_score"] = replay.apply(
            lambda row: trailing_scores.get((str(row["market"]), str(row["symbol"])), 50.0),
            axis=1,
        )
        replay["expert_score"] = (
            pd.to_numeric(replay["expert_score"], errors="coerce").fillna(50) * 0.78
            + pd.to_numeric(replay["technical_score"], errors="coerce").fillna(50) * 0.16
            + pd.to_numeric(
                replay.get("liquidity_score", pd.Series(50, index=replay.index)),
                errors="coerce",
            ).fillna(50)
            * 0.06
        ).clip(0, 100)
        replay["reasons"] = replay["reasons"].astype(str) + f"; historical_replay_signal={signal_date.date()}"
        refined = refine_candidates(
            replay,
            max_per_bucket=max_per_bucket,
            max_per_style=max_per_style,
        )
        if refined.empty:
            continue
        store.execute(
            "DELETE FROM refined_candidates WHERE snapshot_date = ? AND strategy = ?",
            [pd.Timestamp(signal_date).date(), STRATEGY_NAME],
        )
        inserted += store.upsert_dataframe("refined_candidates", refined)
    return inserted


def backtest_refined_candidates(
    initial_capital: float = 1_000_000,
    max_names: int = 12,
    rebalance: RebalanceMode = "snapshot",
    fee_bps: float = 5.0,
    slippage_bps: float = 10.0,
    industry_neutral: bool = False,
    max_per_group: int = 2,
    benchmark: str | None = None,
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
        return _empty_backtest_frame()

    refined["snapshot_date"] = pd.to_datetime(refined["snapshot_date"])
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["symbol"] = prices["symbol"].astype(str)
    dates = sorted(refined["snapshot_date"].dropna().unique())
    final_price_date = prices["trade_date"].max()
    if not dates or final_price_date <= dates[0]:
        return _empty_backtest_frame()

    rows: list[dict[str, object]] = []
    equity = float(initial_capital)
    benchmark_equity = float(initial_capital)
    previous_weights: dict[tuple[str, str], float] = {}
    cost_bps = max(fee_bps, 0) + max(slippage_bps, 0)
    points = _rebalance_points([pd.Timestamp(date) for date in dates], final_price_date, rebalance)
    benchmark_prices = _benchmark_frame(prices, benchmark) if benchmark else pd.DataFrame()
    for index, (start_date, signal_date) in enumerate(points):
        end_date = points[index + 1][0] if index + 1 < len(points) else final_price_date
        if end_date <= start_date:
            continue
        picks = _select_backtest_picks(
            refined[refined["snapshot_date"] == signal_date],
            max_names=max_names,
            industry_neutral=industry_neutral,
            max_per_group=max_per_group,
        )
        holding_returns: dict[tuple[str, str], float] = {}
        for _, pick in picks.iterrows():
            key = (str(pick["market"]), str(pick["symbol"]))
            history = prices[
                (prices["market"] == key[0])
                & (prices["symbol"] == key[1])
                & (prices["trade_date"] >= start_date)
                & (prices["trade_date"] <= end_date)
            ].sort_values("trade_date")
            if len(history) < 2:
                continue
            start_close = pd.to_numeric(history["close"].iloc[0], errors="coerce")
            end_close = pd.to_numeric(history["close"].iloc[-1], errors="coerce")
            if pd.notna(start_close) and pd.notna(end_close) and float(start_close) > 0:
                holding_returns[key] = float(end_close) / float(start_close) - 1
        if not holding_returns:
            continue
        current_weights = {key: 1 / len(holding_returns) for key in holding_returns}
        traded_notional = sum(
            abs(current_weights.get(key, 0.0) - previous_weights.get(key, 0.0))
            for key in set(current_weights) | set(previous_weights)
        )
        gross_return = float(
            sum(current_weights[key] * holding_returns[key] for key in holding_returns)
        )
        cost_rate = traded_notional * cost_bps / 10_000
        period_return = gross_return - cost_rate
        equity *= 1 + period_return
        benchmark_return = None
        if benchmark and not benchmark_prices.empty:
            benchmark_return = _period_price_return(benchmark_prices, start_date, end_date)
            if benchmark_return is not None:
                benchmark_equity *= 1 + benchmark_return
        benchmark_equity_value = benchmark_equity if benchmark_return is not None else None
        excess_return = period_return - benchmark_return if benchmark_return is not None else None
        excess_equity = equity - benchmark_equity if benchmark_return is not None else None
        rows.append(
            {
                "period_start": pd.Timestamp(start_date).date(),
                "period_end": pd.Timestamp(end_date).date(),
                "signal_date": pd.Timestamp(signal_date).date(),
                "holdings": len(holding_returns),
                "gross_return": gross_return,
                "turnover": traded_notional,
                "cost_rate": cost_rate,
                "period_return": period_return,
                "equity": equity,
                "benchmark": benchmark,
                "benchmark_return": benchmark_return,
                "benchmark_equity": benchmark_equity_value,
                "excess_return": excess_return,
                "excess_equity": excess_equity,
                "holding_symbols": ",".join(f"{market}:{symbol}" for market, symbol in current_weights),
            }
        )
        previous_weights = current_weights
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
    result["curated_theme_tags"] = sync_curated_theme_tags()
    result["identity_mappings"] = sync_identity_mappings()
    result["basic_scores"] = run_scores()
    result["history"] = sync_history("all", top=top, lookback_days=lookback_days)
    result["benchmarks"] = sync_benchmarks(lookback_days=lookback_days)
    result["technical_rows"] = run_technical_indicators()
    if include_fundamentals:
        result["fundamentals"] = sync_fundamentals("all", top=top)
    result["expert_scores"] = run_expert_scores()
    result["industry_valuation_stats"] = compute_industry_valuation_stats()
    if include_report:
        result["report"] = str(generate_report())
    return result
