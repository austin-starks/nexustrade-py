"""Inspect-before-extract receipts and continuation wording."""

from __future__ import annotations

import importlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class DocumentInspectReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(
            os.environ,
            {
                "NEXUSTRADE_WORK_DIR": self.tmp.name,
                "NEXUSTRADE_REQUIRE_INSPECT_BEFORE_EXTRACT": "1",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.receipts = importlib.reload(
            importlib.import_module("nexustrade.document_inspect_receipt")
        )

    def test_same_process_inspect_does_not_satisfy_extract(self) -> None:
        self.receipts.persist_inspect_receipt(
            {"kind": "pdf", "pages_inspected": [1], "analysis": {"layout": "table"}}
        )
        with self.assertRaises(self.receipts.InspectBeforeExtractError):
            self.receipts.require_prior_inspect_receipt()

    def test_prior_process_receipt_allows_extract(self) -> None:
        path = self.receipts.persist_inspect_receipt(
            {"kind": "pdf", "pages_inspected": [1], "analysis": {"layout": "table"}}
        )
        assert path is not None
        past = time.time() - 10
        os.utime(path, (past, past))
        self.receipts.require_prior_inspect_receipt()

    def test_missing_work_dir_skips_the_gate(self) -> None:
        os.environ["NEXUSTRADE_WORK_DIR"] = str(
            Path(self.tmp.name) / "does-not-exist"
        )
        receipts = importlib.reload(
            importlib.import_module("nexustrade.document_inspect_receipt")
        )
        receipts.require_prior_inspect_receipt()


class ExtractRowsInspectGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(
            os.environ,
            {
                "NEXUSTRADE_WORK_DIR": self.tmp.name,
                "NEXUSTRADE_REQUIRE_INSPECT_BEFORE_EXTRACT": "1",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.receipts = importlib.reload(
            importlib.import_module("nexustrade.document_inspect_receipt")
        )
        self.scanned_table = importlib.import_module("nexustrade.scanned_table")

    def test_extract_rows_refuses_without_prior_receipt(self) -> None:
        with self.assertRaises(self.receipts.InspectBeforeExtractError):
            self.scanned_table.extract_rows(
                b"%PDF", schema={"ticker": "string"}
            )

    def test_extract_pdfs_with_schema_refuses_without_prior_receipt(self) -> None:
        with self.assertRaises(self.receipts.InspectBeforeExtractError):
            self.scanned_table.extract_pdfs(
                {}, rows_schema={"ticker": "string"}
            )


class ExtractRowsContinuationWordingTests(unittest.TestCase):
    def test_system_prompt_treats_a_page_wrap_as_one_record(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        prompt = scanned_table._EXTRACT_ROWS_SYSTEM
        self.assertIn("page-boundary continuations", prompt)
        self.assertIn("never emit a continuation as another row", prompt)
        self.assertIn("not a semantic classification task", prompt)
        self.assertIn("return null", prompt)
        self.assertNotIn("AllianceBernstein", prompt)


if __name__ == "__main__":
    unittest.main()
