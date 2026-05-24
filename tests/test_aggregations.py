from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.aggregations import CANDIDATE_DIFF_COLUMNS, candidate_diff


def _row(date: str, bucket: str, symbol: str, score: float) -> dict:
    return {
        "snapshot_date": date,
        "bucket": bucket,
        "market": "A",
        "symbol": symbol,
        "name": symbol,
        "expert_score": score,
    }


class CandidateDiffTest(TestCase):
    def test_single_snapshot_is_empty(self) -> None:
        df = pd.DataFrame([_row("2026-05-23", "AI", "600000", 70)])
        self.assertTrue(candidate_diff(df).empty)

    def test_new_removed_kept_and_delta(self) -> None:
        df = pd.DataFrame(
            [
                _row("2026-05-23", "AI", "600000", 70),  # kept
                _row("2026-05-23", "AI", "600001", 60),  # removed
                _row("2026-05-24", "AI", "600000", 72),  # kept (delta +2)
                _row("2026-05-24", "AI", "600002", 65),  # new
            ]
        )
        out = candidate_diff(df)
        self.assertEqual(list(out.columns), CANDIDATE_DIFF_COLUMNS)
        by_symbol = out.set_index("symbol")
        self.assertEqual(by_symbol.loc["600000", "status"], "kept")
        self.assertEqual(by_symbol.loc["600000", "score_delta"], 2.0)
        self.assertEqual(by_symbol.loc["600001", "status"], "removed")
        self.assertEqual(by_symbol.loc["600001", "name"], "600001")
        self.assertEqual(by_symbol.loc["600002", "status"], "new")

    def test_removed_row_keeps_previous_name(self) -> None:
        old = _row("2026-05-23", "AI", "600099", 60)
        old["name"] = "OldCo"
        df = pd.DataFrame([old, _row("2026-05-24", "AI", "600100", 65)])
        out = candidate_diff(df).set_index("symbol")
        self.assertEqual(out.loc["600099", "status"], "removed")
        self.assertEqual(out.loc["600099", "name"], "OldCo")
