from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.point_in_time import (
    NEUTRAL_SCORE,
    as_of_fundamental_score,
    build_income_index,
)


def _items() -> pd.DataFrame:
    rows = []
    for rd, rev_yoy, profit_yoy in [
        ("2024-12-31", 10.0, 5.0),
        ("2025-06-30", 40.0, 80.0),   # strong growth, filed ~Aug 2025
    ]:
        rows.append({"market": "A", "symbol": "600000", "statement_type": "income",
                     "report_date": rd, "item_code": "TOTAL_OPERATE_INCOME_YOY", "item_name": "x", "amount": rev_yoy})
        rows.append({"market": "A", "symbol": "600000", "statement_type": "income",
                     "report_date": rd, "item_code": "PARENT_NETPROFIT_YOY", "item_name": "x", "amount": profit_yoy})
    return pd.DataFrame(rows)


class PointInTimeTest(TestCase):
    def test_as_of_uses_only_filed_reports(self) -> None:
        items = _items()
        # As of 2025-07-01, the 2025-06-30 report (filed ~Aug, lag 60d) is NOT yet known
        # -> falls back to 2024-12-31 (rev 10 / profit 5) -> mild positive score.
        early = as_of_fundamental_score(items, "A", "600000", pd.Timestamp("2025-07-01"))
        # As of 2025-10-01, the strong 2025-06-30 report is known -> higher score.
        late = as_of_fundamental_score(items, "A", "600000", pd.Timestamp("2025-10-01"))
        self.assertGreater(late, early)
        self.assertGreater(late, 60.0)  # strong growth lifts above neutral

    def test_no_look_ahead_before_any_report(self) -> None:
        items = _items()
        score = as_of_fundamental_score(items, "A", "600000", pd.Timestamp("2024-01-01"))
        self.assertEqual(score, NEUTRAL_SCORE)  # nothing filed yet -> neutral

    def test_missing_symbol_neutral(self) -> None:
        index = build_income_index(_items())
        from ah_screener.point_in_time import as_of_score_from_index
        self.assertEqual(as_of_score_from_index(index, "A", "999999", pd.Timestamp("2025-10-01")), NEUTRAL_SCORE)
