"""Best-effort forward estimates (forward PE + analyst recommendation trend).

The free/bulk parts of the screener give *trailing* fundamentals; true forward
analyst estimates / revisions are not available free in bulk. This is an optional,
per-symbol yfinance overlay for the top-liquid names — imported lazily, rate-limit
aware, and it skips cleanly so the screen never depends on it. PEG (from SEC growth,
in scoring) is the always-available growth-adjusted-valuation factor; this adds the
analyst-expectation colour on top where Yahoo is reachable.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

TAG_TYPE = "forward_pe"
SOURCE = "yfinance.forward"


def _num(value: object) -> float | None:
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(n) or math.isinf(n) or n == 0 else n


def _is_rate_limit(exc: Exception) -> bool:
    return "ratelimit" in type(exc).__name__.lower() or "too many requests" in str(exc).lower()


def _fetch_forward(symbol: str) -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker(symbol).info or {}
    return {
        "forward_pe": _num(info.get("forwardPE")),
        "recommendation_mean": _num(info.get("recommendationMean")),  # 1=strong buy .. 5=strong sell
        "analysts": _num(info.get("numberOfAnalystOpinions")),
    }


def enrich_forward_estimates(store, *, limit: int = 300, only_missing: bool = True) -> dict[str, Any]:
    """Tag top-liquid US names with forward PE + analyst recommendation (best-effort)."""
    snaps = store.query_df(
        """
        SELECT symbol, amount FROM market_snapshots
        WHERE market='US' AND COALESCE(asset_type,'stock')<>'etf' AND amount IS NOT NULL
        ORDER BY amount DESC LIMIT ?
        """,
        [int(limit)],
    )
    if snaps.empty:
        return {"status": "empty", "updated": 0}
    if only_missing:
        have = store.query_df(
            "SELECT DISTINCT symbol FROM company_tags WHERE market='US' AND tag_type=? AND source=?",
            [TAG_TYPE, SOURCE],
        )
        done = set(have["symbol"].astype(str).str.upper()) if not have.empty else set()
        snaps = snaps[~snaps["symbol"].astype(str).str.upper().isin(done)]

    rows: list[dict[str, Any]] = []
    rate_limited = False
    now = pd.Timestamp.now()
    for symbol in snaps["symbol"].astype(str).str.upper():
        try:
            data = _fetch_forward(symbol)
        except ImportError:
            return {
                "status": "skipped",
                "reason": "yfinance not installed",
                "updated": 0,
                "rate_limited": False,
            }
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            if _is_rate_limit(exc):
                rate_limited = True
                break
            continue
        if not data or data.get("forward_pe") is None:
            continue
        rows.append(
            {
                "market": "US", "symbol": symbol, "tag_type": TAG_TYPE,
                "tag_name": f"{data['forward_pe']:.2f}",
                "evidence_level": "" if data.get("recommendation_mean") is None else f"rec={data['recommendation_mean']}",
                "source": SOURCE, "updated_at": now,
            }
        )
    if not rows:
        return {"status": "ok", "updated": 0, "rate_limited": rate_limited}
    store.upsert_dataframe("company_tags", pd.DataFrame(rows))
    return {"status": "ok", "updated": len(rows), "rate_limited": rate_limited}


def forward_pe_map(store) -> dict[str, float]:
    tags = store.query_df(
        "SELECT symbol, tag_name FROM company_tags WHERE market='US' AND tag_type=? AND source=?",
        [TAG_TYPE, SOURCE],
    )
    out: dict[str, float] = {}
    if tags.empty:
        return out
    for _, row in tags.iterrows():
        try:
            out[str(row["symbol"]).strip().upper()] = float(row["tag_name"])
        except (TypeError, ValueError):
            continue
    return out
