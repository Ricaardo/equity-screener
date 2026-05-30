"""US backfill + incremental orchestration.

Reuses ah_screener pipeline steps but drives them US-only and against the
independent US DuckDB (see ``config.use_us_database``). Mirrors the resilient
per-step try/except pattern of ``ah_screener.pipeline.run_full_update`` so a flaky
free-data source records a failure without aborting the whole run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from us_screener.china_concept import tag_china_concept
from us_screener.classification_fd import tag_fd_classification
from us_screener.concept_boards import tag_concept_boards
from us_screener.config import get_us_config, use_us_database
from us_screener.heat import compute_heat_scores
from us_screener.macro import get_macro_context
from us_screener.reporting_us import generate_us_premarket_report
from us_screener.scoring_us import run_us_screen
from us_screener.valuation_enrich import enrich_us_valuation_all

logger = logging.getLogger(__name__)


def _core():
    """Import reused core *after* routing the store to the US database."""
    use_us_database()
    from ah_screener import pipeline as ah_pipeline
    from ah_screener.db import get_store

    return ah_pipeline, get_store


def _step(result: dict[str, Any], name: str, fn: Callable[[], Any]) -> None:
    """Run one pipeline step; record failure but keep going (free-data sources are flaky)."""
    try:
        result[name] = fn()
    except Exception as exc:  # noqa: BLE001 — mirror ah_screener resilient pipeline
        logger.warning("us_screener step %s failed: %s", name, exc)
        result[name] = {"error": str(exc)}


def _backfill_universe(ah, *, batch_limit: int, include_etf: bool, max_symbols: int) -> dict[str, int]:
    """Page through the full US security master, populating securities + snapshots.

    Futu-path only: each batch derives snapshots per symbol. The free path uses
    ``_localize_universe`` (bulk Sina quotes) instead — far faster.
    """
    offset = 0
    total_sec = 0
    total_snap = 0
    batches = 0
    while offset < max_symbols:
        out = ah.sync_us_spot_batch(offset=offset, limit=batch_limit, include_etf=include_etf)
        n_sec = int(out.get("US_securities", 0) or 0)
        if n_sec == 0:
            break
        total_sec += n_sec
        total_snap += int(out.get("US_snapshots", 0) or 0)
        batches += 1
        offset += batch_limit
    return {"securities": total_sec, "snapshots": total_snap, "batches": batches}


def _localize_universe(ah, store, *, batch_limit: int, include_etf: bool, max_symbols: int) -> dict[str, Any]:
    """Universe + snapshots, source-aware. Free path = bulk Sina quotes (seconds
    for the whole market); Futu path = per-symbol paging."""
    if get_us_config().use_futu:
        return _backfill_universe(
            ah, batch_limit=batch_limit, include_etf=include_etf, max_symbols=max_symbols
        )
    from us_screener.data_source import localize_us_universe_free

    return localize_us_universe_free(store, include_etf=include_etf)


def _top_liquid_symbols(store, top: int) -> list[str]:
    """Most-tradeable US stocks (by turnover) from the localized snapshots."""
    df = store.query_df(
        """
        SELECT symbol, MAX(amount) AS amt FROM market_snapshots
        WHERE market = 'US' AND COALESCE(asset_type, 'stock') <> 'etf' AND amount IS NOT NULL
        GROUP BY symbol ORDER BY amt DESC LIMIT ?
        """,
        [int(top)],
    )
    return [str(s).strip().upper() for s in df["symbol"].tolist()] if not df.empty else []


def _localize_history(ah, store, *, top: int, lookback_days: int, include_etf: bool, full: bool) -> dict[str, Any]:
    """History source-aware. Free path = parallel akshare for the liquid top-N
    (independent calls, ~6x faster than sequential); Futu path = core sync_history."""
    if get_us_config().use_futu:
        return ah.sync_history(
            "US", top=top, lookback_days=lookback_days, include_etf=include_etf, full=full
        )
    from us_screener.data_source import localize_us_history_alpaca, localize_us_history_free

    # Full backfill: prefer the local stooq bulk ZIP if configured — all symbols,
    # full adjusted history, zero API calls. (Daily updates use the live bars path.)
    cfg = get_us_config()
    if full and cfg.stooq_zip and Path(cfg.stooq_zip).exists():
        from us_screener.stooq_loader import consolidate_history_sources, load_stooq_us_zip

        since = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        out = load_stooq_us_zip(store, cfg.stooq_zip, since=since, include_etf=include_etf)
        # Enforce single-adjustment history so technicals never splice bases.
        out["consolidate"] = consolidate_history_sources(store)
        out["history_source"] = "stooq.d"
        return out

    symbols = _top_liquid_symbols(store, top)
    # Primary: Alpaca bulk bars (tens of requests for the whole liquid set, IEX feed).
    alpaca = localize_us_history_alpaca(store, symbols, lookback_days=lookback_days)
    if alpaca.get("status") == "ok" and int(alpaca.get("symbols_ok", 0)) > 0:
        alpaca["history_source"] = "alpaca.iex"
        return alpaca
    # Fallback only when Alpaca is unavailable: sequential akshare (crash-safe).
    akshare = localize_us_history_free(store, symbols, lookback_days=lookback_days)
    akshare["history_source"] = "akshare"
    akshare["alpaca"] = alpaca
    return akshare


def run_us_full_backfill(
    *,
    batch_limit: int = 200,
    history_top: int = 4000,
    lookback_days: int = 1100,
    include_etf: bool = True,
    fundamentals_top: int = 1500,
    max_symbols: int = 20000,
) -> dict[str, Any]:
    """First-run full localization: pull the entire US universe + history + fundamentals
    into the independent US DuckDB. Subsequent runs should use ``run_us_premarket_update``.
    """
    ah, get_store = _core()
    store = get_store()
    store.init_db()

    result: dict[str, Any] = {"mode": "full_backfill", "source": "futu" if get_us_config().use_futu else "free"}
    _step(result, "delisted", lambda: ah.sync_delisted_universe())
    _step(
        result,
        "universe",
        lambda: _localize_universe(
            ah, store, batch_limit=batch_limit, include_etf=include_etf, max_symbols=max_symbols
        ),
    )
    _step(result, "classify", lambda: ah.classify_existing_securities())
    _step(
        result,
        "history",
        lambda: _localize_history(
            ah, store, top=history_top, lookback_days=lookback_days, include_etf=include_etf, full=True
        ),
    )
    _step(result, "fundamentals", lambda: ah.sync_fundamentals("US", top=fundamentals_top))
    _step(result, "valuation_enrich", lambda: enrich_us_valuation_all(store))
    _step(result, "technical", lambda: ah.run_technical_indicators())
    _step(result, "china_concept", lambda: tag_china_concept(store, use_sec=False))
    _step(result, "concept_boards", lambda: tag_concept_boards(store))
    _step(result, "fd_classification", lambda: tag_fd_classification(store))
    _step(result, "heat", lambda: {"rows": len(compute_heat_scores(store))})
    _step(result, "macro", lambda: get_macro_context(store))
    _step(
        result,
        "screen",
        lambda: {"persisted_rows": run_us_screen(store=store, persist=True)["persisted_rows"]},
    )
    _step(result, "report", lambda: str(generate_us_premarket_report()))
    return result


def run_us_premarket_update(
    *,
    batch_limit: int = 200,
    history_top: int = 4000,
    lookback_days: int = 430,
    include_etf: bool = True,
    fundamentals_top: int | None = None,
    max_symbols: int = 20000,
    refresh_history: bool = False,
) -> dict[str, Any]:
    """Incremental daily refresh — snapshot-first.

    By default the daily run only refreshes the bulk snapshot (latest price / market
    cap / PE via Sina) and re-screens; it does NOT append daily bars. The history
    base stays a single, internally-consistent adjustment source (stooq, periodically
    reloaded), so technicals are never spliced across adjustment bases. Set
    ``refresh_history=True`` only when there is no stooq base and you want Alpaca
    (adjusted) increments — still a single source then.
    """
    ah, get_store = _core()
    store = get_store()
    store.init_db()

    result: dict[str, Any] = {"mode": "premarket_update", "source": "futu" if get_us_config().use_futu else "free"}
    _step(result, "delisted", lambda: ah.sync_delisted_universe())
    _step(
        result,
        "universe",
        lambda: _localize_universe(
            ah, store, batch_limit=batch_limit, include_etf=include_etf, max_symbols=max_symbols
        ),
    )
    _step(result, "classify", lambda: ah.classify_existing_securities())
    if refresh_history or get_us_config().use_futu:
        _step(
            result,
            "history",
            lambda: _localize_history(
                ah, store, top=history_top, lookback_days=lookback_days, include_etf=include_etf, full=False
            ),
        )
    if fundamentals_top:
        _step(result, "fundamentals", lambda: ah.sync_fundamentals("US", top=fundamentals_top))
    _step(result, "valuation_enrich", lambda: enrich_us_valuation_all(store))
    _step(result, "technical", lambda: ah.run_technical_indicators())
    _step(result, "china_concept", lambda: tag_china_concept(store, use_sec=False))
    _step(result, "concept_boards", lambda: tag_concept_boards(store))
    _step(result, "fd_classification", lambda: tag_fd_classification(store))
    _step(result, "heat", lambda: {"rows": len(compute_heat_scores(store))})
    _step(result, "macro", lambda: get_macro_context(store))
    _step(
        result,
        "screen",
        lambda: {"persisted_rows": run_us_screen(store=store, persist=True)["persisted_rows"]},
    )
    _step(result, "report", lambda: str(generate_us_premarket_report()))
    return result
