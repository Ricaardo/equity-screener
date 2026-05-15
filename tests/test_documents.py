from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ah_screener.documents import build_document_records


class DocumentExtractionTest(TestCase):
    def test_extracts_evidence_tags_from_official_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hk_annual_report.txt"
            path.write_text(
                "本集团主营业务包括云服务、人工智能平台和数据中心。"
                "年内研发费用持续投入。主要风险包括客户集中度和技术迭代风险。"
                "公告提示可能延迟刊发年度业绩。",
                encoding="utf-8",
            )

            document, extractions, tags = build_document_records(
                market="HK",
                symbol="700",
                path=path,
                source="hkexnews_pdf",
            )

        self.assertEqual(document.iloc[0]["symbol"], "00700")
        self.assertIn("rd_investment", set(extractions["extract_type"]))
        self.assertIn("AI算力硬件", set(tags["tag_name"]))
        self.assertIn("risk_signal", set(extractions["extract_type"]))
        self.assertIn("延迟刊发财报/业绩", set(tags["tag_name"]))
