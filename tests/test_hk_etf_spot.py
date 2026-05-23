from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.sources.akshare_client import normalize_hk_etf_spot


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
        self.assertEqual(securities.loc[securities["symbol"].eq("02800"), "exchange"].iloc[0], "HKEX")
