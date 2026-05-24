from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.etf_model import classify_etf, infer_etf_cluster, infer_etf_track
from ah_screener.sources import us_client
from ah_screener.sources.us_client import normalize_us_delisted_lifecycle, select_us_batch_symbols


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

    def test_us_security_master_uses_futu_first(self) -> None:
        original = us_client.fetch_futu_us_security_master
        try:

            def fake_futu_master() -> pd.DataFrame:
                return pd.DataFrame(
                    {
                        "market": ["US"],
                        "symbol": ["AAPL"],
                        "asset_type": ["stock"],
                        "board": ["NASDAQ"],
                        "name": ["Apple"],
                        "exchange": ["NASDAQ"],
                        "currency": ["USD"],
                        "status": ["listed"],
                        "is_st": [False],
                        "is_hk_connect": [False],
                        "metadata_source": ["futu.opend.get_stock_basicinfo"],
                        "metadata_confidence": ["high"],
                        "updated_at": [pd.Timestamp("2026-01-02")],
                    }
                )

            us_client.fetch_futu_us_security_master = fake_futu_master
            out = us_client.fetch_us_security_master()
        finally:
            us_client.fetch_futu_us_security_master = original

        self.assertEqual(list(out["symbol"]), ["AAPL"])
        self.assertEqual(out.iloc[0]["metadata_source"], "futu.opend.get_stock_basicinfo")

    def test_us_spot_uses_futu_snapshot_first(self) -> None:
        original = us_client.fetch_futu_us_spot
        master = pd.DataFrame(
            {
                "market": ["US"],
                "symbol": ["AAPL"],
                "asset_type": ["stock"],
                "board": ["NASDAQ"],
                "name": ["Apple"],
                "exchange": ["NASDAQ"],
                "currency": ["USD"],
                "status": ["listed"],
                "is_st": [False],
                "is_hk_connect": [False],
                "metadata_source": ["test"],
                "metadata_confidence": ["high"],
                "updated_at": [pd.Timestamp("2026-01-02")],
            }
        )
        try:

            def fake_futu_spot(
                symbols: list[str] | None = None, master: pd.DataFrame | None = None
            ) -> tuple[pd.DataFrame, pd.DataFrame]:
                return (
                    master if master is not None else pd.DataFrame(),
                    pd.DataFrame(
                        {
                            "market": ["US"],
                            "symbol": ["AAPL"],
                            "asset_type": ["stock"],
                            "board": ["NASDAQ"],
                            "trade_date": [pd.Timestamp("2026-01-02")],
                            "name": ["Apple"],
                            "last_price": [100.0],
                            "pct_change": [1.0],
                            "volume": [1000.0],
                            "amount": [100000.0],
                            "turnover_rate": [pd.NA],
                            "pe_ttm": [20.0],
                            "pb": [5.0],
                            "market_cap": [1e12],
                            "source": ["futu.opend.get_market_snapshot"],
                            "updated_at": [pd.Timestamp("2026-01-02")],
                        }
                    ),
                )

            us_client.fetch_futu_us_spot = fake_futu_spot
            securities, snapshots = us_client.fetch_us_spot(symbols=["AAPL"], master=master)
        finally:
            us_client.fetch_futu_us_spot = original

        self.assertEqual(list(securities["symbol"]), ["AAPL"])
        self.assertEqual(snapshots.iloc[0]["source"], "futu.opend.get_market_snapshot")

    def test_normalizes_alpha_vantage_delisted_status(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "symbol": "abc/u",
                    "name": "ABC Units",
                    "exchange": "NYSE",
                    "assetType": "Stock",
                    "ipoDate": "2010-01-04",
                    "delistingDate": "2020-05-06",
                    "status": "Delisted",
                },
                {
                    "symbol": "ETF1",
                    "name": "ETF One",
                    "exchange": "NASDAQ",
                    "assetType": "ETF",
                    "ipoDate": "2015-02-03",
                    "delistingDate": "2021-06-07",
                    "status": "Delisted",
                },
            ]
        )

        out = normalize_us_delisted_lifecycle(raw, source="alphavantage.listing_status.delisted")

        self.assertEqual(list(out["symbol"]), ["ABC.U", "ETF1"])
        self.assertEqual(list(out["asset_type"]), ["stock", "etf"])
        self.assertTrue(out["source"].str.startswith("alphavantage.listing_status.delisted:").all())
        self.assertEqual(str(out.iloc[0]["delist_date"]), "2020-05-06")

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
