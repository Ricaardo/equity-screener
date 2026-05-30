"""Bulk-load the stooq US daily history ZIP (``d_us_txt.zip``) into daily_prices.

stooq publishes the entire US daily history as one ZIP: ``data/daily/us/<exchange
category>/<...>/<ticker>.us.txt``, each a CSV with columns
``<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>`` and
**split/dividend-adjusted** prices going back decades.

This is the complete, API-free history source: extract once, then a single DuckDB
``read_csv`` over the file glob loads tens of millions of rows in C++ (far faster
than any per-symbol API). It is the right tool for the one-time full backfill; the
daily incremental still comes from the live snapshot/bars path (a stooq ZIP is a
once-a-day download).
"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ADJ_TYPE = "stooq_adj"
SOURCE = "stooq.d_us"


def load_stooq_us_zip(
    store,
    zip_path: str | Path,
    *,
    since: str = "2022-01-01",
    include_etf: bool = True,
    work_dir: str | Path | None = None,
    keep_extracted: bool = False,
) -> dict[str, Any]:
    """Extract ``zip_path`` and bulk-insert US daily bars into ``daily_prices``.

    ``since`` trims history to what the screener needs (technicals/heat/52w), keeping
    the table lean instead of loading 40 years. Adjusted bars land under
    ``source='stooq.d_us'`` / ``adj_type='stooq_adj'`` so they never collide with the
    live (raw) snapshot/bars rows.
    """
    zip_path = Path(zip_path).expanduser()
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    store.init_db()
    created_tmp = work_dir is None
    extract_root = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="stooq_"))
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_root)

        glob = str(extract_root / "data" / "daily" / "us" / "**" / "*.txt")
        etf_clause = "" if include_etf else "AND lower(filename) NOT LIKE '%etfs%'"
        # DuckDB reads the whole file glob in parallel; transform inline and upsert.
        sql = f"""
        INSERT OR REPLACE INTO daily_prices
            (market, symbol, trade_date, open, high, low, close, volume, amount,
             adj_type, source, updated_at)
        SELECT
            'US' AS market,
            upper(regexp_replace("<TICKER>", '\\.US$', '')) AS symbol,
            strptime(CAST("<DATE>" AS VARCHAR), '%Y%m%d')::DATE AS trade_date,
            "<OPEN>"::DOUBLE AS open,
            "<HIGH>"::DOUBLE AS high,
            "<LOW>"::DOUBLE AS low,
            "<CLOSE>"::DOUBLE AS close,
            "<VOL>"::DOUBLE AS volume,
            ("<CLOSE>"::DOUBLE * "<VOL>"::DOUBLE) AS amount,
            '{ADJ_TYPE}' AS adj_type,
            '{SOURCE}' AS source,
            now() AS updated_at
        FROM read_csv(
            ?, header=true, filename=true, union_by_name=true,
            types={{'<DATE>': 'BIGINT', '<OPEN>': 'DOUBLE', '<HIGH>': 'DOUBLE',
                    '<LOW>': 'DOUBLE', '<CLOSE>': 'DOUBLE', '<VOL>': 'DOUBLE'}}
        )
        WHERE "<CLOSE>" IS NOT NULL
          AND strptime(CAST("<DATE>" AS VARCHAR), '%Y%m%d')::DATE >= DATE '{since}'
          {etf_clause}
        """
        with store.connect() as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM daily_prices WHERE source = ?", [SOURCE]
            ).fetchone()[0]
            conn.execute(sql, [glob])
            after = conn.execute(
                "SELECT COUNT(*) FROM daily_prices WHERE source = ?", [SOURCE]
            ).fetchone()[0]
            symbols = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM daily_prices WHERE source = ?", [SOURCE]
            ).fetchone()[0]

        return {
            "status": "ok",
            "source": SOURCE,
            "since": since,
            "rows_inserted": int(after - before),
            "rows_total": int(after),
            "symbols": int(symbols),
        }
    finally:
        if created_tmp and not keep_extracted:
            import shutil

            shutil.rmtree(extract_root, ignore_errors=True)


def stooq_today() -> str:
    return date.today().isoformat()
