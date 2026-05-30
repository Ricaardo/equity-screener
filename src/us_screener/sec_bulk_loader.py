"""Bulk-load SEC ``companyfacts.zip`` into fundamentals + snapshot valuation.

SEC publishes every company's XBRL facts as one ZIP (~20k ``CIK##########.json``).
Instead of a per-symbol companyfacts API call, parse the local archive once and
reuse the *same* tested parser the per-symbol path uses
(``ah_screener.fundamentals._us_metric_row``) to build ``financial_metrics`` rows
for the whole market — then fill ``market_cap`` / ``pb`` / ``pe_ttm`` on the latest
snapshots from shares-outstanding × price. This closes the PB gap (the free path had
PB for only ~22 names) without thousands of API calls.
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

CIK_RE = re.compile(r"CIK(\d+)\.json$")


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _latest_shares(companyfacts: dict[str, Any]) -> float | None:
    """Latest common shares outstanding from the dei facts."""
    rows = (
        companyfacts.get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .get("shares", [])
    )
    best = None
    best_end = ""
    for row in rows:
        end = str(row.get("end") or "")
        val = _num(row.get("val"))
        if val is not None and val > 0 and end >= best_end:
            best_end, best = end, val
    return best


def _fill_snapshot_valuation(store, shares: dict[str, float], metrics_df: pd.DataFrame | None) -> int:
    """market_cap = shares x last_price; pb = mcap/equity; pe = mcap/net_income."""
    snap = store.query_df(
        "SELECT * FROM market_snapshots WHERE market='US' AND COALESCE(asset_type,'stock')<>'etf'"
    )
    if snap.empty:
        return 0
    snap["trade_date"] = pd.to_datetime(snap["trade_date"], errors="coerce")
    latest = snap.sort_values("trade_date").drop_duplicates(["market", "symbol"], keep="last")
    equity: dict[str, float] = {}
    net_income: dict[str, float] = {}
    if metrics_df is not None and not metrics_df.empty:
        md = metrics_df.dropna(subset=["symbol"]).drop_duplicates("symbol", keep="last").set_index("symbol")
        if "total_equity" in md:
            equity = {str(k).upper(): v for k, v in md["total_equity"].dropna().to_dict().items()}
        if "parent_net_profit" in md:
            net_income = {str(k).upper(): v for k, v in md["parent_net_profit"].dropna().to_dict().items()}

    rows: list[pd.Series] = []
    for _, row in latest.iterrows():
        symbol = str(row["symbol"]).strip().upper()
        price = _num(row.get("last_price"))
        sh = shares.get(symbol)
        if sh is None or price is None or price <= 0:
            continue
        market_cap = sh * price
        new_row = row.copy()
        new_row["market_cap"] = market_cap
        eq = equity.get(symbol)
        ni = net_income.get(symbol)
        if eq and eq > 0:
            new_row["pb"] = market_cap / eq
        if ni and ni > 0:
            new_row["pe_ttm"] = market_cap / ni
        rows.append(new_row)
    if not rows:
        return 0
    return int(store.upsert_dataframe("market_snapshots", pd.DataFrame(rows)))


def load_companyfacts_zip(
    store, zip_path: str | Path, *, fill_snapshots: bool = True, limit: int | None = None
) -> dict[str, Any]:
    """Parse companyfacts.zip -> financial_metrics (+ snapshot valuation)."""
    from ah_screener.fundamentals import _us_metric_row
    from ah_screener.sources.us_client import fetch_sec_company_tickers

    zip_path = Path(zip_path).expanduser()
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    store.init_db()

    ticker_map = fetch_sec_company_tickers()  # ticker -> {cik_str, ticker, title}
    cik_to_info = {int(info["cik_str"]): info for info in ticker_map.values() if info.get("cik_str") is not None}
    snapshot_date = pd.Timestamp.now().normalize()

    metric_frames: list[pd.DataFrame] = []
    shares: dict[str, float] = {}
    parsed = matched = 0
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            cik_match = CIK_RE.search(name)
            if not cik_match:
                continue
            info = cik_to_info.get(int(cik_match.group(1)))
            if not info:  # no common-stock ticker (fund/trust) — skip
                continue
            try:
                companyfacts = json.loads(archive.read(name))
            except Exception:  # noqa: BLE001 — skip a malformed member
                continue
            parsed += 1
            ticker = str(info["ticker"]).strip().upper()
            meta = {"title": info.get("title") or companyfacts.get("entityName"), "ticker": ticker, "cik_str": info["cik_str"]}
            try:
                row = _us_metric_row(ticker, meta, companyfacts, snapshot_date)
            except Exception as exc:  # noqa: BLE001 — one bad filing shouldn't abort
                logger.debug("us_metric_row failed for %s: %s", ticker, exc)
                row = pd.DataFrame()
            if not row.empty:
                metric_frames.append(row)
                matched += 1
            sh = _latest_shares(companyfacts)
            if sh:
                shares[ticker] = sh
            if limit and parsed >= limit:
                break

    result: dict[str, Any] = {"status": "ok", "parsed": parsed, "matched": matched, "shares": len(shares)}
    metrics_df = pd.concat(metric_frames, ignore_index=True) if metric_frames else None
    if metrics_df is not None:
        result["financial_metrics"] = int(store.upsert_dataframe("financial_metrics", metrics_df))
    if fill_snapshots and shares:
        result["snapshot_valuation"] = _fill_snapshot_valuation(store, shares, metrics_df)
    return result
