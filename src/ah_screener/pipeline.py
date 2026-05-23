from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd

from ah_screener.classification import enrich_security_metadata
from ah_screener.config import get_settings
from ah_screener.documents import build_document_records
from ah_screener.etf_model import consolidate_etf_candidates, enrich_etf_snapshot
from ah_screener.expert_model import (
    CURATED_THEME_OVERRIDES,
    STRATEGY_NAME,
    refine_candidates,
    run_expert_model,
)
from ah_screener.fundamentals import fetch_fundamentals
from ah_screener.identity import default_identity_mappings
from ah_screener.potential import (
    scan_potential_candidates,
    sweep_potential_thresholds,
    validate_potential_signals,
)
from ah_screener.selection import validate_etf_clusters
from ah_screener.reporting import generate_report
from ah_screener.sources.akshare_client import (
    DEFAULT_BENCHMARKS,
    fetch_a_board_tags,
    fetch_a_etf_spot,
    fetch_benchmark_history,
    fetch_history,
    fetch_hk_etf_spot,
    fetch_spot,
    parse_benchmark,
)
from ah_screener.sources.hkexnews_client import (
    download_hkex_announcements,
    fetch_hkex_announcements,
)
from ah_screener.sources.us_client import fetch_us_spot, fetch_us_spot_batch
from ah_screener.storage import Store
from ah_screener.technical import compute_technical_indicators
from ah_screener.universe import ETFS, STOCKS, AssetClass, select_assets
from ah_screener.backtest import (  # re-exported for backward compat
    backfill_refined_candidate_snapshots as backfill_refined_candidate_snapshots,
    backtest_refined_candidates as backtest_refined_candidates,
)


MarketArg = Literal["A", "HK", "US", "ETF", "all"]


def get_store() -> Store:
    return Store(get_settings().db_path)


def init_db() -> None:
    get_store().init_db()


def sync_spot(market: MarketArg) -> dict[str, int]:
    store = get_store()
    store.init_db()
    result: dict[str, int] = {}
    tolerant = market == "all"  # a single transient endpoint failure must not abort a full refresh

    def _ingest(label: str, fetch) -> None:
        try:
            securities, snapshots = fetch()
        except Exception as exc:  # noqa: BLE001 - record and continue across markets
            result[f"{label}_failed"] = 1
            result[f"{label}_error"] = str(exc)[:200]
            if not tolerant:
                raise
            return
        result[f"{label}_securities"] = store.upsert_dataframe("securities", securities)
        result[f"{label}_snapshots"] = store.upsert_dataframe("market_snapshots", snapshots)

    markets = ["A", "HK", "US"] if market == "all" else ([] if market == "ETF" else [market])
    for item in markets:
        _ingest(item, lambda item=item: fetch_spot(item))  # type: ignore[arg-type]
    if market in {"A", "ETF", "all"}:
        _ingest("A_etf", fetch_a_etf_spot)
    if market in {"HK", "ETF", "all"}:
        _ingest("HK_etf", fetch_hk_etf_spot)
    return result


def sync_us_spot(symbols: list[str], lookback_days: int = 14) -> dict[str, int]:
    store = get_store()
    store.init_db()
    securities, snapshots = fetch_us_spot(symbols=symbols, lookback_days=lookback_days)
    return {
        "US_securities": store.upsert_dataframe("securities", securities),
        "US_snapshots": store.upsert_dataframe("market_snapshots", snapshots),
    }


def sync_us_spot_batch(
    *,
    offset: int = 0,
    limit: int = 100,
    include_etf: bool = False,
    lookback_days: int = 14,
    asset_type: str | None = None,
) -> dict[str, int]:
    store = get_store()
    store.init_db()
    securities, snapshots = fetch_us_spot_batch(
        offset=offset,
        limit=limit,
        include_etf=include_etf,
        lookback_days=lookback_days,
        asset_type=asset_type,
    )
    return {
        "US_securities": store.upsert_dataframe("securities", securities),
        "US_snapshots": store.upsert_dataframe("market_snapshots", snapshots),
        "US_batch_offset": offset,
        "US_batch_limit": limit,
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


def import_industry_mapping(path: Path, source: str = "industry_mapping_csv") -> int:
    store = get_store()
    store.init_db()
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path)
    if "tag_name" not in frame.columns:
        for column in ["detailed_industry", "industry", "industry_peer_group", "sub_industry", "sector"]:
            if column in frame.columns:
                frame = frame.rename(columns={column: "tag_name"})
                break
    if "tag_type" not in frame.columns:
        frame["tag_type"] = "industry"
    if "evidence_level" not in frame.columns:
        frame["evidence_level"] = "A"
    return store.upsert_dataframe("company_tags", _prepare_custom_tags(frame, source=source))


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


def _latest_snapshots(
    store: Store, asset_classes: tuple[AssetClass, ...] = STOCKS
) -> pd.DataFrame:
    snapshots = store.query_df("SELECT * FROM market_snapshots")
    if snapshots.empty:
        return snapshots
    snapshots = select_assets(snapshots, asset_classes)
    snapshots = snapshots.copy()
    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"], errors="coerce")
    return snapshots.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")


def sync_history(
    market: MarketArg,
    top: int = 150,
    lookback_days: int = 420,
    include_etf: bool = True,
    etf_top: int = 120,
) -> dict[str, int]:
    store = get_store()
    store.init_db()
    asset_classes = (*STOCKS, *ETFS) if include_etf else STOCKS
    latest = _latest_snapshots(store, asset_classes=asset_classes)
    if latest.empty:
        raise RuntimeError("No market snapshots found. Run `ah-screener sync-spot --market all` first.")
    if "asset_type" not in latest.columns:
        latest = latest.assign(asset_type="stock")

    markets = ["A", "HK", "US"] if market == "all" else [market]
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    result: dict[str, int] = {}
    for item in markets:
        pool = latest[latest["market"] == item].assign(
            amount_num=lambda df: pd.to_numeric(df["amount"], errors="coerce").fillna(0),
            asset_type=lambda df: df["asset_type"].fillna("stock"),
        )
        # Take top stocks and top ETFs separately, so high-turnover ETFs do not
        # crowd stocks out of a single combined ranking.
        stocks = pool[pool["asset_type"].eq("stock")].sort_values("amount_num", ascending=False).head(top)
        etfs = (
            pool[pool["asset_type"].eq("etf")].sort_values("amount_num", ascending=False).head(etf_top)
            if include_etf
            else pool.iloc[0:0]
        )
        universe = pd.concat([stocks, etfs], ignore_index=True)
        inserted = 0
        failed = 0
        for row in universe.itertuples(index=False):
            try:
                history = fetch_history(
                    item,
                    symbol=str(row.symbol),
                    start_date=start,
                    end_date=end,
                    asset_type=str(getattr(row, "asset_type", "stock") or "stock"),
                )
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
    snapshots = select_assets(store.query_df("SELECT * FROM market_snapshots"), STOCKS)
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


def sync_hkex_documents(
    *,
    symbol: str,
    output_dir: Path = Path("data/hkex_documents"),
    from_date: str | None = None,
    to_date: str | None = None,
    keywords: list[str] | None = None,
    limit: int = 10,
    lang: str = "EN",
) -> dict[str, int]:
    announcements = fetch_hkex_announcements(
        symbol=symbol,
        from_date=from_date,
        to_date=to_date,
        keywords=keywords,
        limit=limit,
        lang=lang,
    )
    downloads = download_hkex_announcements(announcements, output_dir=output_dir)
    result = {
        "hkex_announcements": len(announcements),
        "hkex_downloaded": len(downloads),
        "company_documents": 0,
        "document_extractions": 0,
        "company_tags": 0,
        "document_ingest_failed": 0,
    }
    for _, row in downloads.iterrows():
        release_datetime = pd.to_datetime(row.get("release_datetime"), errors="coerce")
        try:
            ingest_result = ingest_company_document(
                market="HK",
                symbol=symbol,
                path=Path(str(row["local_path"])),
                document_type=str(row.get("document_type") or "announcement"),
                report_date=str(release_datetime.date()) if pd.notna(release_datetime) else None,
                title=str(row.get("title") or ""),
                source_url=str(row.get("url") or ""),
                source="hkexnews_auto",
            )
        except Exception:
            result["document_ingest_failed"] += 1
            continue
        for key in ["company_documents", "document_extractions", "company_tags"]:
            result[key] += ingest_result.get(key, 0)
    return result


def run_potential_validation() -> pd.DataFrame:
    store = get_store()
    prices = store.query_df("SELECT * FROM daily_prices")
    return validate_potential_signals(prices)


def run_potential_threshold_sweep() -> pd.DataFrame:
    store = get_store()
    prices = store.query_df("SELECT * FROM daily_prices")
    return sweep_potential_thresholds(prices)


def run_potential_scan(top: int = 80) -> dict[str, int]:
    store = get_store()
    store.init_db()
    prices = store.query_df("SELECT * FROM daily_prices")
    snapshots = _latest_table(store, "market_snapshots", "trade_date")
    validation = validate_potential_signals(prices)
    candidates = scan_potential_candidates(prices, snapshots, validation=validation, top=top)
    # Replace prior rows for this strategy so a stricter scan doesn't leave stale picks.
    store.execute("DELETE FROM potential_candidates WHERE strategy = ?", ["potential_v1"])
    if not candidates.empty:
        candidates["updated_at"] = pd.Timestamp(datetime.now())
    return {"potential_candidates": store.upsert_dataframe("potential_candidates", candidates)}


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


def validate_etf_cluster_table(min_corr: float = 0.9) -> pd.DataFrame:
    store = get_store()
    snapshots = _latest_table(store, "market_snapshots", "trade_date")
    if snapshots.empty:
        return pd.DataFrame()
    pool = select_assets(snapshots, ETFS)
    prices = store.query_df("SELECT market, symbol, trade_date, close FROM daily_prices")
    return validate_etf_clusters(pool, prices, min_corr=min_corr)


def export_etf_candidates(
    top: int = 100,
    category: str | None = None,
    grouped: bool = True,
    market: str | None = None,
) -> pd.DataFrame:
    store = get_store()
    snapshots = _latest_table(store, "market_snapshots", "trade_date")
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
    result["history"] = sync_history("all", top=top, lookback_days=lookback_days)
    result["benchmarks"] = sync_benchmarks(lookback_days=lookback_days)
    result["technical_rows"] = run_technical_indicators()
    if include_fundamentals:
        result["fundamentals"] = sync_fundamentals("all", top=top)
    result["expert_scores"] = run_expert_scores()
    result["industry_valuation_stats"] = compute_industry_valuation_stats()
    result["potential_scan"] = run_potential_scan()
    if include_report:
        result["report"] = str(generate_report())
    return result
