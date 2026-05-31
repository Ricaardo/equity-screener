from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd

from ah_screener.classification import enrich_security_metadata
from ah_screener.config import get_settings
from ah_screener.db import get_store, init_db as init_db, latest_table as _latest_table
from ah_screener.documents import build_document_records
from ah_screener.expert_model import (
    CURATED_THEME_OVERRIDES,
    STRATEGY_NAME,
    refine_candidates,
    run_expert_model,
)
from ah_screener.expert_validation import validate_expert_decisions
from ah_screener.fundamentals import fetch_fundamentals
from ah_screener.identity import default_identity_mappings, derive_fuzzy_identity_mappings
from ah_screener.potential import (
    scan_potential_candidates,
    sweep_potential_thresholds,
    validate_potential_signals,
    walk_forward_potential_thresholds,
)
from ah_screener.selection import validate_etf_clusters
from ah_screener.reporting import generate_report
from ah_screener.sources.akshare_client import (
    DEFAULT_BENCHMARKS,
    fetch_a_board_tags,
    fetch_a_delisted_lifecycle,
    fetch_a_etf_spot,
    fetch_benchmark_history,
    fetch_history,
    fetch_hk_delisted_lifecycle,
    fetch_hk_etf_spot,
    fetch_spot,
    parse_benchmark,
)
from ah_screener.sources.hkexnews_client import (
    download_hkex_announcements,
    fetch_hkex_announcements,
)
from ah_screener.sources.us_client import (
    fetch_us_delisted_lifecycle,
    fetch_us_spot,
    fetch_us_spot_batch,
)
from ah_screener.storage import Store
from ah_screener.technical import compute_technical_indicators
from ah_screener.universe import ETFS, STOCKS, AssetClass, select_assets
from ah_screener.backtest import (  # re-exported for backward compat
    backfill_refined_candidate_snapshots as backfill_refined_candidate_snapshots,
    backtest_refined_candidates as backtest_refined_candidates,
)

# Read-only view layer lives in exports.py; re-exported here so cli.py and tests can
# keep importing these names from ah_screener.pipeline.
from ah_screener.exports import (
    candidate_changes as candidate_changes,
    coverage_status as coverage_status,
    export_etf_candidates as export_etf_candidates,
    export_expert_scores as export_expert_scores,
    export_potential_candidates as export_potential_candidates,
    export_refined_candidates as export_refined_candidates,
    fundamentals_status as fundamentals_status,
    ingest_failure_status as ingest_failure_status,
)


MarketArg = Literal["A", "HK", "US", "ETF", "all"]

logger = logging.getLogger("ah_screener.pipeline")


def sync_delisted_universe() -> dict[str, int | str]:
    store = get_store()
    store.init_db()
    result: dict[str, int | str] = {}
    frames: list[pd.DataFrame] = []
    sources = [
        ("A", fetch_a_delisted_lifecycle),
        ("HK", fetch_hk_delisted_lifecycle),
        ("US", fetch_us_delisted_lifecycle),
    ]
    for market, fetch in sources:
        try:
            lifecycle = fetch()
        except Exception as exc:  # noqa: BLE001 - lifecycle data is bias metadata, not a refresh blocker
            result[f"{market}_lifecycle_failed"] = 1
            result[f"{market}_lifecycle_error"] = str(exc)[:200]
            continue
        result[f"{market}_lifecycle_rows"] = len(lifecycle)
        if not lifecycle.empty:
            frames.append(lifecycle)
    lifecycle = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    result["security_lifecycle_events"] = store.upsert_dataframe(
        "security_lifecycle_events", lifecycle
    )
    return result


def _persist_security_universe_snapshot(
    store: Store, securities: pd.DataFrame, snapshots: pd.DataFrame, source: str
) -> int:
    if snapshots.empty:
        return 0
    base = snapshots.copy()
    for column, default in [("name", pd.NA), ("asset_type", "stock"), ("board", pd.NA)]:
        if column not in base.columns:
            base[column] = default
    base["snapshot_date"] = pd.Timestamp(datetime.now()).date()
    base = base[["snapshot_date", "market", "symbol", "name", "asset_type", "board"]]

    meta_columns = [
        "market",
        "symbol",
        "name",
        "asset_type",
        "board",
        "exchange",
        "currency",
        "status",
        "is_st",
        "is_hk_connect",
        "metadata_source",
        "metadata_confidence",
    ]
    meta = securities.copy()
    for column in meta_columns:
        if column not in meta.columns:
            meta[column] = pd.NA
    meta = meta[meta_columns].drop_duplicates(["market", "symbol"], keep="last")

    out = base.merge(meta, on=["market", "symbol"], how="left", suffixes=("_snapshot", ""))
    for column in ["name", "asset_type", "board"]:
        out[column] = out[column].combine_first(out[f"{column}_snapshot"])
    out["asset_type"] = out["asset_type"].fillna("stock")
    out["is_st"] = out["is_st"].map(lambda value: False if pd.isna(value) else bool(value))
    out["is_hk_connect"] = out["is_hk_connect"].map(
        lambda value: False if pd.isna(value) else bool(value)
    )
    out["source"] = source
    out["updated_at"] = pd.Timestamp(datetime.now())
    columns = [
        "snapshot_date",
        "market",
        "symbol",
        "name",
        "asset_type",
        "board",
        "exchange",
        "currency",
        "status",
        "is_st",
        "is_hk_connect",
        "metadata_source",
        "metadata_confidence",
        "source",
        "updated_at",
    ]
    return store.upsert_dataframe(
        "security_universe_snapshots",
        out[columns].drop_duplicates(["snapshot_date", "market", "symbol"], keep="last"),
    )


def sync_spot(market: MarketArg) -> dict[str, int]:
    store = get_store()
    store.init_db()
    result: dict[str, int] = {}
    tolerant = market == "all"  # a single transient endpoint failure must not abort a full refresh

    def _ingest(label: str, fetch) -> None:
        # Wrap fetch AND upsert: a constraint/IO error on one market must not abort the rest.
        try:
            securities, snapshots = fetch()
            result[f"{label}_securities"] = store.upsert_dataframe("securities", securities)
            result[f"{label}_snapshots"] = store.upsert_dataframe("market_snapshots", snapshots)
            result[f"{label}_universe_snapshots"] = _persist_security_universe_snapshot(
                store, securities, snapshots, source=label
            )
        except Exception as exc:  # noqa: BLE001 - record and continue across markets
            result[f"{label}_failed"] = 1
            result[f"{label}_error"] = str(exc)[:200]
            if not tolerant:
                raise

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
        "US_universe_snapshots": _persist_security_universe_snapshot(
            store, securities, snapshots, source="US"
        ),
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
        "US_universe_snapshots": _persist_security_universe_snapshot(
            store, securities, snapshots, source="US_batch"
        ),
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
    snapshot_count = int(
        store.query_df("SELECT COUNT(*) AS count FROM market_snapshots")["count"].iloc[0]
    )
    return {"securities": security_rows, "snapshots": snapshot_count}


def sync_a_tags(
    kind: Literal["industry", "concept"],
    limit: int | None,
    force: bool = False,
    max_age_days: int = 7,
) -> int:
    """Sync A-share board membership; skip if already refreshed within max_age_days.

    Board membership changes slowly, so the daily refresh skips re-fetching unless
    stale (or ``force``). Returns 0 when skipped.
    """
    store = get_store()
    store.init_db()
    if not force:
        existing = store.query_df(
            "SELECT MAX(updated_at) AS last FROM company_tags WHERE tag_type = ?", [kind]
        )
        last = (
            pd.to_datetime(existing["last"].iloc[0], errors="coerce")
            if not existing.empty
            else pd.NaT
        )
        if pd.notna(last) and last >= pd.Timestamp.now() - pd.Timedelta(days=max_age_days):
            return 0
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
    frame["symbol"] = frame.apply(
        lambda row: _normalize_tag_symbol(str(row["market"]), row["symbol"]), axis=1
    )
    frame["tag_name"] = frame["tag_name"].astype(str).str.strip()
    frame["tag_type"] = (
        frame["tag_type"].astype(str).str.lower().str.strip()
        if "tag_type" in frame.columns
        else "theme"
    )
    frame["evidence_level"] = (
        frame["evidence_level"].astype(str).str.upper().str.strip()
        if "evidence_level" in frame.columns
        else "B"
    )
    frame["source"] = (
        frame["source"].astype(str).str.strip() if "source" in frame.columns else source
    )
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
        for column in [
            "detailed_industry",
            "industry",
            "industry_peer_group",
            "sub_industry",
            "sector",
        ]:
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
    curated = default_identity_mappings()
    written = store.upsert_dataframe("company_identity_mappings", curated)
    # P2-7: augment curated links with fuzzy cross-market name matches (curated wins).
    securities = store.query_df("SELECT market, symbol, name FROM securities")
    fuzzy = derive_fuzzy_identity_mappings(securities, curated=curated)
    if not fuzzy.empty:
        written += store.upsert_dataframe("company_identity_mappings", fuzzy)
    return written


def _record_ingest_failure(step: str, message: str) -> None:
    """Persist an ingest-step failure so coverage erosion is observable (P2-5).

    Best-effort: recording a failure must never itself abort the refresh.
    """
    try:
        store = get_store()
        store.init_db()
        now = datetime.now()
        store.upsert_dataframe(
            "ingest_failures",
            pd.DataFrame(
                [
                    {
                        "run_date": now.date(),
                        "step": step,
                        "message": message,
                        "occurred_at": pd.Timestamp(now),
                    }
                ]
            ),
        )
    except Exception:  # noqa: BLE001 - observability must not break the pipeline
        logger.exception("failed to record ingest failure for step %s", step)


def run_expert_validation(forward_days: int = 40) -> tuple[pd.DataFrame, dict[str, object]]:
    """Validate expert decision buckets against forward excess returns (P2-4)."""
    store = get_store()
    store.init_db()
    expert = store.query_df(
        "SELECT snapshot_date, market, symbol, decision, expert_score "
        "FROM expert_screening_results WHERE strategy = ?",
        [STRATEGY_NAME],
    )
    prices = store.query_df("SELECT market, symbol, trade_date, close FROM daily_prices")
    return validate_expert_decisions(expert, prices, forward_days=forward_days)


def _identity_mapping_frame(store: Store) -> pd.DataFrame:
    mappings = store.query_df(
        "SELECT market, symbol, canonical_id, confidence FROM company_identity_mappings"
    )
    if mappings.empty:
        mappings = default_identity_mappings()[["market", "symbol", "canonical_id"]]
        mappings["confidence"] = "high"
    # Curated (non-fuzzy) links win when a symbol carries both a curated and a fuzzy row.
    mappings["_fuzzy"] = mappings["confidence"].astype(str).eq("fuzzy")
    mappings = (
        mappings.sort_values("_fuzzy")
        .drop_duplicates(["market", "symbol"], keep="first")
        .drop(columns=["_fuzzy", "confidence"])
    )
    return mappings


def _latest_snapshots(store: Store, asset_classes: tuple[AssetClass, ...] = STOCKS) -> pd.DataFrame:
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
    full: bool = False,
) -> dict[str, int]:
    """Sync daily prices incrementally.

    A name already covered through the latest snapshot date is skipped; a stale name
    is fetched only from its last stored bar (minus a small buffer for adjustments)
    forward, instead of re-pulling the full ``lookback_days`` window. ``full=True``
    forces a complete backfill (e.g. first run or to repair gaps).
    """
    store = get_store()
    store.init_db()
    asset_classes = (*STOCKS, *ETFS) if include_etf else STOCKS
    latest = _latest_snapshots(store, asset_classes=asset_classes)
    if latest.empty:
        raise RuntimeError(
            "No market snapshots found. Run `ah-screener sync-spot --market all` first."
        )
    if "asset_type" not in latest.columns:
        latest = latest.assign(asset_type="stock")

    coverage = store.query_df(
        "SELECT market, symbol, MAX(trade_date) AS max_td FROM daily_prices GROUP BY market, symbol"
    )
    cov_map: dict[tuple[str, str], pd.Timestamp] = {}
    if not coverage.empty:
        coverage["max_td"] = pd.to_datetime(coverage["max_td"], errors="coerce")
        cov_map = {
            (str(r.market), str(r.symbol)): r.max_td for r in coverage.itertuples(index=False)
        }

    markets = ["A", "HK", "US"] if market == "all" else [market]
    end_dt = datetime.now()
    end = end_dt.strftime("%Y%m%d")
    full_start = (end_dt - timedelta(days=lookback_days)).strftime("%Y%m%d")
    result: dict[str, int] = {}
    for item in markets:
        pool = latest[latest["market"] == item].assign(
            amount_num=lambda df: pd.to_numeric(df["amount"], errors="coerce").fillna(0),
            asset_type=lambda df: df["asset_type"].fillna("stock"),
        )
        target = pd.to_datetime(
            pool["trade_date"], errors="coerce"
        ).max()  # latest synced market date
        # Take top stocks and top ETFs separately, so high-turnover ETFs do not
        # crowd stocks out of a single combined ranking.
        stocks = (
            pool[pool["asset_type"].eq("stock")]
            .sort_values("amount_num", ascending=False)
            .head(top)
        )
        etfs = (
            pool[pool["asset_type"].eq("etf")]
            .sort_values("amount_num", ascending=False)
            .head(etf_top)
            if include_etf
            else pool.iloc[0:0]
        )
        universe = pd.concat([stocks, etfs], ignore_index=True)
        inserted = 0
        skipped = 0
        failed = 0
        for row in universe.itertuples(index=False):
            symbol = str(row.symbol)
            nm_max = cov_map.get((item, symbol))
            if (
                not full
                and nm_max is not None
                and pd.notna(nm_max)
                and pd.notna(target)
                and nm_max >= target
            ):
                skipped += 1  # already current through the latest market date
                continue
            if not full and nm_max is not None and pd.notna(nm_max):
                start = (nm_max - pd.Timedelta(days=7)).strftime(
                    "%Y%m%d"
                )  # gap + adjustment buffer
            else:
                start = full_start
            try:
                history = fetch_history(
                    item,
                    symbol=symbol,
                    start_date=start,
                    end_date=end,
                    asset_type=str(getattr(row, "asset_type", "stock") or "stock"),
                )
            except Exception:
                failed += 1
                continue
            inserted += store.upsert_dataframe("daily_prices", history)
        result[f"{item}_history_rows"] = inserted
        result[f"{item}_history_skipped"] = skipped
        result[f"{item}_history_failed_symbols"] = failed
    return result


def sync_benchmarks(
    benchmarks: list[str] | None = None,
    lookback_days: int = 430,
    full: bool = False,
) -> dict[str, int]:
    store = get_store()
    store.init_db()
    coverage = store.query_df(
        "SELECT market, symbol, MAX(trade_date) AS max_td FROM daily_prices GROUP BY market, symbol"
    )
    cov_map: dict[tuple[str, str], pd.Timestamp] = {}
    if not coverage.empty:
        coverage["max_td"] = pd.to_datetime(coverage["max_td"], errors="coerce")
        cov_map = {
            (str(r.market), str(r.symbol)): r.max_td for r in coverage.itertuples(index=False)
        }
    end_dt = datetime.now()
    end = end_dt.strftime("%Y%m%d")
    full_start = (end_dt - timedelta(days=lookback_days)).strftime("%Y%m%d")
    fresh_cut = pd.Timestamp(end_dt.date()) - pd.Timedelta(days=4)  # tolerate weekends/holidays
    result: dict[str, int] = {}
    for benchmark in benchmarks or DEFAULT_BENCHMARKS:
        market, symbol = parse_benchmark(benchmark)
        key = f"{market}_{symbol}_benchmark_rows"
        nm_max = cov_map.get((market, str(symbol)))
        if not full and nm_max is not None and pd.notna(nm_max) and nm_max >= fresh_cut:
            result[key] = 0
            result[f"{market}_{symbol}_benchmark_skipped"] = 1
            continue
        if not full and nm_max is not None and pd.notna(nm_max):
            start = (nm_max - pd.Timedelta(days=7)).strftime("%Y%m%d")
        else:
            start = full_start
        try:
            history = fetch_benchmark_history(f"{market}:{symbol}", start_date=start, end_date=end)
        except Exception:
            result[key] = 0
            result[f"{market}_{symbol}_benchmark_failed"] = 1
            continue
        result[key] = store.upsert_dataframe("daily_prices", history)
        result[f"{market}_{symbol}_benchmark_failed"] = 0
    return result


def run_technical_indicators(
    *, lookback_days: int | None = None, markets: list[str] | None = None
) -> int:
    store = get_store()
    store.init_db()
    where: list[str] = []
    params: list[object] = []
    if lookback_days is not None:
        days = max(int(lookback_days), 180)
        where.append(
            "trade_date >= "
            f"(SELECT MAX(trade_date) - INTERVAL '{days} days' FROM daily_prices)"
        )
    if markets:
        placeholders = ", ".join("?" for _ in markets)
        where.append(f"market IN ({placeholders})")
        params.extend(str(market).upper() for market in markets)
    sql = "SELECT * FROM daily_prices"
    if where:
        sql += " WHERE " + " AND ".join(where)
    daily_prices = store.query_df(sql, params)
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
    lifecycle = store.query_df("SELECT * FROM security_lifecycle_events")
    results, themes = run_expert_model(
        snapshots=snapshots,
        tags=tags,
        technicals=technicals,
        fundamentals=fundamentals,
        settings=get_settings(),
        lifecycle=lifecycle,
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


def sync_fundamentals(
    market: MarketArg, top: int = 120, force: bool = False, max_age_days: int = 75
) -> dict[str, int]:
    """Sync fundamentals incrementally.

    Financials change only ~quarterly, so a name whose stored metrics are younger than
    ``max_age_days`` is carried forward to the current snapshot_date instead of being
    re-fetched (``force=True`` re-fetches everything). Consumers read the latest
    snapshot_date, so carried rows are re-stamped to it. This turns the daily refresh
    cost from O(top-N network fetches) to ~0 outside earnings season.
    """
    store = get_store()
    store.init_db()
    latest = _latest_snapshots(store)
    if latest.empty:
        raise RuntimeError(
            "No market snapshots found. Run `ah-screener sync-spot --market all` first."
        )

    markets = ["A", "HK", "US"] if market == "all" else [market]
    snapshot_date = latest["trade_date"].max()
    existing = store.query_df("SELECT * FROM financial_metrics")
    if not existing.empty:
        existing = existing.copy()
        existing["snapshot_date"] = pd.to_datetime(existing["snapshot_date"], errors="coerce")
        existing["updated_at"] = pd.to_datetime(existing["updated_at"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=max_age_days)
    result: dict[str, int] = {}
    for item in markets:
        universe = (
            latest[latest["market"] == item]
            .assign(amount_num=lambda df: pd.to_numeric(df["amount"], errors="coerce").fillna(0))
            .sort_values("amount_num", ascending=False)
            .head(top)
        )
        prev_latest: dict[str, pd.Series] = {}
        if not existing.empty:
            item_prev = existing[existing["market"] == item].sort_values("snapshot_date")
            for sym, grp in item_prev.groupby("symbol"):
                prev_latest[str(sym)] = grp.iloc[-1]
        store.execute(
            "DELETE FROM financial_metrics WHERE snapshot_date = ? AND market = ?",
            [snapshot_date, item],
        )
        statement_rows = metric_rows = fetched = carried = failed = 0
        for symbol in universe["symbol"].astype(str):
            prev = prev_latest.get(symbol)
            fresh = (
                prev is not None
                and pd.notna(prev.get("updated_at"))
                and prev["updated_at"] >= cutoff
            )
            if fresh and not force:
                row = prev.to_dict()
                row["snapshot_date"] = snapshot_date
                row["updated_at"] = pd.Timestamp(datetime.now())
                metric_rows += store.upsert_dataframe("financial_metrics", pd.DataFrame([row]))
                carried += 1
                continue
            try:
                items, metrics = fetch_fundamentals(item, symbol, snapshot_date=snapshot_date)  # type: ignore[arg-type]
            except Exception:
                failed += 1
                continue
            statement_rows += store.upsert_dataframe("financial_statement_items", items)
            metric_rows += store.upsert_dataframe("financial_metrics", metrics)
            fetched += 1
        result[f"{item}_financial_statement_items"] = statement_rows
        result[f"{item}_financial_metric_rows"] = metric_rows
        result[f"{item}_fundamentals_fetched"] = fetched
        result[f"{item}_fundamentals_carried"] = carried
        result[f"{item}_financial_failed_symbols"] = failed
    return result


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
            valuation_percentile_top_quartile=(
                "valuation_percentile",
                lambda value: value.quantile(0.75),
            ),
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
    items = store.query_df("SELECT * FROM financial_statement_items")
    return validate_potential_signals(prices, items=items)


def run_potential_threshold_sweep() -> pd.DataFrame:
    store = get_store()
    prices = store.query_df("SELECT * FROM daily_prices")
    return sweep_potential_thresholds(prices)


def run_potential_walk_forward() -> pd.DataFrame:
    store = get_store()
    prices = store.query_df("SELECT * FROM daily_prices")
    return walk_forward_potential_thresholds(prices)


def run_potential_scan(top: int = 80) -> dict[str, int]:
    store = get_store()
    store.init_db()
    prices = store.query_df("SELECT * FROM daily_prices")
    snapshots = _latest_table(store, "market_snapshots", "trade_date")
    fundamentals = store.query_df("SELECT * FROM financial_metrics")
    validation = validate_potential_signals(prices)
    candidates = scan_potential_candidates(
        prices, snapshots, validation=validation, top=top, fundamentals=fundamentals
    )
    # Replace prior rows for this strategy so a stricter scan doesn't leave stale picks.
    store.execute("DELETE FROM potential_candidates WHERE strategy = ?", ["potential_v1"])
    if not candidates.empty:
        candidates["updated_at"] = pd.Timestamp(datetime.now())
    return {"potential_candidates": store.upsert_dataframe("potential_candidates", candidates)}


def validate_etf_cluster_table(min_corr: float = 0.9) -> pd.DataFrame:
    store = get_store()
    snapshots = _latest_table(store, "market_snapshots", "trade_date")
    if snapshots.empty:
        return pd.DataFrame()
    pool = select_assets(snapshots, ETFS)
    prices = store.query_df("SELECT market, symbol, trade_date, close FROM daily_prices")
    return validate_etf_clusters(pool, prices, min_corr=min_corr)


def run_full_update(
    top: int = 120,
    lookback_days: int = 430,
    industry_limit: int | None = 50,
    concept_limit: int | None = 120,
    include_fundamentals: bool = True,
    include_report: bool = True,
    fundamentals_top: int | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {}

    def _step(name: str, fn) -> None:
        # One flaky free-data source must not abort the whole refresh: record and continue,
        # so downstream compute (technical/expert/potential/report) still runs on stored data.
        # Failures are also persisted (P2-5) so a silently shrinking coverage is observable.
        try:
            result[name] = fn()
        except Exception as exc:  # noqa: BLE001
            message = str(exc)[:300]
            result[name] = {"failed": message}
            logger.warning("ingest step %s failed: %s", name, message)
            _record_ingest_failure(name, message)

    _step("delisted_universe", sync_delisted_universe)
    _step("sync_spot", lambda: sync_spot("all"))
    if industry_limit is not None:
        _step("a_industry_tags", lambda: sync_a_tags("industry", limit=industry_limit))
    if concept_limit is not None:
        _step("a_concept_tags", lambda: sync_a_tags("concept", limit=concept_limit))
    _step("curated_theme_tags", sync_curated_theme_tags)
    _step("identity_mappings", sync_identity_mappings)
    _step("history", lambda: sync_history("all", top=top, lookback_days=lookback_days))
    _step("benchmarks", lambda: sync_benchmarks(lookback_days=lookback_days))
    _step("technical_rows", lambda: run_technical_indicators(lookback_days=lookback_days))
    if include_fundamentals:
        # Fundamentals are incremental (carried forward), so coverage can go much deeper
        # than history without re-fetch cost. Defaults to `top` when not specified.
        _step("fundamentals", lambda: sync_fundamentals("all", top=fundamentals_top or top))
    _step("expert_scores", run_expert_scores)
    _step("industry_valuation_stats", compute_industry_valuation_stats)
    _step("potential_scan", run_potential_scan)
    # Forward-return check on the expert decision buckets. Cheap, and honest by design:
    # while natural snapshots are too thin it just reports 0 samples, but once they
    # accumulate every refresh auto-produces the out-of-sample verdict.
    _step("expert_validation", lambda: run_expert_validation()[1])
    if include_report:
        _step("report", lambda: str(generate_report()))
    return result
