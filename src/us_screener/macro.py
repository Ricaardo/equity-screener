"""Simple offline macro context for the US pre-market report.

The macro layer prefers already-stored proxy ETF price history (SPY/QQQ/IWM/TLT
and a few sector ETFs). If those proxies are missing, it falls back to a neutral
context instead of introducing new network calls or dependencies.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import numpy as np
import pandas as pd


MACRO_COLUMNS = ["market", "symbol", "macro_score", "macro_components"]
logger = logging.getLogger(__name__)
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


def _mean_optional(values: list[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    return float(np.mean(valid)) if valid else None


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

    # Both legs degrade independently — neither a DB hiccup in the proxy-ETF read nor a
    # FRED network failure may break the screen (the documented contract is "fall back
    # to neutral", so the fallback path must actually be reachable).
    errors: list[dict[str, str]] = []
    try:
        etf_score, component_scores, proxy_metrics, as_of = _etf_signal(store)
    except Exception as exc:  # noqa: BLE001 — proxy-ETF DB read is best-effort
        logger.warning("proxy-ETF macro signal failed: %s", exc)
        errors.append({"source": "proxy_etf", "error": str(exc)})
        etf_score, component_scores, proxy_metrics, as_of = None, {}, {}, None
    try:
        fred = get_fred_macro()
    except Exception as exc:  # noqa: BLE001 — never let macro network failure break a screen
        logger.warning("FRED macro signal failed: %s", exc)
        errors.append({"source": "fred", "error": str(exc)})
        fred = {"status": "unavailable", "fred_score": None, "error": str(exc)}
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
            "errors": errors,
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

    # Forward policy read (rate path + inflation expectations) — separate from the
    # risk-appetite tape. A risk-on tape can coexist with hawkish policy.
    policy = fred.get("policy") if isinstance(fred, dict) else None
    if policy and policy.get("summary"):
        summary = f"{summary} 政策面：{policy['summary']}"

    return {
        "status": "ok",
        "market_score": market_score,
        "regime": regime,
        "summary": summary,
        "policy": policy,
        "as_of": (
            as_of.strftime("%Y-%m-%d")
            if as_of is not None and pd.notna(as_of)
            else ((fred.get("as_of") if isinstance(fred, dict) else None))
        ),
        "component_scores": {key: round(value, 2) for key, value in component_scores.items()},
        "proxy_metrics": proxy_metrics,
        "etf_score": etf_score,
        "fred": fred,
        "errors": errors,
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
    # Policy expectation tilt: higher-for-longer hurts long-duration growth/crypto;
    # dovish helps. This is the forward-looking overlay (not a trailing level).
    policy_stance = ((context.get("policy") or {}).get("stance")) or "neutral"
    policy_delta = -6.0 if policy_stance == "hawkish" else (6.0 if policy_stance == "dovish" else 0.0)

    # concept_boards are sparse: most of the (whole-universe) names carry no board and
    # therefore land on exactly the base score with no adjustments. Resolve boards once,
    # build those base-only rows in one vectorized pass, and run the per-board tilt loop
    # only over the small subset that actually has boards. Behaviour is unchanged.
    boards_per_row = candidates.apply(_extract_boards, axis=1)
    base_value = round(_clip(base_score), 2)
    regime = context.get("regime", "neutral")
    base_components = {
        "base_market_score": round(base_score, 2),
        "regime": regime,
        "boards": [],
        "adjustments": [],
    }

    has_boards = boards_per_row.map(bool)
    rows: list[dict[str, Any]] = [
        {
            "market": candidates.at[idx, "market"] if "market" in candidates.columns else "US",
            "symbol": candidates.at[idx, "symbol"] if "symbol" in candidates.columns else None,
            "macro_score": base_value,
            "macro_components": dict(base_components),
        }
        for idx in candidates.index[~has_boards]
    ]

    for idx in candidates.index[has_boards]:
        row = candidates.loc[idx]
        boards = boards_per_row.loc[idx]
        score = base_score
        adjustments: list[dict[str, Any]] = []

        if _GROWTH_BOARDS.intersection(boards):
            tilt = _mean_optional([growth_score, tech_score])
            if tilt is not None:
                delta = (tilt - 50.0) * 0.30
                score += delta
                adjustments.append({"driver": "growth", "delta": round(delta, 2)})
        if _INFRA_BOARDS.intersection(boards) and infra_score is not None:
            delta = (infra_score - 50.0) * 0.25
            score += delta
            adjustments.append({"driver": "infrastructure", "delta": round(delta, 2)})
        if _CRYPTO_BOARDS.intersection(boards):
            ref = _mean_optional([growth_score, small_cap_score])
            if ref is not None:
                delta = (ref - 50.0) * 0.35
                score += delta
                adjustments.append({"driver": "crypto_beta", "delta": round(delta, 2)})
        if _DEFENSIVE_BOARDS.intersection(boards) and rates_score is not None:
            delta = (rates_score - 50.0) * 0.15
            score += delta
            adjustments.append({"driver": "defensive_rates", "delta": round(delta, 2)})
        if policy_delta and (_GROWTH_BOARDS | _CRYPTO_BOARDS).intersection(boards):
            score += policy_delta
            adjustments.append({"driver": f"policy_{policy_stance}", "delta": round(policy_delta, 2)})

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
