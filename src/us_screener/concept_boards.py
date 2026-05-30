"""US concept/theme board classification (AI算力 / 量子 / 稳定币 / 减肥药 / 核电 ...).

US sector data is sparse compared with A-share concept boards, so we combine an
editable curated symbol→board seed (``data/us_concept_boards.seed.csv``) with a
keyword fallback over the security name. Boards are written to ``company_tags`` as
``tag_type='concept_board'`` and feed the scoring theme-match the same way A-share
concept tags do.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from us_screener.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

SEED_CSV_PATH = PROJECT_ROOT / "data" / "us_concept_boards.seed.csv"

TAG_TYPE = "concept_board"
TAG_SOURCE = "us_screener.concept_boards"

# Keyword fallback over the security name. Curated seed takes precedence.
_KEYWORD_BOARDS: dict[str, tuple[str, ...]] = {
    "AI算力": ("semiconductor", "gpu", " ai ", "artificial intelligence"),
    "量子计算": ("quantum",),
    "稳定币加密": ("bitcoin", "crypto", "blockchain", "stablecoin", "digital asset"),
    "核电": ("nuclear", "uranium"),
    "网络安全": ("cybersecurity", "cyber security"),
    "太空": ("space",),
    "电动车": ("electric vehicle",),
}


def load_seed(path: Path | None = None) -> dict[str, str]:
    """Load the curated symbol→board map (upper-cased symbols)."""
    mapping: dict[str, str] = {}
    csv_path = path or SEED_CSV_PATH
    try:
        frame = pd.read_csv(csv_path)
        if {"symbol", "board"} <= set(frame.columns):
            for _, row in frame.dropna(subset=["symbol", "board"]).iterrows():
                mapping[str(row["symbol"]).strip().upper()] = str(row["board"]).strip()
    except FileNotFoundError:
        logger.warning("concept-board seed CSV not found at %s", csv_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read concept-board seed CSV: %s", exc)
    return mapping


def infer_concept_boards(
    symbol: str,
    name: object = None,
    *,
    seed: dict[str, str] | None = None,
) -> list[str]:
    """Return concept boards for a symbol: curated seed first, else name keywords."""
    seed = seed if seed is not None else load_seed()
    sym = str(symbol or "").strip().upper()
    if sym in seed:
        return [seed[sym]]
    text = f" {str(name or '').lower()} "
    boards = [
        board
        for board, kws in _KEYWORD_BOARDS.items()
        if any(kw in text for kw in kws)
    ]
    return boards


def tag_concept_boards(store, *, seed: dict[str, str] | None = None) -> dict[str, int]:
    """Classify all US stocks and write ``concept_board`` tags to ``company_tags``."""
    seed = seed if seed is not None else load_seed()
    securities = store.query_df(
        "SELECT market, symbol, name FROM securities "
        "WHERE market='US' AND COALESCE(asset_type,'stock') <> 'etf'"
    )
    if securities.empty:
        return {"tagged": 0, "symbols": 0, "boards": 0}

    rows: list[dict[str, Any]] = []
    symbols_with_board = 0
    for _, sec in securities.iterrows():
        boards = infer_concept_boards(sec["symbol"], sec.get("name"), seed=seed)
        if boards:
            symbols_with_board += 1
        for board in boards:
            evidence = "high" if str(sec["symbol"]).strip().upper() in seed else "keyword"
            rows.append(
                {
                    "market": "US",
                    "symbol": str(sec["symbol"]).strip().upper(),
                    "tag_type": TAG_TYPE,
                    "tag_name": board,
                    "evidence_level": evidence,
                    "source": TAG_SOURCE,
                    "updated_at": pd.Timestamp.now(),
                }
            )
    if rows:
        store.upsert_dataframe("company_tags", pd.DataFrame(rows))
    return {
        "tagged": len(rows),
        "symbols": symbols_with_board,
        "boards": len({r["tag_name"] for r in rows}),
    }


def concept_board_map(store) -> dict[str, list[str]]:
    """symbol -> list of concept boards currently tagged."""
    tags = store.query_df(
        "SELECT symbol, tag_name FROM company_tags WHERE market='US' AND tag_type=?",
        [TAG_TYPE],
    )
    out: dict[str, list[str]] = {}
    if tags.empty:
        return out
    for _, row in tags.iterrows():
        out.setdefault(str(row["symbol"]).strip().upper(), []).append(str(row["tag_name"]))
    return out
