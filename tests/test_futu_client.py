from __future__ import annotations

from unittest import TestCase

from ah_screener.sources import futu_client


class FutuClientTest(TestCase):
    def test_code_mapping_across_markets(self) -> None:
        self.assertEqual(futu_client.futu_code("US", "AAPL"), "US.AAPL")
        self.assertEqual(futu_client.futu_code("US", "BRK.B"), "US.BRK-B")
        self.assertEqual(futu_client.futu_code("HK", "700"), "HK.00700")
        self.assertEqual(futu_client.futu_code("HK", "00700"), "HK.00700")
        self.assertEqual(futu_client.futu_code("A", "600000"), "SH.600000")
        self.assertEqual(futu_client.futu_code("A", "000001"), "SZ.000001")
        self.assertEqual(futu_client.futu_code("A", "510300"), "SH.510300")  # SH ETF
        self.assertEqual(futu_client.futu_code("A", "159915"), "SZ.159915")  # SZ ETF

    def test_unsupported_market_raises(self) -> None:
        with self.assertRaises(ValueError):
            futu_client.futu_code("JP", "7203")

    def test_fetch_returns_empty_when_disabled(self) -> None:
        original = futu_client.USE_FUTU
        futu_client.USE_FUTU = False
        try:
            out = futu_client.fetch_futu_history("HK", "00700", "20260101", "20260131")
        finally:
            futu_client.USE_FUTU = original
        self.assertTrue(out.empty)


class FutuHkEtfNormalizeTest(TestCase):
    def test_normalize_hk_etf_builds_frames(self) -> None:
        import pandas as pd

        from ah_screener.sources.futu_client import _normalize_hk_etf

        basics = pd.DataFrame([{"code": "HK.02800", "name": "盈富基金"}, {"code": "HK.03033", "name": "南方恒生科技"}])
        snap = pd.DataFrame(
            [
                {"code": "HK.02800", "last_price": 25.0, "prev_close_price": 24.0, "volume": 1e6,
                 "turnover": 2.5e7, "turnover_rate": 0.5, "update_time": "2026-05-23 16:00:00"},
                {"code": "HK.03033", "last_price": 6.0, "prev_close_price": 6.0, "volume": 5e5,
                 "turnover": 3e6, "turnover_rate": 0.3, "update_time": "2026-05-23 16:00:00"},
            ]
        )
        securities, snapshots = _normalize_hk_etf(basics, snap)
        self.assertEqual(set(securities["symbol"]), {"02800", "03033"})
        self.assertTrue((securities["asset_type"] == "etf").all())
        s = snapshots.set_index("symbol")
        self.assertAlmostEqual(float(s.loc["02800", "pct_change"]), (25.0 / 24.0 - 1) * 100, places=3)
        self.assertEqual(float(s.loc["02800", "last_price"]), 25.0)

    def test_normalize_hk_etf_empty(self) -> None:
        import pandas as pd

        from ah_screener.sources.futu_client import _normalize_hk_etf

        sec, snap = _normalize_hk_etf(pd.DataFrame(), pd.DataFrame())
        self.assertTrue(sec.empty and snap.empty)


class FutuHkEtfDedupTest(TestCase):
    def test_normalize_hk_etf_dedups_codes(self) -> None:
        import pandas as pd

        from ah_screener.sources.futu_client import _normalize_hk_etf

        basics = pd.DataFrame([{"code": "HK.02800", "name": "盈富"}, {"code": "HK.02800", "name": "盈富"}])
        snap = pd.DataFrame(
            [
                {"code": "HK.02800", "last_price": 25.0, "prev_close_price": 24.0},
                {"code": "HK.02800", "last_price": 25.0, "prev_close_price": 24.0},
            ]
        )
        sec, snaps = _normalize_hk_etf(basics, snap)
        self.assertEqual(len(sec), 1)
        self.assertEqual(len(snaps), 1)
