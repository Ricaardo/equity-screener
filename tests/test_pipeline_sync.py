from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd

from ah_screener import pipeline
from ah_screener.storage import Store


def _sec(market: str) -> pd.DataFrame:
    return pd.DataFrame([{"market": market, "symbol": "X1", "name": f"{market} name"}])


def _snap(market: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{"market": market, "symbol": "X1", "trade_date": "2026-05-23", "source": "test", "name": "n"}]
    )


class SyncSpotResilienceTest(TestCase):
    def test_sync_spot_all_tolerates_single_market_failure(self) -> None:
        empty = (pd.DataFrame(), pd.DataFrame())
        originals = (
            pipeline.fetch_spot,
            pipeline.fetch_a_etf_spot,
            pipeline.fetch_hk_etf_spot,
            pipeline.get_store,
        )
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.duckdb")
            store.init_db()

            def fake_fetch_spot(market):
                if market == "HK":
                    raise RuntimeError("transient akshare HK spot failure")
                return _sec(market), _snap(market)

            pipeline.fetch_spot = fake_fetch_spot
            pipeline.fetch_a_etf_spot = lambda: empty
            pipeline.fetch_hk_etf_spot = lambda: empty
            pipeline.get_store = lambda: store
            try:
                result = pipeline.sync_spot("all")
            finally:
                (
                    pipeline.fetch_spot,
                    pipeline.fetch_a_etf_spot,
                    pipeline.fetch_hk_etf_spot,
                    pipeline.get_store,
                ) = originals

        # HK failed but is recorded, not raised; A and US still ingested.
        self.assertEqual(result.get("HK_failed"), 1)
        self.assertIn("transient", result.get("HK_error", ""))
        self.assertEqual(result.get("A_snapshots"), 1)
        self.assertEqual(result.get("US_snapshots"), 1)

    def test_sync_spot_single_market_still_raises(self) -> None:
        originals = (pipeline.fetch_spot, pipeline.get_store)
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.duckdb")
            store.init_db()

            def boom(market):
                raise RuntimeError("boom")

            pipeline.fetch_spot = boom
            pipeline.get_store = lambda: store
            try:
                with self.assertRaises(RuntimeError):
                    pipeline.sync_spot("HK")
            finally:
                pipeline.fetch_spot, pipeline.get_store = originals
