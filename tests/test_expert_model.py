from __future__ import annotations

from pathlib import Path
from unittest import TestCase

import pandas as pd

from ah_screener.config import Settings
from ah_screener.expert_model import run_expert_model


def _snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "market": "A",
                "symbol": "600000",
                "name": "测试股份",
                "trade_date": "2026-05-24",
                "asset_type": "stock",
                "board": "主板",
                "last_price": 10.0,
                "amount": 100_000_000.0,
                "market_cap": 10_000_000_000.0,
                "pe_ttm": 20.0,
                "pb": 2.0,
                "source": "test",
            }
        ]
    )


class ExpertModelTest(TestCase):
    def test_theme_match_does_not_lift_numeric_score(self) -> None:
        settings = Settings(db_path=Path(":memory:"))
        no_theme, _ = run_expert_model(
            _snapshot(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), settings
        )
        with_theme, _ = run_expert_model(
            _snapshot(),
            pd.DataFrame(
                [
                    {
                        "market": "A",
                        "symbol": "600000",
                        "tag_type": "theme",
                        "tag_name": "AI算力硬件",
                        "evidence_level": "B",
                        "source": "test",
                    }
                ]
            ),
            pd.DataFrame(),
            pd.DataFrame(),
            settings,
        )

        self.assertGreater(
            float(with_theme.iloc[0]["theme_score"]), float(no_theme.iloc[0]["theme_score"])
        )
        self.assertAlmostEqual(
            float(with_theme.iloc[0]["expert_score"]),
            float(no_theme.iloc[0]["expert_score"]),
            places=6,
        )
        self.assertIn("不计入综合分", with_theme.iloc[0]["reasons"])
