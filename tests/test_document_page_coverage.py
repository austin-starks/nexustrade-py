from __future__ import annotations

import importlib
import unittest
from unittest import mock


class DocumentPageCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scanned_table = importlib.import_module("nexustrade.scanned_table")

    @staticmethod
    def confidence(score: float = 0.99) -> dict[str, float]:
        return {
            "average_page_confidence_score": score,
            "minimum_page_confidence_score": score,
        }

    def mistral_rows(
        self, page: dict[str, object]
    ) -> tuple[
        dict[int, list[dict[str, object]]],
        dict[int, str | None],
        dict[int, str],
    ]:
        with mock.patch.object(
            self.scanned_table,
            "_mistral_ocr_document",
            return_value={"pages": [page]},
        ):
            return self.scanned_table._mistral_rows_by_page(
                b"pdf", page_count=1
            )

    def assemble(
        self,
        rows: dict[int, list[dict[str, object]]],
        grades: dict[int, str | None],
        failures: dict[int, str],
    ) -> list[dict[str, object]]:
        return self.scanned_table._assemble_mistral_extract_rows(
            limit=1,
            alias_map={},
            fields=[],
            extra_fields=None,
            by_page=rows,
            grades=grades,
            page_failures=failures,
        )

    def test_prose_page_is_coverage_not_unresolved_extraction(self) -> None:
        rows, grades, failed = self.mistral_rows(
            {
                "index": 0,
                "markdown": "Certification and signature",
                "tables": [],
                "confidence_scores": self.confidence(),
            }
        )

        self.assertEqual(failed, {})
        self.assertEqual(
            self.assemble(rows, grades, failed),
            [
                {
                    "_extract_source": "page_audit",
                    "_page_index": 0,
                    "_page_number": 1,
                    "_page_index_base": 0,
                    "_page_grade": "GOOD",
                    "_needs_review": False,
                    "_reason": "no_apparent_table",
                }
            ],
        )

    def test_unparsed_table_evidence_remains_unresolved(self) -> None:
        rows, grades, failed = self.mistral_rows(
            {
                "index": 0,
                "markdown": "[table]",
                "tables": [{"format": "markdown", "content": "not a table"}],
                "confidence_scores": self.confidence(),
            }
        )

        self.assertEqual(failed, {0: "table_parse_failed"})
        marker = self.assemble(rows, grades, failed)[0]
        self.assertEqual(marker["_extract_source"], "unresolved")
        self.assertTrue(marker["_needs_review"])
        self.assertEqual(marker["_reason"], "table_parse_failed")
        self.assertEqual(marker["_page_number"], 1)

    def test_structured_table_evidence_takes_precedence_over_empty_markdown(self) -> None:
        rows, grades, failed = self.mistral_rows(
            {
                "index": 0,
                "markdown": "",
                "tables": [{"format": "html", "content": "<table></table>"}],
                "confidence_scores": self.confidence(),
            }
        )

        self.assertEqual(failed, {0: "table_parse_failed"})
        marker = self.assemble(rows, grades, failed)[0]
        self.assertEqual(marker["_extract_source"], "unresolved")
        self.assertEqual(marker["_reason"], "table_parse_failed")

    def test_low_confidence_no_table_remains_unresolved(self) -> None:
        rows, grades, failed = self.mistral_rows(
            {
                "index": 0,
                "markdown": "Faint prose",
                "tables": [],
                "confidence_scores": self.confidence(0.1),
            }
        )

        marker = self.assemble(rows, grades, failed)[0]
        self.assertEqual(marker["_extract_source"], "unresolved")
        self.assertTrue(marker["_needs_review"])
        self.assertEqual(marker["_reason"], "no_apparent_table_low_confidence")

    def test_missing_provider_page_remains_unresolved(self) -> None:
        with mock.patch.object(
            self.scanned_table,
            "_mistral_ocr_document",
            return_value={"pages": []},
        ):
            rows, grades, failures = self.scanned_table._mistral_rows_by_page(
                b"pdf", page_count=1
            )

        marker = self.assemble(rows, grades, failures)[0]
        self.assertEqual(marker["_extract_source"], "unresolved")
        self.assertEqual(marker["_reason"], "missing_ocr_page")

    def test_empty_provider_page_remains_unresolved(self) -> None:
        rows, grades, failures = self.mistral_rows(
            {
                "index": 0,
                "markdown": "",
                "tables": [],
                "confidence_scores": self.confidence(),
            }
        )

        marker = self.assemble(rows, grades, failures)[0]
        self.assertEqual(marker["_extract_source"], "unresolved")
        self.assertEqual(marker["_reason"], "empty_ocr_page")

    def test_duplicate_provider_page_is_flagged_once(self) -> None:
        page = {
            "index": 0,
            "markdown": "Transactions",
            "tables": [
                {
                    "format": "markdown",
                    "content": "| Asset | Action |\n|---|---|\n| Example | Purchase |",
                }
            ],
            "confidence_scores": self.confidence(),
        }
        with mock.patch.object(
            self.scanned_table,
            "_mistral_ocr_document",
            return_value={"pages": [page, page]},
        ):
            rows, grades, failures = self.scanned_table._mistral_rows_by_page(
                b"pdf", page_count=1
            )

        assembled = self.assemble(rows, grades, failures)
        self.assertEqual(len(assembled), 1)
        self.assertTrue(assembled[0]["_needs_review"])
        self.assertEqual(assembled[0]["_reason"], "duplicate_ocr_page")

    def test_extracted_rows_carry_both_page_coordinate_systems(self) -> None:
        rows, grades, failed = self.mistral_rows(
            {
                "index": 0,
                "markdown": "Transactions",
                "tables": [
                    {
                        "format": "markdown",
                        "content": "| Asset | Action |\n|---|---|\n| Example | Purchase |",
                    }
                ],
                "confidence_scores": self.confidence(),
            }
        )

        self.assertEqual(failed, {})
        row = self.assemble(rows, grades, failed)[0]
        self.assertEqual(row["_page_index"], 0)
        self.assertEqual(row["_page_number"], 1)
        self.assertEqual(row["_page_index_base"], 0)

    def test_page_audit_carries_both_page_coordinate_systems(self) -> None:
        with mock.patch.object(
            self.scanned_table,
            "_mistral_ocr_document",
            return_value={
                "pages": [
                    {
                        "index": 0,
                        "markdown": "Certification",
                        "tables": [],
                        "confidence_scores": self.confidence(),
                    }
                ]
            },
        ):
            _markdown, audit = self.scanned_table._mistral_document_markdown_with_audit(
                b"pdf", page_count=1
            )

        self.assertEqual(audit[0]["page_index"], 0)
        self.assertEqual(audit[0]["page_number"], 1)
        self.assertEqual(audit[0]["page_index_base"], 0)


class InspectDocumentPageCoordinatesTests(unittest.TestCase):
    def test_pdf_attachment_renders_only_requested_pages(self) -> None:
        inspect_module = importlib.import_module("nexustrade.inspect_document")
        host = importlib.import_module("nexustrade.host")
        with (
            mock.patch.object(
                inspect_module,
                "render_pdf_page_png",
                side_effect=lambda _data, page_index: f"page-{page_index}".encode(),
            ) as render,
            mock.patch.object(
                host,
                "gateway_image_url_part",
                side_effect=lambda data, mime_type: {
                    "type": "image_url",
                    "data": data,
                    "mime_type": mime_type,
                },
            ),
        ):
            attachments, kind = inspect_module._gateway_attachments(
                b"whole-pdf",
                "pdf",
                pages_inspected=[2, 4],
            )

        self.assertEqual(
            [call.args[1] for call in render.call_args_list],
            [1, 3],
        )
        self.assertEqual(
            [attachment["data"] for attachment in attachments],
            [b"page-1", b"page-3"],
        )
        self.assertEqual(kind, "rendered_pdf_pages(2,4)")

    def test_result_maps_one_based_inspection_to_zero_based_audit(self) -> None:
        inspect_module = importlib.import_module("nexustrade.inspect_document")
        host = importlib.import_module("nexustrade.host")
        analysis = {
            "layout": "mixed",
            "has_tables": True,
            "has_page_continuations": False,
            "observed_fields": [],
            "instrument_suffix_codes_seen": [],
            "ambiguities": [],
            "evidence_locations": [],
            "notes": "",
        }
        with (
            mock.patch.object(inspect_module, "_detect_document_kind", return_value="pdf"),
            mock.patch.object(inspect_module, "_pdf_page_count", return_value=3),
            mock.patch.object(inspect_module, "probe_pdf", return_value={"pages": 3}),
            mock.patch.object(
                inspect_module,
                "_gateway_attachments",
                return_value=([{"type": "image_url"}], "rendered_pdf_pages(2)"),
            ),
            mock.patch.object(host, "gateway_multimodal_messages", return_value=[]),
            mock.patch.object(host, "gateway_chat_json", return_value=analysis),
            mock.patch(
                "nexustrade.document_inspect_receipt.persist_inspect_receipt"
            ),
        ):
            result = inspect_module.inspect_document(b"pdf", pages=[2])

        self.assertEqual(result["pages_inspected"], [2])
        self.assertEqual(
            result["page_coordinates"],
            {
                "pages_inspected_one_based": [2],
                "pages_inspected_zero_based": [1],
                "inspection_page_base": 1,
                "page_audit_index_base": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
