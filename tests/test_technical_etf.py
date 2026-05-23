from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.technical import compute_technical_indicators


def _prices(symbol: str, closes: list[float], market: str = "A") -> pd.DataFrame:
    dates = pd.bdate_range("2024-09-02", periods=len(closes))
    return pd.DataFrame(
        {
            "market": market,
            "symbol": symbol,
            "trade_date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": 1_000_000.0,
            "amount": 10_000_000.0,
            "adj_type": "qfq",
            "source": "test",
        }
    )


def _snap(symbol: str, name: str, asset_type: str, market: str = "A") -> dict:
    return {
        "market": market,
        "symbol": symbol,
        "trade_date": pd.Timestamp("2025-01-31"),
        "name": name,
        "asset_type": asset_type,
    }


class TechnicalAssetClassTest(TestCase):
    def setUp(self) -> None:
        up = [10.0 + i * 0.05 for i in range(80)]  # steady uptrend, 80 sessions
        flat = [100.0 for _ in range(80)]  # money-market: no trend
        short = [10.0 + i * 0.02 for i in range(40)]  # <60 sessions
        self.daily = pd.concat(
            [
                _prices("510300", up),  # equity ETF
                _prices("511880", flat),  # money ETF
                _prices("159001", short),  # newly-listed ETF (<60d)
                _prices("600000", short),  # newly-listed stock (<60d)
            ],
            ignore_index=True,
        )
        self.snaps = pd.DataFrame(
            [
                _snap("510300", "沪深300ETF华夏", "etf"),
                _snap("511880", "货币ETF", "etf"),
                _snap("159001", "某新主题ETF", "etf"),
                _snap("600000", "浦发银行", "stock"),
            ]
        )

    def test_equity_etf_scored_like_stock(self) -> None:
        out = compute_technical_indicators(self.daily, self.snaps).set_index("symbol")
        self.assertIn("510300", out.index)
        self.assertNotEqual(out.loc["510300", "technical_signal"], "工具型")
        self.assertTrue(0 <= float(out.loc["510300", "technical_score"]) <= 100)

    def test_money_etf_gets_neutral_score(self) -> None:
        out = compute_technical_indicators(self.daily, self.snaps).set_index("symbol")
        self.assertEqual(out.loc["511880", "technical_score"], 50.0)
        self.assertEqual(out.loc["511880", "technical_signal"], "工具型")

    def test_short_history_etf_surfaces_flagged_neutral(self) -> None:
        out = compute_technical_indicators(self.daily, self.snaps).set_index("symbol")
        self.assertIn("159001", out.index)  # ETF surfaces despite <60d (R12)
        self.assertEqual(out.loc["159001", "technical_score"], 50.0)
        self.assertEqual(out.loc["159001", "technical_signal"], "数据不足")

    def test_short_history_stock_still_skipped(self) -> None:
        out = compute_technical_indicators(self.daily, self.snaps)
        self.assertNotIn("600000", set(out.get("symbol", [])))  # stock behaviour unchanged
