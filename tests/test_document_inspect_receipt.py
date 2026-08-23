"""Optional inspection receipts and extraction-first wording."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from unittest import mock


class DocumentInspectReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(
            os.environ,
            {
                "NEXUSTRADE_WORK_DIR": self.tmp.name,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.receipts = importlib.reload(
            importlib.import_module("nexustrade.document_inspect_receipt")
        )

    def test_persists_optional_diagnostic_receipt(self) -> None:
        path = self.receipts.persist_inspect_receipt(
            {"kind": "pdf", "pages_inspected": [1], "analysis": {"layout": "table"}}
        )
        assert path is not None
        self.assertTrue(path.is_file())
        self.assertIn("inspect_receipts", str(path))


class ExtractRowsInspectionIndependenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(
            os.environ,
            {
                "NEXUSTRADE_WORK_DIR": self.tmp.name,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.scanned_table = importlib.import_module("nexustrade.scanned_table")

    def test_extract_pdfs_does_not_consume_inspect_receipts(self) -> None:
        result = self.scanned_table.extract_pdfs(
            {}, rows_schema={"ticker": "string"}
        )
        self.assertEqual(result, {})


class ExtractRowsContinuationWordingTests(unittest.TestCase):
    def test_system_prompt_treats_a_page_wrap_as_one_record(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        prompt = scanned_table._EXTRACT_ROWS_SYSTEM
        self.assertIn("page-boundary continuations", prompt)
        self.assertIn("never emit a continuation as another row", prompt)
        self.assertIn("Apply caller task instructions", prompt)
        self.assertIn("never fill a field from world knowledge", prompt)
        self.assertIn("return null", prompt)
        self.assertNotIn("AllianceBernstein", prompt)


if __name__ == "__main__":
    unittest.main()
