from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.etf_model import classify_etf, infer_etf_cluster, infer_etf_track
from ah_screener.sources import us_client
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

    def test_us_etf_names_infer_broad_tracks(self) -> None:
        self.assertEqual(classify_etf("SPDR S&P 500 ETF Trust")[0], "宽基指数ETF")
        self.assertEqual(infer_etf_track("SPDR S&P 500 ETF Trust")[0], "标普500")
        self.assertEqual(infer_etf_track("Invesco QQQ Trust NASDAQ-100 ETF")[0], "纳斯达克100")
        self.assertEqual(infer_etf_track("iShares Russell 2000 ETF")[0], "罗素2000")
        self.assertEqual(infer_etf_cluster("标普500"), "美股大盘")
        self.assertEqual(infer_etf_cluster("罗素2000"), "美股小盘")

    def test_fetch_us_history_uses_futu_first_when_available(self) -> None:
        calls: list[str] = []
        original_futu = us_client._fetch_us_history_futu
        original_akshare = us_client._fetch_us_history_akshare
        try:
            def fake_futu(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
                calls.append("futu")
                return pd.DataFrame(
                    {
                        "market": ["US"],
                        "symbol": [symbol],
                        "trade_date": [pd.Timestamp("2026-01-02")],
                        "open": [1.0],
                        "high": [1.1],
                        "low": [0.9],
                        "close": [1.0],
                        "volume": [100.0],
                        "amount": [100.0],
                        "adj_type": ["raw"],
                        "source": ["futu.opend.history_kline"],
                        "updated_at": [pd.Timestamp("2026-01-02")],
                    }
                )

            def fail(*args, **kwargs) -> pd.DataFrame:
                calls.append("fallback")
                raise AssertionError("fallback should not be called")

            us_client._fetch_us_history_futu = fake_futu
            us_client._fetch_us_history_akshare = fail
            out = us_client.fetch_us_history("SPY", "20260101", "20260103")
            self.assertEqual(calls, ["futu"])
            self.assertEqual(out.iloc[0]["source"], "futu.opend.history_kline")
        finally:
            us_client._fetch_us_history_futu = original_futu
            us_client._fetch_us_history_akshare = original_akshare
