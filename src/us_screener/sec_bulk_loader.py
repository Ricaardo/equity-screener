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

import gc
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

CIK_RE = re.compile(r"CIK(\d+)\.json$")

# us-gaap concept -> our metric (priority order within each tuple). Mirrors
# ah_screener.fundamentals.SEC_FACT_TAGS but read with plain dict access for speed.
_SEC_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
    "gross_profit": ("GrossProfit",),
    "parent_net_profit": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "total_equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
}
_ANNUAL_FORMS = {"10-K", "20-F", "40-F"}


def _latest_annual(gaap: dict[str, Any], tags: tuple[str, ...]) -> tuple[float | None, str]:
    """Latest annual USD value across the concept's tags: (value, end-date)."""
    best: float | None = None
    best_key = ("", "")
    for tag in tags:
        for row in gaap.get(tag, {}).get("units", {}).get("USD", []) or []:
            form = str(row.get("form") or "").upper()
            fp = str(row.get("fp") or "").upper()
            if form not in _ANNUAL_FORMS and fp != "FY":
                continue
            end = str(row.get("end") or "")
            key = (end, str(row.get("filed") or ""))
            val = _num(row.get("val"))
            if val is not None and key > best_key:
                best_key, best = key, val
    return best, best_key[0]


def _annual_history(gaap: dict[str, Any], tags: tuple[str, ...], n: int = 2) -> list[float]:
    """Most-recent ``n`` annual USD values (newest first) across the concept's tags."""
    by_end: dict[str, float] = {}
    for tag in tags:
        for row in gaap.get(tag, {}).get("units", {}).get("USD", []) or []:
            form = str(row.get("form") or "").upper()
            fp = str(row.get("fp") or "").upper()
            if form not in _ANNUAL_FORMS and fp != "FY":
                continue
            end = str(row.get("end") or "")
            val = _num(row.get("val"))
            if end and val is not None:
                by_end[end] = val  # later filing for same end overwrites
    return [by_end[e] for e in sorted(by_end, reverse=True)[:n]]


def _yoy(values: list[float]) -> float | None:
    if len(values) < 2 or values[1] == 0:
        return None
    return round((values[0] - values[1]) / abs(values[1]) * 100, 2)


def _fast_metrics(companyfacts: dict[str, Any]) -> dict[str, Any]:
    """Latest-annual key fundamentals + YoY growth via direct dict reads."""
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    out: dict[str, Any] = {}
    report_end = ""
    for metric, tags in _SEC_TAGS.items():
        val, end = _latest_annual(gaap, tags)
        out[metric] = val
        if end > report_end:
            report_end = end
    out["report_date"] = report_end or None
    out["parent_net_profit_yoy"] = _yoy(_annual_history(gaap, _SEC_TAGS["parent_net_profit"]))
    out["revenue_yoy"] = _yoy(_annual_history(gaap, _SEC_TAGS["revenue"]))
    return out


def _fast_fundamental_score(m: dict[str, Any]) -> float | None:
    """Lightweight 0-100 quality score from ROE / net margin / leverage."""
    equity = m.get("total_equity")
    revenue = m.get("revenue")
    profit = m.get("parent_net_profit")
    assets = m.get("total_assets")
    liabilities = m.get("total_liabilities")
    parts: list[float] = []
    if equity and equity > 0 and profit is not None:
        roe = profit / equity
        parts.append(max(0.0, min(100.0, 50.0 + roe * 250.0)))  # ~20% ROE -> 100
    if revenue and revenue > 0 and profit is not None:
        margin = profit / revenue
        parts.append(max(0.0, min(100.0, 50.0 + margin * 200.0)))
    if assets and assets > 0 and liabilities is not None:
        debt = liabilities / assets
        parts.append(max(0.0, min(100.0, (1.0 - debt) * 100.0)))
    if not parts:
        return None
    return round(sum(parts) / len(parts), 2)


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
        existing_cap = _num(row.get("market_cap"))
        sh = shares.get(symbol)
        # Sina's market cap is authoritative (handles multi-class/holding structures
        # that SEC's single shares-outstanding figure gets wrong, e.g. IBKR). Only
        # derive from SEC shares x price when there's no existing cap.
        if existing_cap and existing_cap > 0:
            market_cap = existing_cap
        elif sh is not None and price is not None and price > 0:
            market_cap = sh * price
        else:
            continue
        new_row = row.copy()
        new_row["market_cap"] = round(market_cap, 2)
        eq = equity.get(symbol)
        ni = net_income.get(symbol)
        if eq and eq > 0:
            new_row["pb"] = round(market_cap / eq, 4)
        if ni and ni > 0 and (_num(row.get("pe_ttm")) is None):
            new_row["pe_ttm"] = round(market_cap / ni, 2)  # keep Sina's PE when present
        rows.append(new_row)
    if not rows:
        return 0
    return int(store.upsert_dataframe("market_snapshots", pd.DataFrame(rows)))


def load_companyfacts_zip(
    store, zip_path: str | Path, *, fill_snapshots: bool = True, limit: int | None = None
) -> dict[str, Any]:
    """Parse companyfacts.zip -> financial_metrics (+ snapshot valuation), fast.

    Uses direct dict reads (latest-annual key concepts + shares) rather than the
    pandas-per-company parser, so the whole archive loads in a couple of minutes.
    """
    from ah_screener.sources.us_client import fetch_sec_company_tickers

    zip_path = Path(zip_path).expanduser()
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    store.init_db()

    ticker_map = fetch_sec_company_tickers()  # ticker -> {cik_str, ticker, title}
    cik_to_info = {int(info["cik_str"]): info for info in ticker_map.values() if info.get("cik_str") is not None}
    snapshot_date = pd.Timestamp.now().normalize().date()

    metric_rows: list[dict[str, Any]] = []
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
            metrics = _fast_metrics(companyfacts)
            sh = _latest_shares(companyfacts)
            if sh:
                shares[ticker] = sh
            if metrics.get("total_equity") is not None or metrics.get("parent_net_profit") is not None:
                matched += 1
                metric_rows.append(
                    {
                        "snapshot_date": snapshot_date,
                        "market": "US",
                        "symbol": ticker,
                        "name": info.get("title") or companyfacts.get("entityName"),
                        "report_date": metrics.get("report_date") or snapshot_date,
                        "report_type": "annual",
                        "revenue": metrics.get("revenue"),
                        "revenue_yoy": metrics.get("revenue_yoy"),
                        "gross_profit": metrics.get("gross_profit"),
                        "parent_net_profit": metrics.get("parent_net_profit"),
                        "net_profit_yoy": metrics.get("parent_net_profit_yoy"),
                        "operating_cashflow": metrics.get("operating_cashflow"),
                        "total_assets": metrics.get("total_assets"),
                        "total_liabilities": metrics.get("total_liabilities"),
                        "total_equity": metrics.get("total_equity"),
                        "fundamental_score": _fast_fundamental_score(metrics),
                        "updated_at": pd.Timestamp.now(),
                    }
                )
            if limit and parsed >= limit:
                break

    result: dict[str, Any] = {"status": "ok", "parsed": parsed, "matched": matched, "shares": len(shares)}
    metrics_df = pd.DataFrame(metric_rows) if metric_rows else None
    if metrics_df is not None:
        metrics_df["report_date"] = pd.to_datetime(metrics_df["report_date"], errors="coerce").dt.date
        result["financial_metrics"] = int(store.upsert_dataframe("financial_metrics", metrics_df))
    if fill_snapshots and shares:
        result["snapshot_valuation"] = _fill_snapshot_valuation(store, shares, metrics_df)
    # Release the parse buffers before the caller moves on to the heavy screen step
    # (backfill runs this in-process), so peak memory stays bounded.
    del metric_rows, metrics_df, shares
    gc.collect()
    return result
