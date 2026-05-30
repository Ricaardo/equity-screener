"""Upcoming earnings dates for the US pre-market report (free, no key).

Nasdaq's calendar API returns every company reporting on a given date:
``https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`` (one call per day,
needs a browser User-Agent). We sweep the next ~2 weeks of business days, keep the
nearest upcoming date per symbol, and cache it as a ``company_tags`` row so the
report can flag names reporting soon (earnings = single-name gap risk, the classic
pre-market caution).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings?date={date}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36",
    "Accept": "application/json",
}
TAG_TYPE = "earnings_date"
SOURCE = "nasdaq.earnings"


def _business_days(days_ahead: int) -> list[date]:
    out: list[date] = []
    day = date.today()
    while len(out) < days_ahead:
        day += timedelta(days=1)
        if day.weekday() < 5:  # Mon-Fri
            out.append(day)
    return out


def fetch_earnings_calendar(days_ahead: int = 10, *, timeout: int = 20) -> pd.DataFrame:
    """symbol / earnings_date / when over the next ``days_ahead`` business days."""
    import requests

    rows: list[dict[str, Any]] = []
    for day in _business_days(days_ahead):
        iso = day.isoformat()
        try:
            response = requests.get(NASDAQ_EARNINGS_URL.format(date=iso), headers=_HEADERS, timeout=timeout)
            response.raise_for_status()
            data = (response.json() or {}).get("data") or {}
            day_rows = data.get("rows") or []
        except Exception as exc:  # noqa: BLE001 — free source, skip flaky days
            logger.debug("nasdaq earnings fetch failed for %s: %s", iso, exc)
            continue
        for item in day_rows:
            symbol = str(item.get("symbol") or "").strip().upper()
            if symbol:
                rows.append({"symbol": symbol, "earnings_date": iso, "when": str(item.get("time") or "")})
    if not rows:
        return pd.DataFrame(columns=["symbol", "earnings_date", "when"])
    frame = pd.DataFrame(rows).sort_values(["symbol", "earnings_date"])
    return frame.drop_duplicates("symbol", keep="first")  # nearest upcoming per symbol


def tag_earnings(store, *, days_ahead: int = 10) -> dict[str, Any]:
    """Refresh ``earnings_date`` tags (nearest upcoming per symbol)."""
    store.init_db()
    calendar = fetch_earnings_calendar(days_ahead=days_ahead)
    if calendar.empty:
        return {"status": "empty", "tagged": 0}
    now = pd.Timestamp.now()
    rows = [
        {
            "market": "US",
            "symbol": row["symbol"],
            "tag_type": TAG_TYPE,
            "tag_name": row["earnings_date"],
            "evidence_level": row["when"] or "scheduled",
            "source": SOURCE,
            "updated_at": now,
        }
        for _, row in calendar.iterrows()
    ]
    # Replace stale dates: clear prior earnings tags, then write the fresh nearest ones.
    store.execute("DELETE FROM company_tags WHERE market='US' AND tag_type=? AND source=?", [TAG_TYPE, SOURCE])
    store.upsert_dataframe("company_tags", pd.DataFrame(rows))
    return {"status": "ok", "tagged": len(rows)}


def earnings_map(store) -> dict[str, dict[str, str]]:
    """symbol -> {date, when} for the nearest upcoming earnings."""
    tags = store.query_df(
        "SELECT symbol, tag_name, evidence_level FROM company_tags "
        "WHERE market='US' AND tag_type=? AND source=?",
        [TAG_TYPE, SOURCE],
    )
    out: dict[str, dict[str, str]] = {}
    if tags.empty:
        return out
    for _, row in tags.iterrows():
        out[str(row["symbol"]).strip().upper()] = {
            "date": str(row["tag_name"]),
            "when": str(row.get("evidence_level") or ""),
        }
    return out
