from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ah_screener import pipeline
from ah_screener.storage import Store


class IndustryMappingTest(TestCase):
    def test_imports_editable_industry_mapping_csv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "test.duckdb")
            store.init_db()
            path = Path(temp_dir) / "industry.csv"
            path.write_text("market,symbol,detailed_industry\nHK,700,互联网平台\n", encoding="utf-8")

            original_get_store = pipeline.get_store
            pipeline.get_store = lambda: store
            try:
                count = pipeline.import_industry_mapping(path)
                tags = store.query_df("SELECT * FROM company_tags")
            finally:
                pipeline.get_store = original_get_store

        self.assertEqual(count, 1)
        self.assertEqual(tags.iloc[0]["symbol"], "00700")
        self.assertEqual(tags.iloc[0]["tag_type"], "industry")
        self.assertEqual(tags.iloc[0]["tag_name"], "互联网平台")
