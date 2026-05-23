from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.universe import ETFS, STOCKS, AssetClass, latest_per_security, select_assets


class UniverseTest(TestCase):
    def setUp(self) -> None:
        self.df = pd.DataFrame(
            [
                {"market": "A", "symbol": "600000", "asset_type": "stock"},
                {"market": "A", "symbol": "510300", "asset_type": "etf"},
                {"market": "A", "symbol": "000001", "asset_type": None},  # defaults to stock
            ]
        )

    def test_select_stocks_includes_null_asset_type(self) -> None:
        out = select_assets(self.df, STOCKS)
        self.assertEqual(set(out["symbol"]), {"600000", "000001"})

    def test_select_etfs(self) -> None:
        out = select_assets(self.df, ETFS)
        self.assertEqual(set(out["symbol"]), {"510300"})

    def test_select_multiple_classes(self) -> None:
        out = select_assets(self.df, (*STOCKS, *ETFS))
        self.assertEqual(len(out), 3)

    def test_no_asset_type_column_treated_as_stock(self) -> None:
        plain = pd.DataFrame([{"market": "A", "symbol": "600000"}])
        self.assertEqual(len(select_assets(plain, STOCKS)), 1)
        self.assertTrue(select_assets(plain, ETFS).empty)

    def test_latest_per_security_keeps_newest(self) -> None:
        df = pd.DataFrame(
            [
                {"market": "A", "symbol": "600000", "trade_date": "2025-01-01", "v": 1},
                {"market": "A", "symbol": "600000", "trade_date": "2025-02-01", "v": 2},
            ]
        )
        out = latest_per_security(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["v"], 2)

    def test_asset_class_enum_value(self) -> None:
        self.assertEqual(AssetClass.ETF.value, "etf")
