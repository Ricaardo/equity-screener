from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import TestCase, mock


class IngestFailureTest(TestCase):
    def test_record_and_query_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "t.duckdb")
            with mock.patch.dict(os.environ, {"AH_SCREENER_DB": db}):
                from ah_screener import pipeline

                self.assertTrue(pipeline.ingest_failure_status().empty)
                pipeline._record_ingest_failure("sync_spot", "akshare timeout")
                pipeline._record_ingest_failure("history", "OpenD unreachable")
                df = pipeline.ingest_failure_status()
                self.assertEqual(len(df), 2)
                self.assertEqual(set(df["step"]), {"sync_spot", "history"})

    def test_recording_failure_never_raises(self) -> None:
        # Best-effort: a broken DB path must not turn observability into a crash.
        with mock.patch.dict(os.environ, {"AH_SCREENER_DB": "/nonexistent-dir/x/y.duckdb"}):
            from ah_screener import pipeline

            pipeline._record_ingest_failure("sync_spot", "boom")  # must not raise
