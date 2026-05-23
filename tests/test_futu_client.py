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
