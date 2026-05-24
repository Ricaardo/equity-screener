from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd

from ah_screener.storage import Store


class StoreUpsertTest(TestCase):
    def test_market_snapshots_deduplicate_same_date_key(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "test.duckdb")
            store.init_db()
            rows = pd.DataFrame(
                [
                    {
                        "market": "HK",
                        "symbol": "02825",
                        "asset_type": "etf",
                        "board": "HK ETF",
                        "trade_date": "2026-05-22 16:01:00",
                        "name": "ETF A",
                        "last_price": 1.0,
                        "source": "futu.opend.get_market_snapshot",
                        "updated_at": pd.Timestamp("2026-05-24 00:00:00"),
                    },
                    {
                        "market": "HK",
                        "symbol": "02825",
                        "asset_type": "etf",
                        "board": "HK ETF",
                        "trade_date": "2026-05-22 16:08:00",
                        "name": "ETF A",
                        "last_price": 1.1,
                        "source": "futu.opend.get_market_snapshot",
                        "updated_at": pd.Timestamp("2026-05-24 00:01:00"),
                    },
                ]
            )

            self.assertEqual(store.upsert_dataframe("market_snapshots", rows), 1)
            out = store.query_df("SELECT symbol, trade_date, last_price FROM market_snapshots")

        self.assertEqual(len(out), 1)
        self.assertEqual(str(out["trade_date"].iloc[0]), "2026-05-22 00:00:00")
        self.assertEqual(float(out["last_price"].iloc[0]), 1.1)
