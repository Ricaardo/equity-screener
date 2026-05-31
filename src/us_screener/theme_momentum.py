"""Dynamic theme momentum — which concept boards the market is bidding up.

Concept boards are a static seed of *what could matter*; this measures *what's
actually being bid up right now* by aggregating the relative strength of each
board's constituents. A theme whose stocks are collectively leading the market
(high average RS) is "hot/accelerating" — price front-runs the headlines, so this
is a forward read on narrative momentum without needing a news feed.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from us_screener.concept_boards import concept_board_map
from us_screener.relative_strength import compute_rs_scores

MIN_MEMBERS = 3


def compute_theme_momentum(store, *, rs: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-board momentum from constituents' relative strength.

    Columns: board / members / avg_rs / leaders_pct / momentum_score (0-100).
    """
    boards_map = concept_board_map(store)  # symbol -> [boards]
    if not boards_map:
        return pd.DataFrame(columns=["board", "members", "avg_rs", "leaders_pct", "momentum_score"])
    rs = compute_rs_scores(store) if rs is None else rs
    rs_by_symbol = (
        dict(zip(rs["symbol"].astype(str).str.upper(), pd.to_numeric(rs["rs_score"], errors="coerce")))
        if not rs.empty
        else {}
    )

    board_scores: dict[str, list[float]] = {}
    for symbol, boards in boards_map.items():
        score = rs_by_symbol.get(str(symbol).strip().upper())
        if score is None or np.isnan(score):
            continue
        for board in boards:
            board_scores.setdefault(board, []).append(float(score))

    rows: list[dict[str, Any]] = []
    for board, scores in board_scores.items():
        if len(scores) < MIN_MEMBERS:
            continue
        arr = np.array(scores, dtype=float)
        rows.append(
            {
                "board": board,
                "members": len(scores),
                "avg_rs": round(float(arr.mean()), 2),
                "leaders_pct": round(float((arr >= 70).mean()) * 100, 1),  # % constituents leading
                "momentum_score": round(float(0.7 * arr.mean() + 0.3 * (arr >= 70).mean() * 100), 2),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["board", "members", "avg_rs", "leaders_pct", "momentum_score"])
    return pd.DataFrame(rows).sort_values("momentum_score", ascending=False).reset_index(drop=True)


def theme_momentum_map(store, *, rs: pd.DataFrame | None = None) -> dict[str, float]:
    """board -> momentum_score."""
    frame = compute_theme_momentum(store, rs=rs)
    if frame.empty:
        return {}
    return dict(zip(frame["board"], frame["momentum_score"]))
