from __future__ import annotations

from unittest import TestCase

import numpy as np
import pandas as pd

from ah_screener.expert_validation import validate_expert_decisions


def _prices(symbol: str, market: str, start: str, n: int, drift: float) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    close = 100 * np.cumprod(1 + np.full(n, drift))
    return pd.DataFrame(
        {"market": market, "symbol": symbol, "trade_date": dates, "close": close}
    )


class ExpertValidationTest(TestCase):
    def test_empty_inputs_return_note_not_crash(self) -> None:
        stats, summary = validate_expert_decisions(pd.DataFrame(), pd.DataFrame())
        self.assertTrue(stats.empty)
        self.assertEqual(summary["sample_count"], 0)
        self.assertIn("survivorship", summary["bias_note"])

    def test_malformed_trade_date_does_not_crash(self) -> None:
        prices = _prices("CORE1", "A", "2026-01-01", 30, 0.01)
        prices = pd.concat(
            [
                prices,
                pd.DataFrame(
                    [{"market": "A", "symbol": "CORE1", "trade_date": "not-a-date", "close": 1.0}]
                ),
            ],
            ignore_index=True,
        )
        prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="coerce")
        expert = pd.DataFrame(
            [
                {"snapshot_date": pd.Timestamp("2026-01-12"), "market": "A", "symbol": "CORE1",
                 "decision": "core_candidate", "expert_score": 80}
            ]
        )
        stats, summary = validate_expert_decisions(expert, prices, forward_days=5)
        self.assertIsInstance(summary["sample_count"], int)  # no crash

    def test_forward_excess_computed_per_decision(self) -> None:
        # Core names drift up, rejects drift down; forward window = 5 days.
        prices = pd.concat(
            [
                _prices("CORE1", "A", "2026-01-01", 30, 0.02),
                _prices("CORE2", "A", "2026-01-01", 30, 0.018),
                _prices("REJ1", "A", "2026-01-01", 30, -0.02),
                _prices("REJ2", "A", "2026-01-01", 30, -0.018),
            ],
            ignore_index=True,
        )
        snapshot = pd.Timestamp("2026-01-12")
        expert = pd.DataFrame(
            [
                {"snapshot_date": snapshot, "market": "A", "symbol": "CORE1",
                 "decision": "core_candidate", "expert_score": 80},
                {"snapshot_date": snapshot, "market": "A", "symbol": "CORE2",
                 "decision": "core_candidate", "expert_score": 78},
                {"snapshot_date": snapshot, "market": "A", "symbol": "REJ1",
                 "decision": "reject", "expert_score": 30},
                {"snapshot_date": snapshot, "market": "A", "symbol": "REJ2",
                 "decision": "reject", "expert_score": 28},
            ]
        )
        stats, summary = validate_expert_decisions(expert, prices, forward_days=5)
        self.assertGreater(summary["sample_count"], 0)
        by_decision = stats.set_index("decision")
        # Core's forward excess (vs universe median) must exceed reject's.
        self.assertGreater(
            float(by_decision.loc["core_candidate", "median_excess"]),
            float(by_decision.loc["reject", "median_excess"]),
        )
        self.assertTrue(summary["monotonic"])
