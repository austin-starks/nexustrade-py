import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexustrade import signal


def _host_result(request_id: str, *, ok: bool = True, pdf: bool = True) -> str:
    if not ok:
        return json.dumps({"id": request_id, "ok": False, "error": "failed"})
    suffix = ".pdf" if pdf else ".html"
    content_type = "application/pdf" if pdf else "text/html"
    return json.dumps(
        {
            "id": request_id,
            "ok": True,
            "data": {
                "objectKey": f"fetch/{request_id}{suffix}",
                "contentType": content_type,
            },
        }
    )


class SignalProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.results_path = self.root / "host_results.jsonl"
        self.output_path = self.root / "rows.jsonl"
        self.lineage_path = self.root / "lineage.jsonl"
        self.results_path.write_text(
            "\n".join(
                [
                    _host_result("fetch-abc"),
                    _host_result("inventory", pdf=False),
                    _host_result("failed-fetch", ok=False),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write(self, rows: list[dict[str, object]]) -> int:
        with (
            patch.object(signal.host, "HOST_RESULTS_PATH", str(self.results_path)),
            patch.object(signal, "DEFAULT_LINEAGE_PATH", str(self.lineage_path)),
        ):
            return signal.write_rows(rows, path=str(self.output_path))

    def test_accepts_exact_successful_host_fetch_id(self) -> None:
        written = self._write(
            [
                {
                    "timestamp": "2024-01-01",
                    "value": 1,
                    "ticker": "NVDA",
                    "source_id": "fetch-abc",
                }
            ]
        )

        self.assertEqual(written, 1)
        self.assertIn('"source_id":"fetch-abc"', self.output_path.read_text())

    def test_rejects_publisher_filing_id_substituted_for_fetch_id(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "not successful host result keys: 20025310"
        ):
            self._write(
                [
                    {
                        "timestamp": "2024-01-01",
                        "value": 1,
                        "ticker": "NVDA",
                        "source_id": "20025310",
                    }
                ]
            )

        self.assertFalse(self.output_path.exists())

    def test_filing_id_alone_does_not_count_as_provenance(self) -> None:
        with self.assertRaisesRegex(ValueError, "filing_id is a source fact"):
            self._write(
                [
                    {
                        "timestamp": "2024-01-01",
                        "value": 1,
                        "ticker": "NVDA",
                        "filing_id": "20025310",
                    }
                ]
            )

        self.assertFalse(self.output_path.exists())

    def test_rejects_unknown_lineage_source_before_writing(self) -> None:
        self.lineage_path.write_text(
            json.dumps(
                {"output_row_id": "signal-1", "source_ids": ["20025310"]}
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValueError, "not successful host result keys: 20025310"
        ):
            self._write(
                [
                    {
                        "timestamp": "2024-01-01",
                        "value": 1,
                        "ticker": "NVDA",
                        "row_id": "signal-1",
                    }
                ]
            )

        self.assertFalse(self.output_path.exists())

    def test_rejects_unknown_source_in_unreferenced_lineage(self) -> None:
        self.lineage_path.write_text(
            json.dumps(
                {"output_row_id": "unused", "source_ids": ["invented-fetch"]}
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValueError, "not successful host result keys: invented-fetch"
        ):
            self._write(
                [
                    {
                        "timestamp": "2024-01-01",
                        "value": 1,
                        "ticker": "NVDA",
                        "source_id": "fetch-abc",
                    }
                ]
            )

    def test_ignores_malformed_lineage_when_direct_rows_do_not_use_it(self) -> None:
        self.lineage_path.write_text("not-json\n", encoding="utf-8")

        written = self._write(
            [
                {
                    "timestamp": "2024-01-01",
                    "value": 1,
                    "ticker": "NVDA",
                    "source_id": "fetch-abc",
                }
            ]
        )

        self.assertEqual(written, 1)

    def test_accepts_successful_non_pdf_host_result_and_lake_receipt(self) -> None:
        self._write(
            [
                {
                    "timestamp": "2024-01-01",
                    "value": 1,
                    "source_ids": ["fetch-abc", "inventory", "lake-query:lq-1"],
                }
            ]
        )

        self.assertTrue(self.output_path.exists())


if __name__ == "__main__":
    unittest.main()
