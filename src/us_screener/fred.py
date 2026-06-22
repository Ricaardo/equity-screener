"""Real macro inputs from FRED, read through the data-access facade.

We pull a handful of risk-appetite gauges — high-yield credit spread, the 2s10s
curve, VIX, the broad dollar and the 10Y level — and fold them into a 0-100
risk-appetite score. This replaces the previous proxy-ETF-momentum-only macro
signal (which fell back to neutral when ETF history was missing) with actual
rates/credit/vol data.

Data path: the facade ``/macro`` endpoint (reference-data), the single sanctioned
macro source — it serves the same keyless FRED series, cached and kept warm, so
this pipeline no longer collects FRED on its own (decoupling plan §4.8). Fetched
once per process (lru_cache); degrades gracefully per-series on any failure.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from functools import lru_cache
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Enough history for the score's lookbacks: 20-trading-day change (iloc[-21]) on
# daily series and CPI YoY/accel (iloc[-13]/-16, monthly). 480 covers ~22 months
# of daily data and far more than enough monthly points.
MACRO_LIMIT = int(os.environ.get("FRED_MACRO_LIMIT", "480"))

_data = None


def _facade():
    """Lazily import the data-access facade SDK (the single read path)."""
    global _data
    if _data is None:
        pkg = os.environ.get("DATA_ACCESS_PKG", "/Users/x/nimbus-os/services/data-access")
        if pkg not in sys.path:
            sys.path.insert(0, pkg)
        import data_access as _da  # noqa: PLC0415
        _data = _da
    return _data

# series id -> human label
SERIES = {
    "DGS10": "10Y Treasury",
    "DGS2": "2Y Treasury",
    "T10Y2Y": "2s10s curve",
    "DFEDTARU": "Fed funds upper",
    "BAMLH0A0HYM2": "HY OAS",
    "VIXCLS": "VIX",
    "DTWEXBGS": "Broad USD",
    "T10YIE": "10Y breakeven",
}
CPI_SERIES = "CPIAUCSL"  # CPI level -> YoY computed separately


def _num(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def fetch_fred_series(series_id: str, *, timeout: int = 20) -> pd.Series:
    """Date-indexed float series for one FRED id (empty Series on failure).

    Reads through the facade ``/macro``; rows are ``{date, <series>: value}``
    oldest->newest, so the resulting Series is ascending (iloc[-1] = latest).
    """
    try:
        rows = _facade().macro(series_id, limit=MACRO_LIMIT)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.debug("facade macro fetch failed for %s: %s", series_id, exc)
        return pd.Series(dtype="float64")
    if not rows:
        return pd.Series(dtype="float64")
    idx = pd.to_datetime([r.get("date") for r in rows], errors="coerce")
    vals = pd.to_numeric([r.get(series_id) for r in rows], errors="coerce")  # FRED uses '.' for NA
    return pd.Series(vals, index=idx, name=series_id).dropna()


def _band(value: float, breaks: list[tuple[float, float]], default: float) -> float:
    """Map a value through ascending (threshold, score) bands; first match wins."""
    for threshold, score in breaks:
        if value <= threshold:
            return score
    return default


def _credit_score(spread: float | None) -> float | None:
    if spread is None:
        return None
    # HY OAS: tighter = more risk-on.
    return _band(spread, [(3.0, 85), (4.0, 70), (5.0, 55), (6.0, 42), (8.0, 28)], 15)


def _vix_score(vix: float | None) -> float | None:
    if vix is None:
        return None
    return _band(vix, [(13.0, 85), (16.0, 72), (20.0, 58), (25.0, 44), (32.0, 28)], 15)


def _curve_score(curve: float | None) -> float | None:
    if curve is None:
        return None
    # 2s10s: inversion = late-cycle caution.
    return _band(curve, [(-0.5, 35), (0.0, 45), (0.5, 55), (1.5, 65)], 70)


def _policy_signal(latest: dict[str, float]) -> dict[str, Any]:
    """Forward policy read — what the market PRICES, not just current levels.

    rate_path = 2Y yield - fed funds upper: the 2Y is the market's expected average
    funds rate over 2y, so 2Y < funds => cuts priced, 2Y > funds => hikes/higher-
    for-longer. Inflation = CPI YoY + 3-month acceleration. This is the dimension a
    curve level alone can't give (a steepener can be dovish OR hawkish).
    """
    cpi = fetch_fred_series(CPI_SERIES)
    cpi_yoy = cpi_accel = None
    if len(cpi) > 13:
        last = _num(cpi.iloc[-1])
        yr = _num(cpi.iloc[-13])
        if last and yr and yr > 0:
            cpi_yoy = round((last / yr - 1) * 100, 2)
            prev3 = _num(cpi.iloc[-4])
            yr3 = _num(cpi.iloc[-16]) if len(cpi) > 16 else None
            if prev3 and yr3 and yr3 > 0:
                cpi_accel = round(cpi_yoy - (prev3 / yr3 - 1) * 100, 2)

    two_year = latest.get("DGS2")
    funds = latest.get("DFEDTARU")
    rate_path = round(two_year - funds, 2) if (two_year is not None and funds is not None) else None

    # Classify policy stance from priced rate path + inflation.
    stance = "neutral"
    if rate_path is not None:
        if rate_path >= 0.1:
            stance = "hawkish"  # market prices higher-for-longer / hikes
        elif rate_path <= -0.25:
            stance = "dovish"  # cuts priced
    hot_inflation = cpi_yoy is not None and (cpi_yoy >= 3.0 or (cpi_accel or 0) > 0.2)
    if hot_inflation and stance != "dovish":
        stance = "hawkish"

    if stance == "hawkish":
        summary = (
            f"鹰派/higher-for-longer：2Y-funds={rate_path}（市场 price 不降甚至加息），"
            f"CPI YoY={cpi_yoy}%{'（加速）' if (cpi_accel or 0) > 0 else ''}。逆风长久期成长，顺风能源/价值。"
        )
    elif stance == "dovish":
        summary = f"鸽派：2Y-funds={rate_path}（降息已 price），CPI YoY={cpi_yoy}%。利好成长/久期。"
    else:
        summary = f"政策中性：2Y-funds={rate_path}，CPI YoY={cpi_yoy}%。"

    return {
        "stance": stance,
        "rate_path_2y_minus_funds": rate_path,
        "cpi_yoy": cpi_yoy,
        "cpi_accel_3m": cpi_accel,
        "summary": summary,
    }


@lru_cache(maxsize=1)
def get_fred_macro() -> dict[str, Any]:
    """Fetch the FRED gauges and compute a risk-appetite score + regime.

    Returns ``{status, fred_score, regime, as_of, metrics, components}``. When no
    series is reachable, ``status='unavailable'`` and ``fred_score=None`` so callers
    can fall back to the proxy-ETF signal.
    """
    metrics: dict[str, dict[str, Any]] = {}
    latest: dict[str, float] = {}
    as_of: pd.Timestamp | None = None
    for series_id in SERIES:
        series = fetch_fred_series(series_id)
        if series.empty:
            continue
        value = _num(series.iloc[-1])
        if value is None:
            continue
        latest[series_id] = value
        prev = _num(series.iloc[-21]) if len(series) > 21 else None
        metrics[series_id] = {
            "label": SERIES[series_id],
            "value": round(value, 3),
            "change_20d": None if prev is None else round(value - prev, 3),
        }
        ts = pd.to_datetime(series.index[-1])
        if pd.notna(ts) and (as_of is None or ts > as_of):
            as_of = ts

    components = {
        "credit": _credit_score(latest.get("BAMLH0A0HYM2")),
        "vix": _vix_score(latest.get("VIXCLS")),
        "curve": _curve_score(latest.get("T10Y2Y")),
    }
    weights = {"credit": 0.45, "vix": 0.35, "curve": 0.20}
    usable = {k: v for k, v in components.items() if v is not None}
    if not usable:
        return {
            "status": "unavailable",
            "fred_score": None,
            "regime": "neutral",
            "as_of": None,
            "metrics": metrics,
            "components": components,
        }
    total_w = sum(weights[k] for k in usable)
    fred_score = round(sum(usable[k] * weights[k] for k in usable) / total_w, 2)
    if fred_score >= 60:
        regime = "risk_on"
    elif fred_score <= 42:
        regime = "risk_off"
    else:
        regime = "neutral"
    return {
        "status": "ok",
        "fred_score": fred_score,
        "regime": regime,
        "as_of": None if as_of is None else as_of.strftime("%Y-%m-%d"),
        "metrics": metrics,
        "components": {k: (None if v is None else round(v, 2)) for k, v in components.items()},
        "policy": _policy_signal(latest),
    }
