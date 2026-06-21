"""Short-selling pressure from FINRA's free daily short-volume files.

FINRA publishes a daily consolidated short-volume file per trading day
(``CNMSshvol<YYYYMMDD>.txt``, pipe-delimited). The short-volume ratio
(short / total) averaged over recent days is a free, bulk sentiment / squeeze
gauge: a persistently elevated ratio means heavy short pressure — bearish if price
is breaking down, squeeze fuel if the name is leading (high RS). Cached as a
``company_tags`` row so scoring/report read it without re-fetching.
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
TAG_TYPE = "short_ratio"
SOURCE = "finra.regsho"
# data-gateway centralizes the per-day FINRA file fetch (fetched once, cached by
# date) so this bulk pipeline and the nimbus short-interest skill share one pull.
DATA_GATEWAY = Path(
    os.environ.get("DATA_GATEWAY_BIN", "/Users/x/nimbus-os/services/data-gateway/bin/data-gateway")
)


def _fetch_finra_short_via_gateway(date_yyyymmdd: str) -> pd.DataFrame | None:
    """One day's rows via data-gateway (shared cache). None if gateway unusable."""
    if not DATA_GATEWAY.exists():
        return None
    try:
        proc = subprocess.run(
            [str(DATA_GATEWAY), "fetch", "finra-day", "--date", date_yyyymmdd],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout).get("data", {})
        if not data.get("published"):
            return pd.DataFrame()  # holiday / not published — a definitive empty
        rows = data.get("rows") or {}
        if not rows:
            return pd.DataFrame()
        out = pd.DataFrame(
            [(s, v[0], v[1]) for s, v in rows.items()],
            columns=["symbol", "short_vol", "total_vol"],
        )
        out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
        return out.dropna(subset=["symbol", "total_vol"])
    except Exception as exc:  # noqa: BLE001 — fall back to direct fetch
        logger.debug("data-gateway finra-day failed for %s: %s", date_yyyymmdd, exc)
        return None


def fetch_finra_short(date_yyyymmdd: str, *, timeout: int = 25) -> pd.DataFrame:
    """symbol / short_vol / total_vol for one trading day (empty on failure)."""
    gated = _fetch_finra_short_via_gateway(date_yyyymmdd)
    if gated is not None:
        return gated

    import requests

    try:
        response = requests.get(FINRA_URL.format(date=date_yyyymmdd), timeout=timeout)
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text), sep="|")
    except Exception as exc:  # noqa: BLE001 — holiday / missing file, skip
        logger.debug("FINRA short fetch failed for %s: %s", date_yyyymmdd, exc)
        return pd.DataFrame()
    if not {"Symbol", "ShortVolume", "TotalVolume"} <= set(frame.columns):
        return pd.DataFrame()
    out = frame[["Symbol", "ShortVolume", "TotalVolume"]].copy()
    out.columns = ["symbol", "short_vol", "total_vol"]
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["short_vol"] = pd.to_numeric(out["short_vol"], errors="coerce")
    out["total_vol"] = pd.to_numeric(out["total_vol"], errors="coerce")
    return out.dropna(subset=["symbol", "total_vol"])


def _recent_trading_dates(store, lookback_days: int) -> list[str]:
    dates = store.query_df(
        "SELECT DISTINCT trade_date FROM daily_prices WHERE market='US' ORDER BY trade_date DESC LIMIT ?",
        [int(lookback_days)],
    )
    if dates.empty:
        return []
    return [pd.to_datetime(d).strftime("%Y%m%d") for d in dates["trade_date"].tolist()]


def compute_short_ratio(store, *, lookback_days: int = 8) -> pd.DataFrame:
    """Per-symbol short-volume ratio (short/total) averaged over recent trading days."""
    frames = []
    for date_str in _recent_trading_dates(store, lookback_days):
        day = fetch_finra_short(date_str)
        if not day.empty:
            frames.append(day)
    if not frames:
        return pd.DataFrame(columns=["market", "symbol", "short_ratio", "short_days"])
    allrows = pd.concat(frames, ignore_index=True)
    agg = allrows.groupby("symbol").agg(short=("short_vol", "sum"), total=("total_vol", "sum"),
                                        days=("short_vol", "size")).reset_index()
    agg = agg[agg["total"] > 0]
    agg["market"] = "US"
    agg["short_ratio"] = (agg["short"] / agg["total"]).round(4)
    agg["short_days"] = agg["days"].astype(int)
    return agg[["market", "symbol", "short_ratio", "short_days"]]


def tag_short_interest(store, *, lookback_days: int = 8) -> dict[str, Any]:
    """Refresh short-ratio tags in company_tags (one per symbol)."""
    store.init_db()
    ratios = compute_short_ratio(store, lookback_days=lookback_days)
    if ratios.empty:
        return {"status": "empty", "tagged": 0}
    now = pd.Timestamp.now()
    rows = [
        {
            "market": "US",
            "symbol": row["symbol"],
            "tag_type": TAG_TYPE,
            "tag_name": f"{row['short_ratio']:.4f}",
            "evidence_level": str(int(row["short_days"])),
            "source": SOURCE,
            "updated_at": now,
        }
        for _, row in ratios.iterrows()
    ]
    store.execute("DELETE FROM company_tags WHERE market='US' AND tag_type=? AND source=?", [TAG_TYPE, SOURCE])
    store.upsert_dataframe("company_tags", pd.DataFrame(rows))
    return {"status": "ok", "tagged": len(rows)}


def short_ratio_map(store) -> dict[str, float]:
    """symbol -> short-volume ratio from stored tags."""
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
