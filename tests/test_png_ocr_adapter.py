import importlib
import os
import sys
import types
import unittest
from unittest import mock

class PngOcrAdapterTests(unittest.TestCase):
    def test_211_constructor_options_and_document_only_parse(self):
        module = importlib.import_module("nexustrade.scanned_table")
        seen = []
        class Parser:
            def __init__(self, **options):
                seen.append(options)
            def parse(self, path):
                seen.append(path)
                with open(path, "rb") as stream:
                    self_data = stream.read()
                if self_data != b"public-page-image":
                    raise AssertionError("Input image changed")
                return types.SimpleNamespace(text="Net income 1,234")
        with mock.patch.dict(sys.modules, {"liteparse": types.SimpleNamespace(LiteParse=Parser)}):
            self.assertEqual(module.ocr_png_bytes(b"public-page-image", strict=True), "Net income 1,234")
        self.assertTrue(seen[0]["ocr_enabled"])
        self.assertEqual(seen[0]["output_format"], "text")
        self.assertFalse(os.path.exists(seen[1]))
    def test_strict_failure_is_visible_and_default_remains_best_effort(self):
        module = importlib.import_module("nexustrade.scanned_table")
        class BrokenParser:
            def __init__(self, **options):
                pass
            def parse(self, path):
                raise RuntimeError("OCR runtime unavailable")
        with mock.patch.dict(sys.modules, {"liteparse": types.SimpleNamespace(LiteParse=BrokenParser)}):
            self.assertEqual(module.ocr_png_bytes(b"public-page-image"), "")
            with self.assertRaisesRegex(RuntimeError, "OCR runtime unavailable"):
                module.ocr_png_bytes(b"public-page-image", strict=True)

if __name__ == "__main__":
    unittest.main()
