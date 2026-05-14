from __future__ import annotations

from unittest import TestCase

from ah_screener.fundamentals import _innovation_efficiency_score, _quality_scores


class FundamentalsTest(TestCase):
    def test_quality_scores_reward_roe_cashflow_and_balance_sheet_strength(self) -> None:
        strong = _quality_scores(
            roe=18,
            gross_margin=60,
            net_margin=25,
            debt_asset_ratio=25,
            cashflow_to_profit=1.2,
            revenue_yoy=30,
            net_profit_yoy=35,
        )
        weak = _quality_scores(
            roe=2,
            gross_margin=10,
            net_margin=1,
            debt_asset_ratio=85,
            cashflow_to_profit=0.2,
            revenue_yoy=-10,
            net_profit_yoy=-20,
        )

        self.assertGreater(strong[4], 90)
        self.assertLess(weak[4], 20)
        self.assertIn("ROE偏低", weak[5])
        self.assertIn("资产负债率偏高", weak[5])

    def test_innovation_efficiency_has_bounded_score(self) -> None:
        efficient = _innovation_efficiency_score(rd_expense_ratio=8, capex_to_operating_cashflow=0.2)
        inefficient = _innovation_efficiency_score(
            rd_expense_ratio=0,
            capex_to_operating_cashflow=2.0,
        )

        self.assertGreaterEqual(inefficient, 0)
        self.assertLessEqual(inefficient, efficient)
        self.assertLessEqual(efficient, 100)
