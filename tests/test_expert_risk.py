from __future__ import annotations

from pathlib import Path
from unittest import TestCase

import pandas as pd

from ah_screener.config import Settings
from ah_screener.expert_model import run_expert_model


class ExpertRiskTest(TestCase):
    def test_document_risk_tags_increase_expert_risk_score(self) -> None:
        snapshots = pd.DataFrame(
            [
                {
                    "market": "HK",
                    "symbol": "00700",
                    "trade_date": "2026-05-15",
                    "name": "TENCENT",
                    "board": "HK Main",
                    "last_price": 400,
                    "amount": 100_000_000,
                    "pe_ttm": 20,
                    "pb": 4,
                    "market_cap": 4_000_000_000_000,
                }
            ]
        )
        tags = pd.DataFrame(
            [
                {
                    "market": "HK",
                    "symbol": "00700",
                    "tag_type": "risk",
                    "tag_name": "异常审计意见",
                }
            ]
        )

        results, _ = run_expert_model(
            snapshots=snapshots,
            tags=tags,
            technicals=pd.DataFrame(),
            fundamentals=pd.DataFrame(),
            settings=Settings(db_path=Path("test.duckdb")),
        )

        self.assertGreaterEqual(float(results.iloc[0]["risk_score"]), 30.0)
        self.assertIn("审计意见异常", str(results.iloc[0]["reasons"]))
