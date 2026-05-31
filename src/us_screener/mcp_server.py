"""Optional MCP server for the US screener."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from ah_screener.db import get_store
from us_screener.config import get_us_config, use_us_database
from us_screener.llm_opinion import generate_us_llm_opinion
from us_screener.reporting_us import generate_us_premarket_report
from us_screener.scoring_us import run_us_screen


def _clean(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if np.isnan(number) or np.isinf(number) else number
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, (str, int, list, dict)) else str(value)


def _rows(df: pd.DataFrame, limit: int | None = None) -> list[dict[str, object]]:
    if df.empty:
        return []
    frame = df.head(limit) if limit else df
    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        rows.append({column: _clean(row[column]) for column in frame.columns})
    return rows


def _latest_payload_path():
    return get_us_config().reports_dir / "us-premarket-latest.json"


def _latest_premarket_payload() -> dict[str, Any]:
    path = _latest_payload_path()
    if not path.exists():
        generate_us_premarket_report()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid US premarket latest report: {path}") from exc


def _find_payload_security(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    wanted = symbol.strip().upper()
    for key in ("top_candidates", "rejected_candidates"):
        for row in payload.get(key, []) or []:
            if str(row.get("symbol", "")).strip().upper() == wanted:
                return dict(row)
    return None


def _persisted_security_detail(symbol: str) -> dict[str, object] | None:
    wanted = symbol.strip().upper()
    if not wanted:
        return None
    store = get_store()
    store.init_db()
    df = store.query_df(
        """
        SELECT *
        FROM expert_screening_results
        WHERE market = 'US'
          AND strategy = 'us_premarket'
          AND upper(symbol) = ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        [wanted],
    )
    rows = _rows(df, limit=1)
    return rows[0] if rows else None


def create_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised via offline import test
        raise RuntimeError("mcp extra is not installed") from exc

    use_us_database()
    server = FastMCP("us-screener")

    @server.tool()
    def us_screen(limit: int = 20) -> dict[str, Any]:
        payload = run_us_screen(persist=True)
        rows = _rows(payload["results"], limit=limit)
        return {
            "snapshot_date": payload["snapshot_date"],
            "macro_context": payload["macro_context"],
            "results": rows,
            "persisted_rows": payload["persisted_rows"],
        }

    @server.tool()
    def us_report_latest() -> dict[str, Any]:
        return _latest_premarket_payload()

    @server.tool()
    def us_security_detail(symbol: str) -> dict[str, Any]:
        payload = _latest_premarket_payload()
        detail = _find_payload_security(payload, symbol) or _persisted_security_detail(symbol)
        if detail is None:
            return {"symbol": symbol, "found": False}
        return {"symbol": symbol, "found": True, "detail": detail}

    @server.tool()
    def us_generate_opinion() -> dict[str, Any]:
        payload = _latest_premarket_payload()
        return generate_us_llm_opinion(payload)

    @server.resource("report://us-premarket/latest")
    def us_premarket_latest() -> str:
        path = _latest_payload_path()
        if not path.exists():
            generate_us_premarket_report()
        return path.read_text(encoding="utf-8")

    return server


def main() -> None:
    server = create_mcp_server()
    server.run()


if __name__ == "__main__":
    main()
