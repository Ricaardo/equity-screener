"""Sector / industry classification from FinanceDatabase.

FinanceDatabase bundles static metadata (sector, industry_group, industry) for
~300k assets with no API calls or rate limits. We intersect it with our actual
localized US universe (so cross-listing noise is dropped) and write
``sector`` / ``industry`` tags into ``company_tags`` — plus a few high-confidence
industry -> concept-board derivations that extend the curated seed to the whole
market.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from us_screener.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

SECTOR_TAG = "sector"
INDUSTRY_TAG = "industry"
CONCEPT_TAG = "concept_board"
SOURCE = "financedatabase"

# Local cache so a (possibly offline) subprocess backfill never has to cold-download
# the FinanceDatabase dataset — which is what silently produced 0 tags before.
FD_CACHE_PATH = PROJECT_ROOT / "data" / "us_fd_equities.parquet"

# High-confidence industry-substring -> concept board. Lower-cased substring match.
_INDUSTRY_BOARD_MAP: dict[str, str] = {
    "semiconductor": "AI算力",
    "aerospace": "太空",
    "uranium": "核电",
}


def _load_fd_live() -> pd.DataFrame:
    try:
        import financedatabase as fd
    except ImportError:
        logger.warning("financedatabase not installed; skipping FD classification")
        return pd.DataFrame()
    try:
        frame = fd.Equities().select(country="United States")
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.warning("financedatabase select failed: %s", exc)
        return pd.DataFrame()
    if frame is None or frame.empty:
        return pd.DataFrame()
    keep = [c for c in ("sector", "industry_group", "industry", "summary") if c in frame.columns]
    out = frame[keep].copy()
    out.index = out.index.astype(str).str.strip().str.upper()
    return out[~out.index.duplicated(keep="first")]


def export_fd_cache(path: Path | None = None) -> dict[str, Any]:
    """Fetch FD US equities once (live) and persist to a local parquet cache."""
    frame = _load_fd_live()
    if frame.empty:
        return {"status": "skipped", "rows": 0}
    target = Path(path) if path else FD_CACHE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.reset_index(names="symbol").to_parquet(target, index=False)
    return {"status": "ok", "rows": int(len(frame)), "path": str(target)}


def load_fd_us_equities() -> pd.DataFrame:
    """symbol-indexed US equities with sector/industry.

    Prefers the local parquet cache (no network); falls back to a live FD fetch and
    writes the cache for next time. Empty frame only if both are unavailable.
    """
    if FD_CACHE_PATH.exists():
        try:
            cached = pd.read_parquet(FD_CACHE_PATH).set_index("symbol")
            cached.index = cached.index.astype(str).str.strip().str.upper()
            return cached[~cached.index.duplicated(keep="first")]
        except Exception as exc:  # noqa: BLE001 — fall through to live
            logger.warning("FD cache read failed (%s); falling back to live", exc)
    frame = _load_fd_live()
    if not frame.empty:
        try:
            export_fd_cache()
        except Exception as exc:  # noqa: BLE001 — cache write is best-effort
            logger.debug("FD cache write failed: %s", exc)
    return frame


def _board_for_industry(industry: str) -> str | None:
    text = industry.lower()
    for needle, board in _INDUSTRY_BOARD_MAP.items():
        if needle in text:
            return board
    return None


def tag_fd_classification(store) -> dict[str, Any]:
    """Write sector/industry (+derived concept_board) tags for our US universe."""
    fd_data = load_fd_us_equities()
    if fd_data.empty:
        return {"status": "skipped", "sector_tags": 0, "industry_tags": 0, "board_tags": 0}

    securities = store.query_df(
        "SELECT symbol FROM securities WHERE market='US' AND COALESCE(asset_type,'stock') <> 'etf'"
    )
    if securities.empty:
        return {"status": "empty", "sector_tags": 0, "industry_tags": 0, "board_tags": 0}

    our_symbols = {str(s).strip().upper() for s in securities["symbol"].tolist()}
    now = pd.Timestamp.now()
    rows: list[dict[str, Any]] = []
    sector_n = industry_n = board_n = 0
    for symbol in our_symbols & set(fd_data.index):
        meta = fd_data.loc[symbol]
        sector = str(meta.get("sector") or "").strip()
        industry = str(meta.get("industry") or meta.get("industry_group") or "").strip()
        if sector and sector.lower() != "nan":
            rows.append(_tag(symbol, SECTOR_TAG, sector, "fd", now))
            sector_n += 1
        if industry and industry.lower() != "nan":
            rows.append(_tag(symbol, INDUSTRY_TAG, industry, "fd", now))
            industry_n += 1
            board = _board_for_industry(industry)
            if board:
                rows.append(_tag(symbol, CONCEPT_TAG, board, "fd_industry", now))
                board_n += 1
    if rows:
        store.upsert_dataframe("company_tags", pd.DataFrame(rows))
    return {
        "status": "ok",
        "sector_tags": sector_n,
        "industry_tags": industry_n,
        "board_tags": board_n,
        "matched_symbols": len(our_symbols & set(fd_data.index)),
    }


def _tag(symbol: str, tag_type: str, tag_name: str, evidence: str, now: pd.Timestamp) -> dict[str, Any]:
    return {
        "market": "US",
        "symbol": symbol,
        "tag_type": tag_type,
        "tag_name": tag_name,
        "evidence_level": evidence,
        "source": SOURCE,
        "updated_at": now,
    }


def sector_industry_map(store) -> dict[str, dict[str, str]]:
    """symbol -> {sector, industry} from stored FD tags."""
    tags = store.query_df(
        "SELECT symbol, tag_type, tag_name FROM company_tags "
        "WHERE market='US' AND tag_type IN ('sector','industry') AND source=?",
        [SOURCE],
    )
    out: dict[str, dict[str, str]] = {}
    if tags.empty:
        return out
    for _, row in tags.iterrows():
        out.setdefault(str(row["symbol"]).strip().upper(), {})[str(row["tag_type"])] = str(row["tag_name"])
    return out
