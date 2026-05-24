"""Canonical, presentation-neutral aggregations shared by pipeline / reporting / UI.

Single source of truth for cross-snapshot diffs and coverage rollups, so the CLI,
the Markdown report and the JSON product cannot drift from one another (they used
to each carry their own near-duplicate copy). Pure: DataFrames in, DataFrame out —
no DB, no display formatting (callers rename columns for their surface).
"""

from __future__ import annotations

import pandas as pd

CANDIDATE_DIFF_COLUMNS = [
    "status",
    "bucket",
    "market",
    "symbol",
    "name",
    "latest_score",
    "previous_score",
    "score_delta",
]


def candidate_diff(refined: pd.DataFrame) -> pd.DataFrame:
    """Compare the two most recent refined-candidate snapshots.

    Returns stable English columns (see CANDIDATE_DIFF_COLUMNS); ``status`` is
    new / removed / kept, ``score_delta`` is latest - previous (rounded to 1dp).
    Empty when fewer than two snapshots exist.
    """
    if refined is None or refined.empty or refined["snapshot_date"].nunique() < 2:
        return pd.DataFrame(columns=CANDIDATE_DIFF_COLUMNS)

    dates = sorted(refined["snapshot_date"].dropna().unique())
    previous_date, latest_date = dates[-2], dates[-1]
    previous = refined[refined["snapshot_date"] == previous_date].copy()
    latest = refined[refined["snapshot_date"] == latest_date].copy()
    key_columns = ["bucket", "market", "symbol"]
    merged = latest.merge(
        previous[key_columns + ["name", "expert_score"]].rename(
            columns={"name": "name_previous", "expert_score": "previous_score"}
        ),
        on=key_columns,
        how="outer",
        indicator=True,
    )
    merged["status"] = merged["_merge"].map(
        {"left_only": "new", "right_only": "removed", "both": "kept"}
    )
    merged["latest_score"] = pd.to_numeric(merged.get("expert_score"), errors="coerce")
    merged["previous_score"] = pd.to_numeric(merged.get("previous_score"), errors="coerce")
    merged["score_delta"] = (merged["latest_score"] - merged["previous_score"]).round(1)
    merged["name"] = merged["name"].fillna(merged.get("name_previous"))
    return merged[CANDIDATE_DIFF_COLUMNS].sort_values(
        ["status", "bucket", "latest_score"], ascending=[True, True, False]
    )
