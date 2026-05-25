from __future__ import annotations

from datetime import datetime
from unittest import TestCase

import numpy as np
import pandas as pd

from ah_screener import reporting


class CleanTest(TestCase):
    def test_coerces_numpy_and_missing_values(self) -> None:
        self.assertEqual(reporting._clean(np.int64(7)), 7)
        self.assertIsInstance(reporting._clean(np.int64(7)), int)
        self.assertEqual(reporting._clean(np.float64(1.5)), 1.5)
        self.assertEqual(reporting._clean(True), True)
        self.assertIsNone(reporting._clean(None))
        self.assertIsNone(reporting._clean(np.nan))
        self.assertIsNone(reporting._clean(pd.NaT))

    def test_non_finite_floats_become_none(self) -> None:
        # JSON has no Infinity literal; the payload must stay strict-parseable.
        self.assertIsNone(reporting._clean(float("inf")))
        self.assertIsNone(reporting._clean(float("-inf")))
        self.assertIsNone(reporting._clean(np.float64("nan")))

    def test_timestamp_becomes_iso_date(self) -> None:
        self.assertEqual(reporting._clean(pd.Timestamp("2026-05-24")), "2026-05-24")


class TradingSystemTest(TestCase):
    def test_market_and_etf_category_rules(self) -> None:
        self.assertEqual(reporting._trading_system("US", "stock"), "T+0")
        self.assertEqual(reporting._trading_system("HK", "stock"), "T+0")
        self.assertEqual(reporting._trading_system("A", "stock"), "T+1")
        # A-share ETFs: cross-border/bond/commodity/money are T+0; equity is T+1.
        self.assertEqual(reporting._trading_system("A", "etf", "跨境ETF"), "T+0")
        self.assertEqual(reporting._trading_system("A", "etf", "债券ETF"), "T+0")
        self.assertEqual(reporting._trading_system("A", "etf", "商品ETF"), "T+0")
        self.assertEqual(reporting._trading_system("A", "etf", "货币ETF"), "T+0")
        self.assertEqual(reporting._trading_system("A", "etf", "宽基指数ETF"), "T+1")
        self.assertEqual(reporting._trading_system("A", "etf", "行业ETF"), "T+1")


class ParseJsonListTest(TestCase):
    def test_parses_real_list(self) -> None:
        self.assertEqual(reporting._parse_json_list('["a", "b"]'), ["a", "b"])

    def test_drops_empty_and_none_items(self) -> None:
        self.assertEqual(reporting._parse_json_list('["a", "", null]'), ["a"])

    def test_malformed_json_falls_back_to_raw(self) -> None:
        self.assertEqual(reporting._parse_json_list("not json"), ["not json"])

    def test_empty_inputs(self) -> None:
        self.assertEqual(reporting._parse_json_list(""), [])
        self.assertEqual(reporting._parse_json_list(None), [])

    def test_json_list_helper_reuses_parser(self) -> None:
        self.assertEqual(reporting._json_list('["x", "y"]'), "x、y")


class RecordsTest(TestCase):
    def test_keeps_present_fields_and_parses_list_columns(self) -> None:
        df = pd.DataFrame(
            [{"symbol": "AAPL", "expert_score": np.float64(91.2), "reasons": '["r1", "r2"]'}]
        )
        records = reporting._records(
            df, ["symbol", "expert_score", "missing", "reasons"], list_fields=("reasons",)
        )
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertNotIn("missing", record)
        self.assertEqual(record["symbol"], "AAPL")
        self.assertEqual(record["expert_score"], 91.2)
        self.assertEqual(record["reasons"], ["r1", "r2"])

    def test_empty_frame_returns_empty_list(self) -> None:
        self.assertEqual(reporting._records(pd.DataFrame(), ["symbol"]), [])


class BuildPayloadTest(TestCase):
    def test_empty_frames_produce_strict_json_payload(self) -> None:
        empty = pd.DataFrame()
        payload = reporting._build_payload(
            generated_at=datetime(2026, 5, 24, 18, 30, 0),
            report_date="2026-05-24",
            db_path="/tmp/x.duckdb",
            refined=empty,
            expert=empty,
            potential=empty,
            etf_leaders=empty,
            change_display=empty,
            date_table=pd.DataFrame(columns=["市场", "最新日期"]),
            date_warning="",
            coverage={"证券快照": 0},
            decision_counts=empty,
            bias_notes=["note"],
            markdown_relpath="ah-screening-report-2026-05-24.md",
            appendix_relpath="ah-screening-appendix-2026-05-24.md",
        )
        self.assertEqual(payload["schema_version"], reporting.REPORT_SCHEMA_VERSION)
        self.assertEqual(payload["report_date"], "2026-05-24")
        self.assertEqual(payload["generated_at"], "2026-05-24T18:30:00")
        self.assertIsNone(payload["data_freshness_warning"])
        self.assertEqual(payload["refined_candidates"], [])
        self.assertEqual(payload["counts"]["refined_candidates"], 0)
        self.assertEqual(payload["appendix_report"], "ah-screening-appendix-2026-05-24.md")
        self.assertIn("daily_brief", payload)
        self.assertIn("etf_use_cases", payload)
        # Must be strict-JSON serializable (no NaN/Infinity, no numpy types).
        import json

        json.dumps(payload, allow_nan=False)

    def test_core_candidates_filtered_and_evidence_parsed(self) -> None:
        expert = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "expert_score": np.float64(80.0),
                    "decision": "core_candidate",
                    "theme_matches": '["高股息央国企防御"]',
                    "reasons": '["估值同类分位高", "ROE 稳定"]',
                },
                {
                    "market": "A",
                    "symbol": "000001",
                    "name": "平安银行",
                    "expert_score": np.float64(50.0),
                    "decision": "reject",
                    "theme_matches": "[]",
                    "reasons": "[]",
                },
            ]
        )
        payload = reporting._build_payload(
            generated_at=datetime(2026, 5, 24),
            report_date="2026-05-24",
            db_path="/tmp/x.duckdb",
            refined=pd.DataFrame(),
            expert=expert,
            potential=pd.DataFrame(),
            etf_leaders=pd.DataFrame(),
            change_display=pd.DataFrame(),
            date_table=pd.DataFrame(columns=["市场", "最新日期"]),
            date_warning="",
            coverage={},
            decision_counts=expert,
            bias_notes=[],
            markdown_relpath="r.md",
            appendix_relpath="a.md",
        )
        self.assertEqual(len(payload["core_candidates"]), 1)
        core = payload["core_candidates"][0]
        self.assertEqual(core["symbol"], "600519")
        self.assertEqual(core["theme_matches"], ["高股息央国企防御"])
        self.assertEqual(core["reasons"], ["估值同类分位高", "ROE 稳定"])
        self.assertIn("why_selected", core)
        self.assertIn("verify_before_action", core)
        decisions = {row["decision"]: row["count"] for row in payload["decision_distribution"]}
        self.assertEqual(decisions, {"core_candidate": 1, "reject": 1})

    def test_etf_use_cases_group_tool_records_for_ui(self) -> None:
        etf = pd.DataFrame(
            [
                {
                    "market": "A",
                    "symbol": "513100",
                    "name": "纳指ETF国泰",
                    "etf_category": "跨境ETF",
                    "etf_cluster": "美股大盘",
                    "etf_track": "纳斯达克100",
                    "etf_score": 94.2,
                    "peer_count": 35,
                    "amount": 1_443_000_000,
                    "peer_alternatives": '["A:159941 纳指ETF广发"]',
                },
                {
                    "market": "A",
                    "symbol": "511990",
                    "name": "华宝添益ETF",
                    "etf_category": "货币ETF",
                    "etf_cluster": "现金管理",
                    "etf_track": "货币",
                    "etf_score": 95.1,
                    "peer_count": 1,
                    "amount": 15_508_000_000,
                    "peer_alternatives": "[]",
                },
            ]
        )
        payload = reporting._build_payload(
            generated_at=datetime(2026, 5, 24),
            report_date="2026-05-24",
            db_path="/tmp/x.duckdb",
            refined=pd.DataFrame(),
            expert=pd.DataFrame(),
            potential=pd.DataFrame(),
            etf_leaders=etf,
            change_display=pd.DataFrame(),
            date_table=pd.DataFrame(columns=["市场", "最新日期"]),
            date_warning="",
            coverage={},
            decision_counts=pd.DataFrame(),
            bias_notes=[],
            markdown_relpath="r.md",
            appendix_relpath="a.md",
        )
        by_key = {case["key"]: case for case in payload["etf_use_cases"]}
        self.assertEqual(by_key["cross_border_t0"]["leaders"][0]["symbol"], "513100")
        self.assertEqual(by_key["defensive_cash"]["leaders"][0]["symbol"], "511990")
        self.assertIn("alternatives", payload["etf_leaders"][0])


class GenerateReportArtifactsTest(TestCase):
    def test_emits_json_and_latest_pointers_and_returns_md_path(self) -> None:
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            # Hermetic: point at an empty temp DB so we neither depend on nor mutate
            # the real local database. init_db() creates the schema on first use.
            db_path = output / "test.duckdb"
            with mock.patch.dict(os.environ, {"AH_SCREENER_DB": str(db_path)}):
                md_path = reporting.generate_report(output_dir=output)
            # Backward-compat: callers rely on the Markdown Path return.
            self.assertTrue(str(md_path).endswith(".md"))
            self.assertTrue(md_path.exists())
            date = md_path.stem.replace("ah-screening-report-", "")
            self.assertTrue((output / f"ah-screening-report-{date}.json").exists())
            self.assertTrue((output / f"ah-screening-appendix-{date}.md").exists())
            self.assertTrue((output / "ah-screening-report-latest.json").exists())
            self.assertTrue((output / "ah-screening-report-latest.md").exists())
            self.assertTrue((output / "ah-screening-appendix-latest.md").exists())
            payload = json.loads(
                (output / "ah-screening-report-latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["report_type"], "ah-screening")
            json.dumps(payload, allow_nan=False)


class ValidatePayloadTest(TestCase):
    def _minimal_payload(self) -> dict:
        return {
            key: (
                []
                if key.endswith("candidates") or key in {"etf_leaders", "candidate_changes"}
                else "x"
            )
            for key in reporting.REPORT_REQUIRED_TOP_KEYS
        }

    def test_minimal_payload_passes(self) -> None:
        reporting.validate_report_payload(self._minimal_payload())

    def test_missing_top_key_raises(self) -> None:
        payload = self._minimal_payload()
        del payload["counts"]
        with self.assertRaises(ValueError):
            reporting.validate_report_payload(payload)

    def test_record_missing_required_field_raises(self) -> None:
        payload = self._minimal_payload()
        # refined_candidates record missing the contract field `bucket`.
        payload["refined_candidates"] = [
            {
                "market": "A",
                "trading_system": "T+1",
                "symbol": "600519",
                "name": "贵州茅台",
                "expert_score": 80.0,
            }
        ]
        with self.assertRaises(ValueError):
            reporting.validate_report_payload(payload)


class BuildReportPayloadTest(TestCase):
    def test_returns_validated_payload_without_writing_files(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "t.duckdb")
            with mock.patch.dict(os.environ, {"AH_SCREENER_DB": db}):
                payload = reporting.build_report_payload()
            # Contract holds and no report files were written by this call.
            self.assertEqual(payload["report_type"], "ah-screening")
            self.assertEqual(payload["schema_version"], reporting.REPORT_SCHEMA_VERSION)
            reporting.validate_report_payload(payload)
            self.assertEqual(list(Path(tmp).glob("*.json")), [])
