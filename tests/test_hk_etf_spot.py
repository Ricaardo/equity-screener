from __future__ import annotations

import sys
import types
from unittest import TestCase

import pandas as pd

from ah_screener.sources import akshare_client
from ah_screener.sources.akshare_client import (
    _parse_hkex_delisted_html,
    fetch_history,
    normalize_hk_delisted_lifecycle,
    normalize_hk_etf_spot,
)


class HkEtfSpotTest(TestCase):
    def test_normalizes_hk_etfs_from_hk_spot_rows(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "代码": "02800",
                    "名称": "盈富基金",
                    "最新价": 18.5,
                    "涨跌幅": 0.3,
                    "成交量": 100_000,
                    "成交额": 185_000_000,
                },
                {
                    "代码": "03033",
                    "名称": "南方恒生科技",
                    "最新价": 4.2,
                    "涨跌幅": 1.2,
                    "成交量": 80_000,
                    "成交额": 33_600_000,
                },
                {
                    "代码": "00700",
                    "名称": "腾讯控股",
                    "最新价": 400.0,
                    "涨跌幅": 0.5,
                    "成交量": 10_000,
                    "成交额": 4_000_000,
                },
            ]
        )

        securities, snapshots = normalize_hk_etf_spot(raw, "test.hk_spot")

        self.assertEqual(set(securities["symbol"]), {"02800", "03033"})
        self.assertEqual(set(snapshots["symbol"]), {"02800", "03033"})
        self.assertTrue(securities["asset_type"].eq("etf").all())
        self.assertTrue(snapshots["asset_type"].eq("etf").all())
        self.assertEqual(
            securities.loc[securities["symbol"].eq("02800"), "exchange"].iloc[0], "HKEX"
        )


class HkDelistedLifecycleTest(TestCase):
    def test_parses_hkex_table_with_header_row(self) -> None:
        html = """
        <table><tr><td>Stock Code</td><td>Stock Name</td></tr>
        <tr><td>00009</td><td>KEYNE LTD</td></tr></table>
        """

        out = _parse_hkex_delisted_html(html)

        self.assertEqual(list(out.columns), ["Stock Code", "Stock Name"])
        self.assertEqual(out.iloc[0]["Stock Code"], "00009")

    def test_normalizes_hkex_delisted_list(self) -> None:
        raw = pd.DataFrame(
            [
                {"Stock Code": "00067", "Stock Name": "LUMENA NEWMAT"},
                {"Stock Code": "67", "Stock Name": "OLD LISTING"},
                {"Stock Code": "bad", "Stock Name": "ignore"},
            ]
        )

        out = normalize_hk_delisted_lifecycle(raw, source="hkex.di.delisted_stock_list")

        self.assertEqual(list(out["symbol"]), ["00067", "00067"])
        self.assertTrue(out["source"].str.startswith("hkex.di.delisted_stock_list:").all())
        self.assertTrue(out["status"].eq("delisted").all())
        self.assertTrue(out["exchange"].eq("HKEX").all())


class AHistoryFutuFallbackTest(TestCase):
    def test_a_history_uses_futu_before_akshare(self) -> None:
        original = akshare_client.fetch_futu_history
        try:

            def fake_futu(
                market: str, symbol: str, start_date: str, end_date: str, adjust: str
            ) -> pd.DataFrame:
                self.assertEqual(market, "A")
                return pd.DataFrame(
                    {
                        "market": ["A"],
                        "symbol": [symbol],
                        "trade_date": [pd.Timestamp("2026-01-02")],
                        "open": [10.0],
                        "high": [11.0],
                        "low": [9.0],
                        "close": [10.5],
                        "volume": [1000.0],
                        "amount": [10500.0],
                        "adj_type": [adjust],
                        "source": ["futu.opend.history_kline"],
                        "updated_at": [pd.Timestamp("2026-01-02")],
                    }
                )

            akshare_client.fetch_futu_history = fake_futu
            out = fetch_history("A", "600519", "20260101", "20260103")
        finally:
            akshare_client.fetch_futu_history = original

        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["source"], "futu.opend.history_kline")


class ASpotFutuFallbackTest(TestCase):
    def test_a_spot_keeps_bse_rows_from_akshare_when_futu_is_available(self) -> None:
        original_futu = akshare_client.fetch_futu_a_spot
        original_first = akshare_client._fetch_first_available
        original_akshare = sys.modules.get("akshare")
        sys.modules["akshare"] = types.SimpleNamespace(
            stock_zh_a_spot_em=lambda: None,
            stock_zh_a_spot=lambda: None,
        )
        try:

            def fake_futu() -> tuple[pd.DataFrame, pd.DataFrame]:
                return (
                    pd.DataFrame(
                        {
                            "market": ["A"],
                            "symbol": ["600000"],
                            "asset_type": ["stock"],
                            "board": ["主板"],
                            "name": ["浦发银行"],
                            "exchange": ["SSE"],
                            "currency": ["CNY"],
                            "status": ["listed"],
                            "is_st": [False],
                            "is_hk_connect": [False],
                            "metadata_source": ["futu.opend.get_stock_basicinfo"],
                            "metadata_confidence": ["high"],
                            "updated_at": [pd.Timestamp("2026-01-02")],
                        }
                    ),
                    pd.DataFrame(
                        {
                            "market": ["A"],
                            "symbol": ["600000"],
                            "asset_type": ["stock"],
                            "board": ["主板"],
                            "trade_date": [pd.Timestamp("2026-01-02")],
                            "name": ["浦发银行"],
                            "last_price": [10.0],
                            "pct_change": [1.0],
                            "volume": [1000.0],
                            "amount": [10000.0],
                            "turnover_rate": [0.1],
                            "pe_ttm": [5.0],
                            "pb": [0.5],
                            "market_cap": [1e11],
                            "source": ["futu.opend.get_market_snapshot"],
                            "updated_at": [pd.Timestamp("2026-01-02")],
                        }
                    ),
                )

            def fake_first_available(calls):
                return (
                    pd.DataFrame(
                        [
                            {"代码": "600000", "名称": "浦发银行", "最新价": 10.0},
                            {"代码": "830799", "名称": "艾融软件", "最新价": 20.0},
                        ]
                    ),
                    "akshare.stock_zh_a_spot_em",
                )

            akshare_client.fetch_futu_a_spot = fake_futu
            akshare_client._fetch_first_available = fake_first_available
            securities, snapshots = akshare_client.fetch_spot("A")
        finally:
            akshare_client.fetch_futu_a_spot = original_futu
            akshare_client._fetch_first_available = original_first
            if original_akshare is None:
                sys.modules.pop("akshare", None)
            else:
                sys.modules["akshare"] = original_akshare

        self.assertEqual(set(securities["symbol"]), {"600000", "830799"})
        self.assertEqual(set(snapshots["symbol"]), {"600000", "830799"})
        source_by_symbol = snapshots.set_index("symbol")["source"].to_dict()
        self.assertEqual(source_by_symbol["600000"], "futu.opend.get_market_snapshot")
        self.assertEqual(source_by_symbol["830799"], "akshare.stock_zh_a_spot_em")
