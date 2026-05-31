"""US-tuned screening model for the independent US screener.

This module reads the latest US snapshot, technical, fundamental and tag tables,
applies a few practical tradeability filters, and scores the universe with a
lightweight US-specific blend.

China-concept names are not physically dropped from the frame: they are marked
``is_filtered`` (with reason ``china_concept``), forced to ``expert_score=0`` /
``decision=reject``, and still persisted as an audit trail. Every candidate view
(``top_candidates``, MCP rows, reports) selects on ``~is_filtered``, so a China
name can never surface as a candidate — but downstream code querying
``expert_screening_results`` directly should filter on ``decision``/``is_filtered``.

Results are persisted into ``expert_screening_results`` using the existing schema;
US-only extras such as heat/macro/components stay on the returned DataFrame for
reporting/MCP consumers and are mirrored into JSON ``reasons`` where useful.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ah_screener import weights
from ah_screener.db import get_store
from ah_screener.scoring import _liquidity_score
from us_screener.china_concept import china_concept_symbols
from us_screener.classification_fd import sector_industry_map
from us_screener.concept_boards import concept_board_map
from us_screener.config import get_us_config, use_us_database
from us_screener.heat import compute_heat_scores
from us_screener.macro import get_macro_context, score_macro_transmission
from us_screener.relative_strength import compute_rs_scores
from us_screener.short_interest import short_ratio_map

STRATEGY_NAME = "us_premarket"
DEFAULT_TECHNICAL_SCORE = getattr(weights, "DEFAULT_TECHNICAL_SCORE", 42.0)
DEFAULT_FUNDAMENTAL_SCORE = getattr(weights, "DEFAULT_FUNDAMENTAL_SCORE", 50.0)

# Model parameters live in ah_screener.weights (single source of truth, with
# provenance) — see US_* entries. Aliased here for terse use in the blend below.
_SCORE_WEIGHTS = weights.US_EXPERT_COMPOSITE

_SCHEMA_COLUMNS = [
    "snapshot_date",
    "strategy",
    "market",
    "symbol",
    "name",
    "canonical_id",
    "expert_score",
    "master_score",
    "china_master_score",
    "fundamental_score",
    "detailed_industry",
    "industry_peer_group",
    "peer_score",
    "industry_fit_score",
    "valuation_percentile",
    "theme_score",
    "technical_score",
    "liquidity_score",
    "valuation_score",
    "risk_score",
    "decision",
    "theme_matches",
    "reasons",
    "updated_at",
]


def _series(frame: pd.DataFrame, column: str, dtype: str = "float") -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(index=frame.index, dtype=dtype)


def _num(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _clean(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if np.isnan(number) or np.isinf(number) else number
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.strftime("%Y-%m-%d")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, (str, int, list, dict)) else str(value)


def _latest_by_symbol(df: pd.DataFrame, date_column: str) -> pd.DataFrame:
    if df.empty or date_column not in df.columns:
        return df
    frame = df.copy()
    frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
    return frame.sort_values(date_column).drop_duplicates(["market", "symbol"], keep="last")


def _board_list(value: object) -> list[str]:
    seen: set[str] = set()
    boards: list[str] = []
    for item in value or []:
        board = str(item or "").strip()
        if board and board not in seen:
            seen.add(board)
            boards.append(board)
    return boards


def _peer_relative_score(values: pd.Series, groups: pd.Series, *, min_group: int = 8) -> pd.Series:
    """0-100 valuation score where a *lower* multiple scores higher, ranked WITHIN
    the peer (sector) group. Small/empty groups fall back to a whole-universe rank so
    a cheap utility isn't punished for not being as cheap as a bank, etc.
    """
    numeric = pd.to_numeric(values, errors="coerce").where(lambda s: s > 0)
    out = pd.Series(np.nan, index=numeric.index, dtype=float)
    sectors = groups.reindex(numeric.index).fillna("").astype(str)
    for name, idx in sectors.groupby(sectors).groups.items():
        if not name:
            continue
        peer = numeric.loc[idx].dropna()
        if len(peer) < min_group:
            continue
        out.loc[peer.index] = (1.0 - peer.rank(pct=True)) * 100.0
    remaining = out.isna() & numeric.notna()
    if remaining.any():
        rv = numeric[remaining]
        out.loc[rv.index] = (1.0 - rv.rank(pct=True)) * 100.0
    return out


def _theme_score(boards: list[str]) -> float:
    cfg = weights.US_THEME_SCORE
    if not boards:
        return float(cfg["base_no_match"])
    return float(min(cfg["cap"], cfg["base_match"] + cfg["per_theme"] * len(boards)))


def _compose_fundamental_score(row: pd.Series) -> float:
    explicit = _num(row.get("fundamental_score"))
    if explicit is not None:
        return float(np.clip(explicit, 0, 100))
    parts = [
        _num(row.get("quality_score")),
        _num(row.get("growth_score")),
        _num(row.get("balance_score")),
        _num(row.get("cashflow_score")),
    ]
    valid = [item for item in parts if item is not None]
    if valid:
        return float(np.clip(np.mean(valid), 0, 100))
    return DEFAULT_FUNDAMENTAL_SCORE


def _bool_value(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return False


def _filter_reasons(row: pd.Series, cfg) -> list[str]:
    reasons: list[str] = []
    if _bool_value(row.get("is_china_concept")) and cfg.exclude_china_concept:
        reasons.append("china_concept")
    price = _num(row.get("last_price"))
    if price is None or price <= 0:
        reasons.append("price_missing")
    elif price < weights.US_PENNY_PRICE:
        reasons.append("us_penny")
    amount = _num(row.get("amount"))
    if amount is None or amount <= 0:
        reasons.append("amount_missing")
    elif amount < cfg.min_us_amount:
        reasons.append("low_amount")
    market_cap = _num(row.get("market_cap"))
    if market_cap is not None and market_cap < cfg.min_market_cap:
        reasons.append("low_market_cap")
    return reasons


def _daily_factor_symbols(frame: pd.DataFrame, cfg) -> set[str]:
    """Symbols worth running expensive daily-price factors for.

    The final scoring frame still keeps every latest snapshot for audit/filtering,
    but heat and RS still scan/aggregate daily history. Running them only on names
    that can pass the hard tradeability gates keeps daily screening bounded without
    changing candidate eligibility. Vectorized: the gates are pure column predicates.
    """
    if frame.empty:
        return set()
    symbol = frame.get("symbol")
    if symbol is None:
        return set()
    symbol = symbol.astype(str).str.strip().str.upper()
    price = pd.to_numeric(_series(frame, "last_price"), errors="coerce")
    amount = pd.to_numeric(_series(frame, "amount"), errors="coerce")
    market_cap = pd.to_numeric(_series(frame, "market_cap"), errors="coerce")
    keep = (
        (symbol != "")
        & price.gt(0)
        & price.ge(weights.US_PENNY_PRICE)
        & amount.ge(cfg.min_us_amount)
        & ~market_cap.lt(cfg.min_market_cap)  # NaN cap passes (unknown != too-small)
    )
    return set(symbol[keep].tolist())


def _risk_score(filter_reasons: list[str]) -> float:
    penalty = sum(weights.US_RISK_PENALTY.get(reason, 0.0) for reason in filter_reasons)
    return float(np.clip(100.0 - penalty, 0, 100))


def _decision_series(frame: pd.DataFrame) -> pd.Series:
    """Vectorized decision bucketing over the whole frame (was apply axis=1)."""
    cuts = weights.US_DECISION
    expert = pd.to_numeric(_series(frame, "expert_score"), errors="coerce").fillna(0.0)
    technical = pd.to_numeric(_series(frame, "technical_score"), errors="coerce").fillna(0.0)
    is_filtered = _series(frame, "is_filtered", dtype="bool").fillna(False).astype(bool)
    conditions = [
        is_filtered,
        (expert >= cuts["core_min"]) & (technical >= cuts["core_technical_min"]),
        expert >= cuts["watchlist_min"],
        expert >= cuts["reserve_min"],
    ]
    choices = ["reject", "core_candidate", "watchlist", "reserve"]
    return pd.Series(np.select(conditions, choices, default="reject"), index=frame.index)


def _build_score_components(frame: pd.DataFrame) -> pd.Series:
    """Build JSON-friendly component dicts without row-wise DataFrame.apply."""
    values = pd.DataFrame(
        {
            "fundamental": pd.to_numeric(_series(frame, "fundamental_score_final"), errors="coerce").fillna(
                DEFAULT_FUNDAMENTAL_SCORE
            ),
            "technical": pd.to_numeric(_series(frame, "technical_score"), errors="coerce").fillna(
                DEFAULT_TECHNICAL_SCORE
            ),
            "valuation": pd.to_numeric(_series(frame, "valuation_score"), errors="coerce").fillna(50.0),
            "liquidity": pd.to_numeric(_series(frame, "liquidity_score"), errors="coerce").fillna(50.0),
            "heat": pd.to_numeric(_series(frame, "heat_score"), errors="coerce").fillna(50.0),
            "macro": pd.to_numeric(_series(frame, "macro_score"), errors="coerce").fillna(50.0),
        },
        index=frame.index,
    ).round(2)
    return pd.Series(values.to_dict("records"), index=frame.index)


def _build_reasons(frame: pd.DataFrame) -> pd.Series:
    """Build candidate reason lists from precomputed columns (cheaper than row apply)."""
    boards = _series(frame, "concept_boards", dtype=object)
    heat = pd.to_numeric(_series(frame, "heat_score"), errors="coerce").fillna(50.0)
    macro = pd.to_numeric(_series(frame, "macro_score"), errors="coerce").fillna(50.0)
    technical = pd.to_numeric(_series(frame, "technical_score"), errors="coerce").fillna(
        DEFAULT_TECHNICAL_SCORE
    )
    fundamental = pd.to_numeric(_series(frame, "fundamental_score_final"), errors="coerce").fillna(
        DEFAULT_FUNDAMENTAL_SCORE
    )
    filters = _series(frame, "filter_reasons", dtype=object)
    rows: list[list[str]] = []
    for bs, h, m, t, f, reasons in zip(boards, heat, macro, technical, fundamental, filters, strict=False):
        items: list[str] = []
        if isinstance(bs, list) and bs:
            items.append("主题板块: " + "、".join(bs[:3]))
        items.extend([f"heat={h:.1f}", f"macro={m:.1f}", f"technical={t:.1f}", f"fundamental={f:.1f}"])
        if reasons:
            items.append("filtered: " + ", ".join(reasons))
        rows.append(items)
    return pd.Series(rows, index=frame.index)


def _persist_results(store, results: pd.DataFrame, snapshot_date: pd.Timestamp) -> int:
    if results.empty:
        return 0
    payload = results.copy()
    payload["snapshot_date"] = snapshot_date.date()
    payload["strategy"] = STRATEGY_NAME
    payload["canonical_id"] = None
    payload["master_score"] = payload["expert_score"]
    _cm = weights.US_CHINA_MASTER_PROXY
    payload["china_master_score"] = (
        payload["fundamental_score_final"].fillna(DEFAULT_FUNDAMENTAL_SCORE) * _cm["fundamental"]
        + payload["valuation_score"].fillna(50.0) * _cm["valuation"]
        + payload["technical_score"].fillna(DEFAULT_TECHNICAL_SCORE) * _cm["technical"]
        + payload["macro_score"].fillna(50.0) * _cm["macro"]
    ).clip(0, 100)
    payload["fundamental_score"] = payload["fundamental_score_final"]
    payload["detailed_industry"] = payload["primary_board"].fillna("")
    payload["industry_peer_group"] = payload["primary_board"].fillna("")
    payload["peer_score"] = payload["heat_score"].fillna(50.0)
    payload["industry_fit_score"] = payload["macro_score"].fillna(50.0)
    payload["valuation_percentile"] = (100.0 - payload["valuation_score"].fillna(50.0)).clip(0, 100)
    payload["theme_score"] = payload["theme_score_final"].fillna(35.0)
    payload["risk_score"] = payload["risk_score"].fillna(100.0)
    payload["theme_matches"] = payload["concept_boards"].map(
        lambda boards: json.dumps(boards or [], ensure_ascii=False)
    )
    payload["reasons"] = payload["reasons_list"].map(
        lambda items: json.dumps(items or [], ensure_ascii=False)
    )
    payload["updated_at"] = pd.Timestamp(datetime.now())

    schema_payload = payload[_SCHEMA_COLUMNS].copy()
    store.execute(
        "DELETE FROM expert_screening_results WHERE snapshot_date = ? AND strategy = ? AND market = 'US'",
        [snapshot_date.date(), STRATEGY_NAME],
    )
    return store.upsert_dataframe("expert_screening_results", schema_payload)


def run_us_screen(store=None, *, persist: bool = True, macro_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the US-tuned screen and optionally persist schema-compatible rows."""
    if store is None:
        use_us_database()
        store = get_store()
    store.init_db()
    cfg = get_us_config()

    snapshots = store.query_df(
        """
        SELECT market, symbol, name, trade_date, last_price, amount, pe_ttm, pb, market_cap, asset_type
        FROM market_snapshots
        WHERE market = 'US' AND COALESCE(asset_type, 'stock') <> 'etf'
        """
    )
    if snapshots.empty:
        empty = pd.DataFrame()
        try:
            macro_ctx = macro_context or get_macro_context(store)
        except Exception as exc:  # noqa: BLE001 — empty-universe path must not abort on macro
            macro_ctx = {
                "status": "error",
                "market_score": 50.0,
                "regime": "neutral",
                "errors": [{"source": "macro_context", "error": str(exc)}],
            }
        return {
            "snapshot_date": None,
            "macro_context": macro_ctx,
            "results": empty,
            "persisted_rows": 0,
            "factor_universe": 0,
            "summary": {"top_candidates": []},
        }

    latest_snap = _latest_by_symbol(snapshots, "trade_date")
    snapshot_date = pd.to_datetime(latest_snap["trade_date"], errors="coerce").max()
    daily_factor_symbols = _daily_factor_symbols(latest_snap, cfg)

    technical = _latest_by_symbol(
        store.query_df("SELECT * FROM technical_indicators WHERE market = 'US'"), "snapshot_date"
    )
    fundamentals = _latest_by_symbol(
        store.query_df("SELECT * FROM financial_metrics WHERE market = 'US'"), "snapshot_date"
    )
    heat = compute_heat_scores(store, symbols=daily_factor_symbols)
    rs = compute_rs_scores(store, symbols=daily_factor_symbols)

    frame = latest_snap.merge(
        technical[["market", "symbol", "technical_score", "technical_signal", "return_20d"]]
        if not technical.empty
        else pd.DataFrame(columns=["market", "symbol", "technical_score", "technical_signal", "return_20d"]),
        on=["market", "symbol"],
        how="left",
    ).merge(
        fundamentals,
        on=["market", "symbol"],
        how="left",
        suffixes=("", "_fund"),
    ).merge(heat, on=["market", "symbol"], how="left").merge(
        rs if not rs.empty else pd.DataFrame(columns=["market", "symbol", "rs_score", "rs_components"]),
        on=["market", "symbol"],
        how="left",
    )

    boards_map = concept_board_map(store)
    china_symbols = china_concept_symbols(store)
    frame["concept_boards"] = frame["symbol"].map(
        lambda symbol: _board_list(boards_map.get(str(symbol).strip().upper(), []))
    )
    frame["primary_board"] = frame["concept_boards"].map(lambda boards: boards[0] if boards else "")
    frame["is_china_concept"] = frame["symbol"].astype(str).str.upper().isin(china_symbols)
    # short_ratio is carried for the report's squeeze_watch annotation only (elevated
    # short-volume + high RS). It is NOT a scoring factor: the FINRA daily short-VOLUME
    # ratio is a noisy market-maker-heavy proxy (not true short interest / days-to-cover),
    # so it stays out of expert_score / risk_score to avoid polluting the composite.
    short_map = short_ratio_map(store)
    frame["short_ratio"] = frame["symbol"].map(lambda s: short_map.get(str(s).strip().upper()))

    frame["fundamental_score_final"] = frame.apply(_compose_fundamental_score, axis=1)
    frame["technical_score"] = (
        pd.to_numeric(_series(frame, "technical_score"), errors="coerce")
        .fillna(DEFAULT_TECHNICAL_SCORE)
        .clip(0, 100)
    )
    sector_map = sector_industry_map(store)
    frame["sector"] = frame["symbol"].map(
        lambda s: (sector_map.get(str(s).strip().upper()) or {}).get("sector", "")
    )
    # PEG = trailing PE / trailing earnings growth (SEC net_profit_yoy). This is a
    # growth-adjusted valuation from the always-available bulk fundamentals — NOT a
    # forward/analyst PEG (forward estimates aren't free in bulk; see
    # forward_estimates.py, which surfaces forward PE as a report-only annotation).
    # Only for positive growth; lower PEG scores higher.
    _pe = pd.to_numeric(_series(frame, "pe_ttm"), errors="coerce")
    _growth = pd.to_numeric(_series(frame, "net_profit_yoy"), errors="coerce")
    frame["peg"] = (_pe / _growth.where(_growth > 0)).round(3)
    # Valuation is ranked WITHIN sector peers (FD classification) — cross-sector PE/PB/PEG
    # aren't comparable. Falls back to a whole-universe rank where sector is unknown.
    # Weighted-average over whichever component is present, so a missing one does not
    # blank out a name that still has the others.
    _val = pd.DataFrame(
        {
            "pe": _peer_relative_score(_series(frame, "pe_ttm"), frame["sector"]),
            "pb": _peer_relative_score(_series(frame, "pb"), frame["sector"]),
            "peg": _peer_relative_score(frame["peg"], frame["sector"]),
        }
    )
    _w = pd.Series(weights.US_VALUATION_WEIGHTS)
    _wsum = _val.notna().mul(_w, axis=1).sum(axis=1).replace(0, np.nan)
    frame["valuation_score"] = (
        (_val.fillna(0.0).mul(_w, axis=1).sum(axis=1) / _wsum).fillna(50.0).clip(0, 100)
    )
    frame["liquidity_score"] = _liquidity_score(frame).fillna(50.0).clip(0, 100)
    # Momentum factor blends absolute heat (RVOL/return/52w) with relative strength
    # (excess return vs market) — leadership shows up in RS first.
    _heat = pd.to_numeric(_series(frame, "heat_score"), errors="coerce").fillna(50.0)
    frame["rs_score"] = pd.to_numeric(_series(frame, "rs_score"), errors="coerce").fillna(50.0)
    _hb = weights.US_HEAT_RS_BLEND
    frame["heat_score"] = (_hb["heat"] * _heat + _hb["rs"] * frame["rs_score"]).clip(0, 100)
    frame["theme_score_final"] = frame["concept_boards"].map(_theme_score)

    # Macro is an optional tilt: a failure here must leave every name at a neutral
    # macro_score, never abort the screen.
    try:
        macro_context = macro_context or get_macro_context(store)
        macro_scores = score_macro_transmission(
            frame[["market", "symbol", "concept_boards"]], store, macro_context
        )
    except Exception as exc:  # noqa: BLE001 — degrade to neutral macro, keep the screen running
        macro_context = {
            "status": "error",
            "market_score": 50.0,
            "regime": "neutral",
            "errors": [{"source": "macro_scoring", "error": str(exc)}],
        }
        macro_scores = pd.DataFrame(columns=["market", "symbol", "macro_score", "macro_components"])
    frame = frame.merge(macro_scores, on=["market", "symbol"], how="left")
    frame["macro_score"] = pd.to_numeric(_series(frame, "macro_score"), errors="coerce").fillna(50.0)
    if "macro_components" not in frame.columns:
        frame["macro_components"] = None
    frame["macro_components"] = frame["macro_components"].where(frame["macro_components"].notna(), None)

    frame["filter_reasons"] = frame.apply(lambda row: _filter_reasons(row, cfg), axis=1)
    frame["is_filtered"] = frame["filter_reasons"].map(bool).astype(bool)
    frame["risk_score"] = frame["filter_reasons"].map(_risk_score)

    frame["expert_score"] = (
        frame["fundamental_score_final"] * _SCORE_WEIGHTS["fundamental"]
        + frame["technical_score"] * _SCORE_WEIGHTS["technical"]
        + frame["valuation_score"] * _SCORE_WEIGHTS["valuation"]
        + frame["liquidity_score"] * _SCORE_WEIGHTS["liquidity"]
        + frame["heat_score"] * _SCORE_WEIGHTS["heat"]
        + frame["macro_score"] * _SCORE_WEIGHTS["macro"]
    ).clip(0, 100)
    frame.loc[frame["is_filtered"], "expert_score"] = 0.0
    frame["reasons_list"] = _build_reasons(frame)
    frame["decision"] = _decision_series(frame)
    frame["score_components"] = _build_score_components(frame)

    frame["expert_score"] = pd.to_numeric(frame["expert_score"], errors="coerce").fillna(0).round(2)
    frame["macro_score"] = pd.to_numeric(frame["macro_score"], errors="coerce").fillna(50).round(2)
    frame["heat_score"] = pd.to_numeric(frame["heat_score"], errors="coerce").fillna(50).round(2)
    frame["valuation_score"] = pd.to_numeric(frame["valuation_score"], errors="coerce").fillna(50).round(2)
    frame["liquidity_score"] = pd.to_numeric(frame["liquidity_score"], errors="coerce").fillna(50).round(2)
    frame["fundamental_score_final"] = (
        pd.to_numeric(frame["fundamental_score_final"], errors="coerce")
        .fillna(DEFAULT_FUNDAMENTAL_SCORE)
        .round(2)
    )
    frame["is_filtered"] = frame["is_filtered"].map(_bool_value).astype(bool)
    frame["is_china_concept"] = frame["is_china_concept"].map(_bool_value).astype(bool)

    frame = frame.sort_values(
        ["is_filtered", "expert_score", "heat_score", "symbol"], ascending=[True, False, False, True]
    ).reset_index(drop=True)

    persisted_rows = _persist_results(store, frame, snapshot_date) if persist and snapshot_date is not None else 0
    return {
        "snapshot_date": None if snapshot_date is None or pd.isna(snapshot_date) else snapshot_date.strftime("%Y-%m-%d"),
        "macro_context": macro_context,
        "results": frame,
        "persisted_rows": persisted_rows,
        "factor_universe": int(len(daily_factor_symbols)),
        "summary": {
            "top_candidates": [
                {
                    "market": _clean(row.get("market")),
                    "symbol": _clean(row.get("symbol")),
                    "name": _clean(row.get("name")),
                    "expert_score": _clean(row.get("expert_score")),
                    "decision": _clean(row.get("decision")),
                    "is_filtered": _clean(row.get("is_filtered")),
                    "filter_reasons": _clean(row.get("filter_reasons")),
                    "concept_boards": _clean(row.get("concept_boards")),
                    "score_components": _clean(row.get("score_components")),
                }
                for _, row in frame.head(20).iterrows()
            ]
        },
    }
