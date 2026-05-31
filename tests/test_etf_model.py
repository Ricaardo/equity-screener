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
from ah_screener.selection import (
    dedup_etf_pool,
    dedup_etf_pool_by_exposure,
    etf_category_overview,
    validate_etf_clusters,
)


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
        self.assertNotEqual(infer_etf_track("美国50ETF易方达")[0], "上证50")
        category, keyword = classify_etf("港股通50ETF华泰柏瑞")
        self.assertEqual(infer_etf_track("港股通50ETF华泰柏瑞", category, keyword)[0], "港股")

    def test_classifies_cross_border_lof_and_commodity_tools(self) -> None:
        examples = {
            "港美互联网LOF": ("跨境ETF", "港美互联网"),
            "纳斯达克ETF华夏": ("跨境ETF", "纳斯达克100"),
            "标普500ETF博时": ("跨境ETF", "标普500"),
            "标普信息科技LOF": ("跨境ETF", "标普信息科技"),
            "巴西ETF华夏": ("跨境ETF", "巴西"),
            "沙特ETF南方": ("跨境ETF", "沙特"),
            "国投白银LOF": ("商品ETF", "白银"),
            "豆粕ETF华夏": ("商品ETF", "豆粕"),
            "石油LOF": ("商品ETF", "原油"),
            "粮食ETF鹏华": ("商品ETF", "粮食"),
        }
        for name, (category, track) in examples.items():
            with self.subTest(name=name):
                self.assertEqual(classify_etf(name)[0], category)
                self.assertEqual(infer_etf_track(name)[0], track)

    def test_resource_equity_etfs_stay_out_of_commodity_bucket(self) -> None:
        # These are stock/industry ETFs, not physical or futures commodity products.
        self.assertEqual(classify_etf("有色金属ETF南方")[0], "行业ETF")
        self.assertEqual(classify_etf("黄金股ETF永赢")[0], "行业ETF")
        self.assertEqual(classify_etf("石油ETF国泰")[0], "行业ETF")
        self.assertEqual(infer_etf_track("黄金股ETF永赢")[0], "黄金股")
        self.assertEqual(infer_etf_track("石油ETF国泰")[0], "油气股")
        self.assertNotEqual(classify_etf("标普A股红利ETF华宝")[0], "跨境ETF")

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

    def test_rules_loaded_from_json_preserve_significant_order(self) -> None:
        from ah_screener.etf_model import ETF_CLUSTER_RULES, ETF_RULES, ETF_TRACK_RULES

        self.assertTrue(ETF_RULES and ETF_TRACK_RULES and ETF_CLUSTER_RULES)
        tracks = [r.track for r in ETF_TRACK_RULES]
        # First-match matters: specific "...50" tracks must precede 上证50.
        self.assertLess(tracks.index("科创50"), tracks.index("上证50"))
        self.assertLess(tracks.index("创业板50"), tracks.index("上证50"))

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

    def test_exposure_dedup_uses_holding_overlap(self) -> None:
        pool = pd.DataFrame(
            [
                _etf("513100", "纳指ETF国泰", 5e8),
                _etf("159941", "纳指ETF广发", 4e8),
                _etf("513500", "标普500ETF博时", 3e8),
            ]
        )
        holdings = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "513100",
                    "component_symbol": "NVDA",
                    "component_name": "英伟达",
                    "weight_pct": 9.0,
                },
                {
                    "market": "A",
                    "symbol": "513100",
                    "component_symbol": "MSFT",
                    "component_name": "微软",
                    "weight_pct": 8.0,
                },
                {
                    "market": "A",
                    "symbol": "159941",
                    "component_symbol": "NVDA",
                    "component_name": "英伟达",
                    "weight_pct": 8.8,
                },
                {
                    "market": "A",
                    "symbol": "159941",
                    "component_symbol": "MSFT",
                    "component_name": "微软",
                    "weight_pct": 7.7,
                },
                {
                    "market": "A",
                    "symbol": "513500",
                    "component_symbol": "BRK.B",
                    "component_name": "伯克希尔",
                    "weight_pct": 3.0,
                },
            ]
        )
        leaders = dedup_etf_pool_by_exposure(pool, holdings=holdings, top=10)
        symbols = set(leaders["symbol"])
        self.assertEqual(len(leaders), 2)
        self.assertIn("513100", symbols)
        self.assertIn("513500", symbols)
        nasdaq = leaders[leaders["symbol"].eq("513100")].iloc[0]
        self.assertEqual(int(nasdaq["peer_count"]), 2)
        self.assertIn("159941", nasdaq["peer_alternatives"])
        self.assertEqual(nasdaq["etf_dedup_basis"], "holding_seed")

    def test_exposure_dedup_keeps_active_lofs_with_different_holdings(self) -> None:
        pool = pd.DataFrame(
            [
                _etf("160644", "港美互联网LOF", 5e8),
                _etf("501312", "海外科技LOF", 4e8),
            ]
        )
        holdings = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "160644",
                    "component_symbol": "TSM",
                    "component_name": "台积电",
                    "weight_pct": 9.0,
                },
                {
                    "market": "A",
                    "symbol": "501312",
                    "component_symbol": "QQQ",
                    "component_name": "纳指ETF",
                    "weight_pct": 35.0,
                },
            ]
        )
        leaders = dedup_etf_pool_by_exposure(pool, holdings=holdings, top=10)
        self.assertEqual(set(leaders["symbol"]), {"160644", "501312"})

    def test_exposure_dedup_uses_latest_report_period(self) -> None:
        pool = pd.DataFrame(
            [
                _etf("513100", "纳指ETF国泰", 5e8),
                _etf("159941", "纳指ETF广发", 4e8),
            ]
        )
        holdings = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "513100",
                    "report_period": "2025年4季度股票投资明细",
                    "component_symbol": "NVDA",
                    "component_name": "英伟达",
                    "weight_pct": 50.0,
                },
                {
                    "market": "A",
                    "symbol": "513100",
                    "report_period": "2026年1季度股票投资明细",
                    "component_symbol": "AAPL",
                    "component_name": "苹果",
                    "weight_pct": 50.0,
                },
                {
                    "market": "A",
                    "symbol": "159941",
                    "report_period": "2026年1季度股票投资明细",
                    "component_symbol": "NVDA",
                    "component_name": "英伟达",
                    "weight_pct": 50.0,
                },
            ]
        )
        leaders = dedup_etf_pool_by_exposure(pool, holdings=holdings, top=10)
        self.assertEqual(set(leaders["symbol"]), {"513100", "159941"})
        self.assertFalse(leaders["etf_top_holdings"].str.contains("英伟达").iloc[0])

    def test_exposure_dedup_falls_back_for_commodity_tools(self) -> None:
        pool = pd.DataFrame(
            [
                _etf("501018", "南方原油LOF", 5e8),
                _etf("160723", "嘉实原油LOF", 4e8),
                _etf("161226", "国投白银LOF", 3e8),
            ]
        )
        leaders = dedup_etf_pool_by_exposure(pool, top=10)
        self.assertEqual(len(leaders), 2)
        oil = leaders[leaders["etf_track"].eq("原油")].iloc[0]
        self.assertEqual(int(oil["peer_count"]), 2)

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

    def test_validate_clusters_flags_merge_candidate(self) -> None:
        import numpy as np

        # Two different-cluster tracks whose reps move almost identically -> merge_candidate.
        dates = pd.bdate_range("2024-01-01", periods=200)
        rng = np.random.default_rng(0)
        base = rng.normal(0, 0.01, len(dates)).cumsum() + 10
        pool = pd.DataFrame(
            [
                _etf("159915", "创业板ETF", 5e8),  # cluster 成长科创宽基
                _etf("510050", "上证50ETF", 5e8),  # cluster 大盘宽基
            ]
        )
        prices = pd.concat(
            [
                pd.DataFrame({"market": "A", "symbol": "159915", "trade_date": dates, "close": base}),
                pd.DataFrame(
                    {"market": "A", "symbol": "510050", "trade_date": dates, "close": base * 1.001}
                ),
            ],
            ignore_index=True,
        )
        out = validate_etf_clusters(pool, prices, min_corr=0.9)
        self.assertFalse(out.empty)
        self.assertEqual(out.iloc[0]["relation"], "merge_candidate")
        self.assertGreaterEqual(float(out.iloc[0]["corr"]), 0.9)

    def test_category_overview_counts_pool(self) -> None:
        overview = etf_category_overview(
            pd.DataFrame([_etf("510300", "沪深300ETF", 1e8), _etf("511880", "货币ETF", 1e7)])
        )
        self.assertEqual(set(overview["分类"]), {"宽基指数ETF", "货币ETF"})
