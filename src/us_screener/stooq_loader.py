"""Bulk-load stooq daily-history ZIPs (``d_us_txt.zip`` / ``d_world_txt.zip`` / per
region) into ``daily_prices`` — any market, one local file, no API.

Each stooq ``*.us.txt`` (or ``*.jp.txt`` ...) CSV carries
``<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>`` with
**split/dividend-adjusted** prices. The ``<TICKER>`` suffix (``AAPL.US`` / ``7203.JP``
/ ``0700.HK``) is the authoritative market signal, so a single DuckDB ``read_csv``
over the whole file glob localizes every market in the archive at once.

This is the one-time / periodic full-history base. Daily increments come from the
live (adjusted) bars path; a stooq ZIP is a once-a-day download.
"""

from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9]+$")


def _safe_since(since: str) -> str:
    """Validate the YYYY-MM-DD date literal (interpolated into SQL)."""
    if not _DATE_RE.match(since):
        raise ValueError(f"invalid 'since' (expected YYYY-MM-DD): {since!r}")
    datetime.strptime(since, "%Y-%m-%d")  # raises on impossible dates
    return since


def _safe_code(code: str) -> str:
    """Validate a market/suffix code (interpolated into SQL); alphanumerics only."""
    if not _CODE_RE.match(code or ""):
        raise ValueError(f"invalid market/suffix code: {code!r}")
    return code

ADJ_TYPE = "stooq_adj"
SOURCE = "stooq.d"

# stooq ticker suffix -> our market code. Unknown suffixes fall through to the
# upper-cased suffix itself (e.g. '.DE' -> 'DE'), so new regions Just Work.
DEFAULT_MARKET_MAP = {"US": "US", "HK": "HK", "JP": "JP"}


def _market_case(market_map: dict[str, str]) -> str:
    """SQL CASE mapping the ticker suffix to our market code (else the suffix)."""
    whens = " ".join(
        f"WHEN '{_safe_code(suffix).upper()}' THEN '{_safe_code(market).upper()}'"
        for suffix, market in market_map.items()
    )
    return f"CASE _suffix {whens} ELSE _suffix END"


def load_stooq_zip(
    store,
    zip_path: str | Path,
    *,
    since: str = "2022-01-01",
    include_etf: bool = True,
    markets: list[str] | None = None,
    market_map: dict[str, str] | None = None,
    delete_zip: bool = False,
    work_dir: str | Path | None = None,
    keep_extracted: bool = False,
) -> dict[str, Any]:
    """Extract a stooq ZIP and bulk-insert daily bars for all (or selected) markets.

    ``markets`` (our codes, e.g. ``['US','HK']``) filters which markets to keep;
    None keeps everything in the archive. ``delete_zip=True`` removes the source ZIP
    after a successful load (the data now lives in DuckDB).
    """
    zip_path = Path(zip_path).expanduser()
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    store.init_db()
    since = _safe_since(since)
    market_map = {**DEFAULT_MARKET_MAP, **(market_map or {})}
    created_tmp = work_dir is None
    extract_root = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="stooq_"))
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_root)

        glob = str(extract_root / "data" / "daily" / "**" / "*.txt")
        etf_clause = "" if include_etf else "AND lower(filename) NOT LIKE '%etfs%'"
        market_filter = ""
        if markets:
            allowed = ", ".join(f"'{_safe_code(m).upper()}'" for m in markets)
            market_filter = f"AND _market IN ({allowed})"

        # Derive market + symbol from the ticker suffix; one parallel read_csv.
        sql = f"""
        INSERT OR REPLACE INTO daily_prices
            (market, symbol, trade_date, open, high, low, close, volume, amount,
             adj_type, source, updated_at)
        WITH parsed AS (
            SELECT
                upper(regexp_extract("<TICKER>", '\\.([A-Za-z]+)$', 1)) AS _suffix,
                upper(regexp_replace("<TICKER>", '\\.[A-Za-z]+$', '')) AS symbol,
                strptime(CAST("<DATE>" AS VARCHAR), '%Y%m%d')::DATE AS trade_date,
                "<OPEN>"::DOUBLE AS open, "<HIGH>"::DOUBLE AS high,
                "<LOW>"::DOUBLE AS low, "<CLOSE>"::DOUBLE AS close, "<VOL>"::DOUBLE AS volume
            FROM read_csv(
                ?, header=true, filename=true, union_by_name=true,
                types={{'<DATE>': 'BIGINT', '<OPEN>': 'DOUBLE', '<HIGH>': 'DOUBLE',
                        '<LOW>': 'DOUBLE', '<CLOSE>': 'DOUBLE', '<VOL>': 'DOUBLE'}}
            )
            WHERE "<CLOSE>" IS NOT NULL {etf_clause}
        )
        SELECT
            {_market_case(market_map)} AS market,
            symbol, trade_date, open, high, low, close, volume,
            (close * volume) AS amount,
            '{ADJ_TYPE}' AS adj_type, '{SOURCE}' AS source, now() AS updated_at
        FROM (SELECT *, {_market_case(market_map)} AS _market FROM parsed)
        WHERE _suffix <> '' AND trade_date >= DATE '{since}' {market_filter}
        """
        with store.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM daily_prices WHERE source = ?", [SOURCE]).fetchone()[0]
            conn.execute(sql, [glob])
            after = conn.execute("SELECT COUNT(*) FROM daily_prices WHERE source = ?", [SOURCE]).fetchone()[0]
            by_market = conn.execute(
                "SELECT market, COUNT(DISTINCT symbol) FROM daily_prices WHERE source = ? GROUP BY market",
                [SOURCE],
            ).fetchall()

        result = {
            "status": "ok",
            "source": SOURCE,
            "since": since,
            "rows_inserted": int(after - before),
            "rows_total": int(after),
            "symbols_by_market": {row[0]: int(row[1]) for row in by_market},
        }
        if delete_zip:
            zip_path.unlink(missing_ok=True)
            result["zip_deleted"] = str(zip_path)
        return result
    finally:
        if created_tmp and not keep_extracted:
            import shutil

            shutil.rmtree(extract_root, ignore_errors=True)


def load_stooq_us_zip(store, zip_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Backwards-compatible US-only entry point (markets=['US'])."""
    kwargs.setdefault("markets", ["US"])
    return load_stooq_zip(store, zip_path, **kwargs)


_ADJUSTED_SOURCES = ("stooq.d", "alpaca.iex")


def consolidate_history_sources(store) -> dict[str, Any]:
    """Make free-path history single-adjustment.

    1. Migrate the old per-market ``stooq.d_us`` tag to the unified ``stooq.d``.
    2. Drop raw/qfq cruft (akshare/futu rows left over from earlier runs) so the
       technical/heat layer never mixes adjustment bases. Adjusted sources
       (``stooq.d`` history base + ``alpaca.iex`` increments) are kept.
    """
    store.init_db()
    with store.connect() as conn:
        # Legacy rename only fires if old rows exist (fresh backfills already write
        # 'stooq.d', so this is a cheap no-op for them).
        legacy = conn.execute("SELECT COUNT(*) FROM daily_prices WHERE source='stooq.d_us'").fetchone()[0]
        if legacy:
            conn.execute("UPDATE daily_prices SET source='stooq.d' WHERE source='stooq.d_us'")
        before = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        keep = ", ".join(f"'{s}'" for s in _ADJUSTED_SOURCES)
        # 1. Drop non-adjusted cruft (akshare/futu raw/qfq) everywhere.
        conn.execute(f"DELETE FROM daily_prices WHERE market='US' AND source NOT IN ({keep})")
        # 2. Single source per symbol: where a symbol has the stooq base, drop any
        #    other (e.g. alpaca) rows for it so MA/RSI never splice adjustment bases.
        conn.execute(
            "DELETE FROM daily_prices WHERE market='US' AND source <> 'stooq.d' "
            "AND (market, symbol) IN (SELECT market, symbol FROM daily_prices WHERE source='stooq.d')"
        )
        after = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        remaining = conn.execute(
            "SELECT source, COUNT(*) FROM daily_prices WHERE market='US' GROUP BY source"
        ).fetchall()
    return {
        "status": "ok",
        "rows_deleted": int(before - after),
        "remaining_sources": {row[0]: int(row[1]) for row in remaining},
    }


def stooq_today() -> str:
    return date.today().isoformat()
