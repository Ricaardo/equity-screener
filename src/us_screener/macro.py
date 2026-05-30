"""Simple offline macro context for the US pre-market report.

The macro layer prefers already-stored proxy ETF price history (SPY/QQQ/IWM/TLT
and a few sector ETFs). If those proxies are missing, it falls back to a neutral
context instead of introducing new network calls or dependencies.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd


MACRO_COLUMNS = ["market", "symbol", "macro_score", "macro_components"]
PROXY_SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "XLK", "XLE")
_GROWTH_BOARDS = frozenset({"AI算力", "量子计算", "网络安全", "电动车"})
_INFRA_BOARDS = frozenset({"核电", "数据中心电力"})
_CRYPTO_BOARDS = frozenset({"稳定币加密"})
_DEFENSIVE_BOARDS = frozenset({"减肥药GLP1"})


def _clip(value: float) -> float:
    return float(np.clip(value, 0, 100))


def _num(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _loads_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item or "").strip()]
    return [str(parsed)]


def _extract_boards(row: pd.Series) -> list[str]:
    for field in ("concept_boards", "theme_matches"):
        boards = _loads_list(row.get(field))
        if boards:
            return boards
    board = str(row.get("board") or "").strip()
    return [board] if board else []


def _proxy_signal_score(close: pd.Series) -> tuple[float, dict[str, Any]]:
    last = pd.to_numeric(close, errors="coerce").iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else np.nan
    ret20 = (
        float(last / close.shift(20).iloc[-1] - 1)
        if len(close) > 20 and pd.notna(close.shift(20).iloc[-1]) and close.shift(20).iloc[-1] > 0
        else np.nan
    )
    score = 50.0
    if pd.notna(ma20) and pd.notna(last):
        score += 20.0 if last > ma20 else -20.0
    if pd.notna(ret20):
        if ret20 > 0.03:
            score += 20.0
        elif ret20 > 0:
            score += 10.0
        elif ret20 < -0.03:
            score -= 20.0
        else:
            score -= 10.0
    return _clip(score), {
        "close": _num(last),
        "ma20": _num(ma20),
        "return_20d": _num(ret20),
        "above_ma20": None if pd.isna(ma20) or pd.isna(last) else bool(last > ma20),
    }


def _etf_signal(store) -> tuple[float | None, dict[str, float], dict[str, Any], Any]:
    """Proxy-ETF momentum signal: (etf_score|None, component_scores, proxy_metrics, as_of)."""
    daily = store.query_df(
        """
        SELECT symbol, trade_date, close, adj_type
        FROM daily_prices
        WHERE market = 'US' AND symbol IN ('SPY', 'QQQ', 'IWM', 'TLT', 'XLK', 'XLE')
        """
    )
    component_scores: dict[str, float] = {}
    proxy_metrics: dict[str, dict[str, Any]] = {}
    as_of = None
    if daily.empty:
        return None, component_scores, proxy_metrics, as_of
    daily = daily.copy()
    if "adj_type" in daily.columns:
        daily = daily[~daily["adj_type"].fillna("").astype(str).str.lower().eq("benchmark")]
    if daily.empty:
        return None, component_scores, proxy_metrics, as_of
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], errors="coerce")
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
    for symbol, group in daily.groupby("symbol"):
        prices = group.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
        close = pd.to_numeric(prices["close"], errors="coerce").dropna()
        if len(close) < 20:
            continue
        score, metrics = _proxy_signal_score(close)
        component_scores[str(symbol)] = score
        proxy_metrics[str(symbol)] = metrics
        latest_date = prices["trade_date"].max()
        if pd.notna(latest_date) and (as_of is None or latest_date > as_of):
            as_of = latest_date
    etf_score = round(float(np.mean(list(component_scores.values()))), 2) if component_scores else None
    return etf_score, component_scores, proxy_metrics, as_of


def get_macro_context(store) -> dict[str, Any]:
    """Macro/risk-appetite context blending real FRED gauges (credit/curve/VIX)
    with proxy-ETF momentum. Falls back to neutral only when neither is available."""
    from us_screener.fred import get_fred_macro

    etf_score, component_scores, proxy_metrics, as_of = _etf_signal(store)
    try:
        fred = get_fred_macro()
    except Exception:  # noqa: BLE001 — never let macro network failure break a screen
        fred = {"status": "unavailable", "fred_score": None}
    fred_score = fred.get("fred_score") if fred.get("status") == "ok" else None

    parts = [value for value in (fred_score, etf_score) if value is not None]
    if not parts:
        return {
            "status": "fallback_neutral",
            "market_score": 50.0,
            "regime": "neutral",
            "summary": "No FRED or proxy-ETF data; macro context falls back to neutral.",
            "as_of": None,
            "component_scores": {},
            "proxy_metrics": {},
            "fred": fred,
        }
    # FRED (real rates/credit/vol) leads; ETF momentum confirms.
    if fred_score is not None and etf_score is not None:
        market_score = round(0.6 * fred_score + 0.4 * etf_score, 2)
    else:
        market_score = round(parts[0], 2)

    if market_score >= 60:
        regime = "bullish"
        summary = "Risk appetite is supportive; credit is firm and momentum transmission is constructive."
    elif market_score <= 40:
        regime = "bearish"
        summary = "Macro tape is risk-off; prefer higher quality and stronger technical confirmation."
    else:
        regime = "neutral"
        summary = "Macro tape is mixed; stock selection matters more than broad beta."

    return {
        "status": "ok",
        "market_score": market_score,
        "regime": regime,
        "summary": summary,
        "as_of": None if as_of is None or pd.isna(as_of) else as_of.strftime("%Y-%m-%d"),
        "component_scores": {key: round(value, 2) for key, value in component_scores.items()},
        "proxy_metrics": proxy_metrics,
        "etf_score": etf_score,
        "fred": fred,
    }


def score_macro_transmission(
    candidates: pd.DataFrame, store, context: dict[str, Any] | None = None
) -> pd.DataFrame:
    """Attach a per-symbol macro transmission score to candidate rows."""
    if candidates.empty:
        return pd.DataFrame(columns=MACRO_COLUMNS)
    context = context or get_macro_context(store)
    base_score = float(context.get("market_score", 50.0) or 50.0)
    growth_score = _num((context.get("component_scores") or {}).get("QQQ"))
    tech_score = _num((context.get("component_scores") or {}).get("XLK"))
    infra_score = _num((context.get("component_scores") or {}).get("XLE"))
    small_cap_score = _num((context.get("component_scores") or {}).get("IWM"))
    rates_score = _num((context.get("component_scores") or {}).get("TLT"))

    rows: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        boards = _extract_boards(row)
        score = base_score
        adjustments: list[dict[str, Any]] = []

        if _GROWTH_BOARDS.intersection(boards):
            tilt = np.mean([value for value in [growth_score, tech_score] if value is not None])
            if not np.isnan(tilt):
                delta = (float(tilt) - 50.0) * 0.30
                score += delta
                adjustments.append({"driver": "growth", "delta": round(delta, 2)})
        if _INFRA_BOARDS.intersection(boards) and infra_score is not None:
            delta = (infra_score - 50.0) * 0.25
            score += delta
            adjustments.append({"driver": "infrastructure", "delta": round(delta, 2)})
        if _CRYPTO_BOARDS.intersection(boards):
            ref = np.mean([value for value in [growth_score, small_cap_score] if value is not None])
            if not np.isnan(ref):
                delta = (float(ref) - 50.0) * 0.35
                score += delta
                adjustments.append({"driver": "crypto_beta", "delta": round(delta, 2)})
        if _DEFENSIVE_BOARDS.intersection(boards) and rates_score is not None:
            delta = (rates_score - 50.0) * 0.15
            score += delta
            adjustments.append({"driver": "defensive_rates", "delta": round(delta, 2)})

        score = round(_clip(score), 2)
        rows.append(
            {
                "market": row.get("market", "US"),
                "symbol": row.get("symbol"),
                "macro_score": score,
                "macro_components": {
                    "base_market_score": round(base_score, 2),
                    "regime": context.get("regime", "neutral"),
                    "boards": boards,
                    "adjustments": adjustments,
                },
            }
        )

    return pd.DataFrame(rows, columns=MACRO_COLUMNS)
