from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.etf_model import classify_etf, enrich_etf_snapshot


class EtfModelTest(TestCase):
    def test_classify_common_categories(self) -> None:
        self.assertEqual(classify_etf("沪深300ETF华夏")[0], "宽基指数ETF")
        self.assertEqual(classify_etf("恒生科技ETF")[0], "跨境ETF")
        self.assertEqual(classify_etf("黄金ETF")[0], "商品ETF")
        self.assertEqual(classify_etf("未知工具")[0], "其他ETF")

    def test_enrich_scores_liquid_broad_index(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "510300",
                    "name": "沪深300ETF",
                    "amount": 500_000_000,
                    "market_cap": 20_000_000_000,
                    "pct_change": 1.0,
                    "turnover_rate": 2.0,
                },
                {
                    "market": "A",
                    "symbol": "511880",
                    "name": "货币ETF",
                    "amount": 10_000_000,
                    "market_cap": 1_000_000_000,
                    "pct_change": 0.0,
                    "turnover_rate": 0.1,
                },
            ]
        )

        enriched = enrich_etf_snapshot(df)
        broad = enriched[enriched["symbol"].eq("510300")].iloc[0]
        money = enriched[enriched["symbol"].eq("511880")].iloc[0]

        self.assertEqual(broad["etf_category"], "宽基指数ETF")
        self.assertGreater(broad["etf_score"], money["etf_score"])
        self.assertEqual(money["etf_recommendation"], "流动性谨慎")
