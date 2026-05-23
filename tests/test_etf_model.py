from __future__ import annotations

from unittest import TestCase

import pandas as pd

from ah_screener.etf_model import (
    classify_etf,
    consolidate_etf_candidates,
    enrich_etf_snapshot,
    infer_etf_cluster,
    infer_etf_track,
    is_hk_listed_etf,
)
from ah_screener.selection import dedup_etf_pool, etf_category_overview


def _etf(symbol: str, name: str, amount: float, market: str = "A", **kw) -> dict:
    row = {
        "market": market,
        "symbol": symbol,
        "name": name,
        "amount": amount,
        "market_cap": amount * 30,
        "pct_change": 0.5,
        "turnover_rate": 1.5,
    }
    row.update(kw)
    return row


class EtfModelTest(TestCase):
    def test_classify_common_categories(self) -> None:
        self.assertEqual(classify_etf("沪深300ETF华夏")[0], "宽基指数ETF")
        self.assertEqual(classify_etf("恒生科技ETF")[0], "跨境ETF")
        self.assertEqual(classify_etf("黄金ETF")[0], "商品ETF")
        self.assertEqual(classify_etf("未知工具")[0], "其他ETF")
        self.assertEqual(infer_etf_track("华夏沪深300ETF")[0], "沪深300")
        self.assertEqual(infer_etf_track("盈富基金")[0], "恒生指数")
        self.assertTrue(is_hk_listed_etf("02800", "盈富基金"))
        self.assertTrue(is_hk_listed_etf("03033", "南方恒生科技"))
        self.assertFalse(is_hk_listed_etf("00700", "腾讯控股"))

    def test_specific_50_tracks_not_swallowed_by_shangzheng50(self) -> None:
        # 上证50's greedy "50etf" keyword must not capture 科创50/创业板50.
        self.assertEqual(infer_etf_track("科创50ETF华夏")[0], "科创50")
        self.assertEqual(infer_etf_track("创业板50ETF华安")[0], "创业板50")
        self.assertEqual(infer_etf_track("上证50ETF华夏")[0], "上证50")

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
        self.assertEqual(broad["etf_track"], "沪深300")
        self.assertEqual(broad["etf_peer_group"], "宽基指数ETF:沪深300")
        self.assertGreater(broad["etf_score"], money["etf_score"])
        self.assertEqual(money["etf_recommendation"], "流动性谨慎")

    def test_consolidates_same_index_and_keeps_best_candidate(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "510300",
                    "name": "沪深300ETF华夏",
                    "amount": 300_000_000,
                    "market_cap": 12_000_000_000,
                    "pct_change": 0.8,
                    "turnover_rate": 2.0,
                },
                {
                    "market": "A",
                    "symbol": "159919",
                    "name": "沪深300ETF",
                    "amount": 80_000_000,
                    "market_cap": 4_000_000_000,
                    "pct_change": 0.7,
                    "turnover_rate": 1.0,
                },
                {
                    "market": "HK",
                    "symbol": "02800",
                    "name": "盈富基金",
                    "amount": 500_000_000,
                    "market_cap": 100_000_000_000,
                    "pct_change": 0.2,
                    "turnover_rate": 1.5,
                },
            ]
        )

        consolidated = consolidate_etf_candidates(df)
        hs300 = consolidated[consolidated["etf_peer_group"].eq("宽基指数ETF:沪深300")].iloc[0]

        self.assertEqual(len(consolidated), 2)
        self.assertEqual(hs300["symbol"], "510300")
        self.assertEqual(hs300["peer_count"], 2)
        self.assertIn("159919", hs300["peer_alternatives"])

    def test_infer_cluster_folds_substitutes_and_defaults_to_track(self) -> None:
        self.assertEqual(infer_etf_cluster("沪深300"), "大盘宽基")
        self.assertEqual(infer_etf_cluster("上证50"), "大盘宽基")
        self.assertEqual(infer_etf_cluster("中证A500"), "大盘宽基")
        self.assertEqual(infer_etf_cluster("中证500"), "中小盘宽基")
        # Unlisted tracks keep their own identity (conservative, R16).
        self.assertEqual(infer_etf_cluster("军工"), "军工")
        self.assertEqual(infer_etf_cluster(""), "其他ETF")

    def test_enrich_adds_cluster_column(self) -> None:
        enriched = enrich_etf_snapshot(pd.DataFrame([_etf("510050", "上证50ETF", 1e8)]))
        self.assertEqual(enriched.iloc[0]["etf_cluster"], "大盘宽基")

    def test_cluster_level_consolidation_folds_correlated_indices(self) -> None:
        df = pd.DataFrame(
            [
                _etf("159338", "中证A500ETF国泰", 4.4e8),
                _etf("510050", "上证50ETF华夏", 3.2e8),
                _etf("510300", "沪深300ETF华泰柏瑞", 5.0e8),
                _etf("510500", "中证500ETF南方", 7.9e8),
            ]
        )
        leaders = consolidate_etf_candidates(df, group_col="etf_cluster")
        clusters = set(leaders["etf_cluster"])
        self.assertEqual(clusters, {"大盘宽基", "中小盘宽基"})
        big = leaders[leaders["etf_cluster"].eq("大盘宽基")].iloc[0]
        self.assertEqual(int(big["peer_count"]), 3)  # A500 + 上证50 + 沪深300 folded

    def test_dedup_pool_keeps_unclassified_etfs_distinct(self) -> None:
        df = pd.DataFrame(
            [
                _etf("159338", "中证A500ETF国泰", 4.4e8),
                _etf("159352", "A500ETF南方", 4.6e8),
                # Two different unclassified ETFs must NOT collapse into one.
                _etf("159001", "自由现金流ETF", 2.0e8),
                _etf("159002", "高端装备ETF", 1.0e8),
            ]
        )
        leaders = dedup_etf_pool(df, top=20)
        symbols = set(leaders["symbol"])
        # A500 pair folds to one; the two unclassified stay separate.
        self.assertEqual(len(leaders), 3)
        self.assertIn("159001", symbols)
        self.assertIn("159002", symbols)
        a500 = leaders[leaders["etf_cluster"].eq("大盘宽基")].iloc[0]
        self.assertEqual(int(a500["peer_count"]), 2)

    def test_enrich_uses_real_technical_score(self) -> None:
        df = pd.DataFrame([_etf("510300", "沪深300ETF华夏", 5e8)])
        tech = pd.DataFrame([{"market": "A", "symbol": "510300", "technical_score": 90.0}])
        enriched = enrich_etf_snapshot(df, technicals=tech)
        self.assertEqual(enriched.iloc[0]["etf_technical_score"], 90.0)
        # No technicals supplied -> neutral 50, not the old pct_change proxy.
        neutral = enrich_etf_snapshot(df)
        self.assertEqual(neutral.iloc[0]["etf_technical_score"], 50.0)

    def test_technical_join_disambiguates_same_code_across_markets(self) -> None:
        # A:510300 and a hypothetical US:510300 must not cross-map on the join.
        df = pd.DataFrame(
            [_etf("510300", "沪深300ETF", 5e8, market="A"), _etf("510300", "US 510300", 5e8, market="US")]
        )
        tech = pd.DataFrame(
            [
                {"market": "A", "symbol": "510300", "technical_score": 90.0},
                {"market": "US", "symbol": "510300", "technical_score": 10.0},
            ]
        )
        enriched = enrich_etf_snapshot(df, technicals=tech).set_index("market")
        self.assertEqual(enriched.loc["A", "etf_technical_score"], 90.0)
        self.assertEqual(enriched.loc["US", "etf_technical_score"], 10.0)

    def test_higher_technical_lifts_etf_score(self) -> None:
        # Same track / liquidity / scale: only the technical score differs.
        df = pd.DataFrame([_etf("AAA", "沪深300ETF华夏", 3e8), _etf("BBB", "沪深300ETF南方", 3e8)])
        tech = pd.DataFrame(
            [
                {"market": "A", "symbol": "AAA", "technical_score": 95.0},
                {"market": "A", "symbol": "BBB", "technical_score": 10.0},
            ]
        )
        enriched = enrich_etf_snapshot(df, technicals=tech).set_index("symbol")
        self.assertGreater(enriched.loc["AAA", "etf_score"], enriched.loc["BBB", "etf_score"])

    def test_category_overview_counts_pool(self) -> None:
        overview = etf_category_overview(
            pd.DataFrame([_etf("510300", "沪深300ETF", 1e8), _etf("511880", "货币ETF", 1e7)])
        )
        self.assertEqual(set(overview["分类"]), {"宽基指数ETF", "货币ETF"})
