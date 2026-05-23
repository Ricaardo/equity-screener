from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.reporting import market_date_health


class MarketDateHealthTest(TestCase):
    def test_warns_when_markets_diverge(self) -> None:
        snaps = pd.DataFrame(
            [
                {"market": "A", "trade_date": "2026-05-24"},
                {"market": "HK", "trade_date": "2026-05-23"},
                {"market": "US", "trade_date": "2026-05-20"},
            ]
        )
        table, warning = market_date_health(snaps, max_spread_days=3)
        self.assertEqual(len(table), 3)
        self.assertTrue(warning)  # 4-day spread > 3
        self.assertIn("US", warning)

    def test_no_warning_when_aligned(self) -> None:
        snaps = pd.DataFrame(
            [
                {"market": "A", "trade_date": "2026-05-24"},
                {"market": "HK", "trade_date": "2026-05-24"},
            ]
        )
        _, warning = market_date_health(snaps)
        self.assertEqual(warning, "")

    def test_empty_safe(self) -> None:
        table, warning = market_date_health(pd.DataFrame())
        self.assertTrue(table.empty)
        self.assertEqual(warning, "")
