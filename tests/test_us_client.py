from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.sources.us_client import select_us_batch_symbols


class UsClientTest(TestCase):
    def test_selects_full_list_batch_with_optional_etfs(self) -> None:
        master = pd.DataFrame(
            [
                {"symbol": "BBB", "asset_type": "etf", "exchange": "NYSE", "status": "listed"},
                {"symbol": "AAA", "asset_type": "stock", "exchange": "NASDAQ", "status": "listed"},
                {"symbol": "CCC", "asset_type": "stock", "exchange": "NYSE", "status": "listed"},
                {"symbol": "DDD", "asset_type": "stock", "exchange": "NYSE", "status": "delisted"},
            ]
        )

        self.assertEqual(
            select_us_batch_symbols(master, offset=0, limit=10, include_etf=False),
            ["AAA", "CCC"],
        )
        self.assertEqual(
            select_us_batch_symbols(master, offset=1, limit=2, include_etf=True),
            ["AAA", "CCC"],
        )
