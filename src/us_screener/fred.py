"""Real macro inputs from FRED (no API key required).

FRED exposes every series as a no-key CSV at
``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>``. We pull a handful
of risk-appetite gauges — high-yield credit spread, the 2s10s curve, VIX, the broad
dollar and the 10Y level — and fold them into a 0-100 risk-appetite score. This
replaces the previous proxy-ETF-momentum-only macro signal (which fell back to
neutral when ETF history was missing) with actual rates/credit/vol data.

Fetched once per process (lru_cache); degrades gracefully per-series on any failure.
"""

from __future__ import annotations

import io
import logging
import math
from functools import lru_cache
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# series id -> human label
SERIES = {
    "DGS10": "10Y Treasury",
    "T10Y2Y": "2s10s curve",
    "BAMLH0A0HYM2": "HY OAS",
    "VIXCLS": "VIX",
    "DTWEXBGS": "Broad USD",
    "T10YIE": "10Y breakeven",
}


def _num(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def fetch_fred_series(series_id: str, *, timeout: int = 20) -> pd.Series:
    """Date-indexed float series for one FRED id (empty Series on failure)."""
    import requests

    try:
        response = requests.get(FRED_CSV_URL.format(series=series_id), timeout=timeout)
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.debug("FRED fetch failed for %s: %s", series_id, exc)
        return pd.Series(dtype="float64")
    if frame.shape[1] < 2:
        return pd.Series(dtype="float64")
    date_col, value_col = frame.columns[0], frame.columns[1]
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")  # FRED uses '.' for NA
    frame = frame.dropna()
    return pd.Series(frame[value_col].values, index=frame[date_col].values, name=series_id)


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
    }
