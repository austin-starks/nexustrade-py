import importlib
import threading
import time
import unittest
from unittest import mock


class ScannedTableExtractionReplayTests(unittest.TestCase):
    def test_group_schema_uses_one_compact_document_item_definition(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        normalized = scanned_table.normalize_rows_schema(
            {
                "ticker": {"type": ["string", "null"]},
                "amount": {"type": ["number", "null"]},
            }
        )

        schema = scanned_table._group_response_schema(
            normalized,
            ["source-a", "source-b", "source-c"],
        )

        documents = schema["properties"]["documents"]
        self.assertEqual(documents["minItems"], 3)
        self.assertEqual(documents["maxItems"], 3)
        item = documents["items"]
        self.assertEqual(
            item["properties"]["source_id"],
            {"type": "string", "enum": ["source-a", "source-b", "source-c"]},
        )
        self.assertEqual(item["properties"]["rows"], normalized["properties"]["rows"])

    def test_group_schema_enforces_a_caller_declared_document_row_minimum(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        normalized = scanned_table.normalize_rows_schema({"ticker": "string"})

        schema = scanned_table._group_response_schema(
            normalized,
            ["source-a", "source-b"],
            min_rows_per_document=1,
        )

        self.assertEqual(
            schema["properties"]["documents"]["items"]["properties"]["rows"]["minItems"],
            1,
        )

    def test_group_schema_preserves_a_stronger_caller_row_minimum(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        normalized = scanned_table.normalize_rows_schema(
            {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "minItems": 5,
                        "items": {
                            "type": "object",
                            "properties": {"ticker": {"type": "string"}},
                        },
                    }
                },
            },
            min_rows=1,
        )

        schema = scanned_table._group_response_schema(
            normalized,
            ["source-a"],
            min_rows_per_document=1,
        )

        self.assertEqual(
            schema["properties"]["documents"]["items"]["properties"]["rows"]["minItems"],
            5,
        )

    def test_rows_schema_rejects_an_invalid_existing_minimum(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        for invalid_minimum in (True, -1):
            with self.subTest(invalid_minimum=invalid_minimum):
                with self.assertRaisesRegex(
                    scanned_table.RowsSchemaError,
                    "minItems must be a non-negative integer",
                ):
                    scanned_table.normalize_rows_schema(
                        {
                            "type": "object",
                            "properties": {
                                "rows": {
                                    "type": "array",
                                    "minItems": invalid_minimum,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "ticker": {"type": "string"}
                                        },
                                    },
                                }
                            },
                        }
                    )

    def test_extract_pdfs_rejects_an_invalid_document_row_minimum(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            scanned_table.extract_pdfs(
                {"filing": b"pdf"},
                rows_schema={"ticker": "string"},
                min_rows_per_document=True,
            )
        with self.assertRaisesRegex(ValueError, "requires rows_schema"):
            scanned_table.extract_pdfs(
                {"filing": b"pdf"},
                document_schema={"report_date": "string"},
                min_rows_per_document=1,
            )
        with self.assertRaisesRegex(ValueError, "requires rows_schema"):
            scanned_table.extract_pdfs(
                {"filing": b"pdf"},
                min_rows_per_document=1,
            )

    def test_group_prompt_allows_relationships_without_cross_document_field_copying(
        self,
    ) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        self.assertIn(
            "compare the mapped sources to identify that relationship",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "Never copy, reuse, or complete a source-local field from another source range",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "verify every non-null row field against that same source range",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "keep those roles distinct",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "never place one column's text into a different semantic field",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "printed date 11/26/13 stays 11/26/13",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "empty ticker or symbol cell stays null",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "Never emit an all-null placeholder row",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "whitespace inside a printed identifier",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "those outputs must preserve the same exact token",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "GOOGL, 0700.HK, BRK.B, and BF-B are well-formed; GOOG L and visually similar non-ASCII letters are not",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )

    def test_extracted_rows_preserves_legacy_positional_construction(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        extracted = scanned_table.ExtractedRows([{"asset": "ACME"}], "source")

        self.assertEqual(extracted.rows, [{"asset": "ACME"}])
        self.assertEqual(extracted.markdown, "source")
        self.assertEqual(extracted.document, {})

    def test_batch_default_bounds_paid_document_concurrency(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        active = 0
        peak = 0
        lock = threading.Lock()

        def extract(*args: object, **kwargs: object) -> list[dict[str, object]]:
            nonlocal active, peak
            del args, kwargs
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return []

        with (
            mock.patch.dict(
                "os.environ", {"NEXUSTRADE_DOCUMENT_MAX_WORKERS": "2"}
            ),
            mock.patch.object(scanned_table, "_gateway_json"),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(scanned_table, "extract_pdf", side_effect=extract),
        ):
            result = scanned_table.extract_pdfs(
                {str(index): b"pdf" for index in range(6)},
                max_attempts=1,
            )

        self.assertEqual(len(result), 6)
        self.assertEqual(peak, 2)

    def test_batch_does_not_multiply_exhausted_schema_retry_budget(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        with (
            mock.patch.object(scanned_table, "_gateway_json"),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table,
                "extract_rows",
                side_effect=scanned_table._RowsStructuringError(
                    "schema retries exhausted"
                ),
            ) as extract_rows,
        ):
            result = scanned_table.extract_pdfs(
                {"filing": b"pdf"},
                rows_schema={"asset": "string"},
                max_workers=1,
                max_attempts=3,
                rows_retries=1,
            )

        self.assertEqual(extract_rows.call_count, 1)
        self.assertEqual(result["filing"]["rows"], [])
        self.assertIn("schema retries exhausted", result["filing"]["error"])

    def test_schema_batch_groups_pdfs_and_stamps_exact_source_identity(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        calls: list[list[str]] = []

        def structured(*args: object, **kwargs: object) -> dict[str, object]:
            del args
            schema = kwargs["json_schema"]
            source_ids = list(
                schema["properties"]["documents"]["items"]["properties"][
                    "source_id"
                ]["enum"]
            )
            calls.append(list(source_ids))
            return {
                "documents": [
                    {
                        "source_id": source_id,
                        "rows": [{"ticker": source_id.upper()}],
                    }
                    for source_id in source_ids
                ]
            }

        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record") as record,
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(host, "gateway_chat_json", side_effect=structured),
        ):
            result = scanned_table.extract_pdfs(
                {f"filing-{index}": b"pdf" for index in range(4)},
                rows_schema={"ticker": "string"},
                instructions="Return the requested rows.",
                documents_per_request=2,
                max_workers=1,
            )

        self.assertEqual(calls, [["filing-0", "filing-1"], ["filing-2", "filing-3"]])
        self.assertEqual(record.call_count, 4)
        self.assertEqual(result["filing-2"]["rows"][0]["source_id"], "filing-2")
        self.assertEqual(result["filing-2"]["rows"][0]["_source_row_index"], 0)

    def test_schema_batch_defaults_to_one_request_for_the_supplied_corpus(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        calls: list[list[str]] = []
        call_options: list[dict[str, object]] = []

        def structured(*args: object, **kwargs: object) -> dict[str, object]:
            del args
            source_ids = list(
                kwargs["json_schema"]["properties"]["documents"]["items"][
                    "properties"
                ]["source_id"]["enum"]
            )
            calls.append(list(source_ids))
            call_options.append(kwargs)
            return {
                "documents": [
                    {"source_id": source_id, "rows": [{"ticker": "ACME"}]}
                    for source_id in source_ids
                ]
            }

        documents = {f"filing-{index}": b"pdf" for index in range(30)}
        combined_mapping = [
            {
                "attachment": "combined-corpus.pdf",
                "source_id": source_id,
                "start_page": index + 1,
                "end_page": index + 1,
            }
            for index, source_id in enumerate(documents)
        ]
        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table,
                "_combine_pdf_group",
                return_value=(b"combined-pdf", combined_mapping),
            ) as combine_pdf_group,
            mock.patch.object(host, "gateway_chat_json", side_effect=structured),
        ):
            result = scanned_table.extract_pdfs(
                documents,
                rows_schema={"ticker": "string"},
                instructions="Return the requested rows.",
            )

        self.assertEqual(calls, [list(documents)])
        combine_pdf_group.assert_called_once()
        self.assertEqual(call_options[0]["timeout_sec"], 930)
        self.assertEqual(call_options[0]["max_transport_attempts"], 1)
        self.assertRegex(
            str(call_options[0]["idempotency_key"]), r"^[a-f0-9]{64}\.0$"
        )
        self.assertEqual(len(result), 30)

    def test_group_response_schema_preserves_string_grammar_constraints(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        captured_schema: dict[str, object] = {}

        def structured(*args: object, **kwargs: object) -> dict[str, object]:
            del args
            captured_schema.update(kwargs["json_schema"])
            return {
                "documents": {
                    "filing": {"rows": [{"ticker": "SUNE"}]},
                    "second": {"rows": [{"ticker": "ACME"}]},
                }
            }

        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(host, "gateway_chat_json", side_effect=structured),
        ):
            result = scanned_table.extract_pdfs(
                {"filing": b"pdf", "second": b"pdf"},
                rows_schema={
                    "ticker": {
                        "type": ["string", "null"],
                        "description": "Exact ASCII exchange symbol.",
                        "pattern": r"^[A-Z0-9]+(?:[.-][A-Z0-9]+)*$",
                    }
                },
                min_rows_per_document=1,
            )

        self.assertEqual(
            captured_schema["properties"]["documents"]["items"]["properties"]["source_id"],
            {"type": "string", "enum": ["filing", "second"]},
        )
        ticker_schema = captured_schema["properties"]["documents"]["items"][
            "properties"
        ]["rows"]["items"]["properties"]["ticker"]
        self.assertEqual(
            captured_schema["properties"]["documents"]["items"]["properties"][
                "rows"
            ]["minItems"],
            1,
        )
        self.assertIn("pattern", ticker_schema, ticker_schema)
        self.assertEqual(
            ticker_schema["pattern"], r"^[A-Z0-9]+(?:[.-][A-Z0-9]+)*$"
        )
        self.assertEqual(
            ticker_schema["description"],
            "Exact ASCII exchange symbol.",
        )
        self.assertEqual(result["filing"]["rows"][0]["ticker"], "SUNE")

    def test_explicit_row_required_fields_remain_non_nullable(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        normalized = scanned_table.normalize_rows_schema(
            {
                "type": "object",
                "properties": {
                    "transaction_type": {"type": "string"},
                    "ticker": {"type": "string"},
                },
                "required": ["transaction_type"],
            }
        )
        properties = normalized["properties"]["rows"]["items"]["properties"]

        self.assertEqual(properties["transaction_type"]["type"], "string")
        self.assertEqual(properties["ticker"]["type"], ["string", "null"])

    def test_group_marks_all_null_placeholder_rows_for_review(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")

        def structured(*args: object, **kwargs: object) -> dict[str, object]:
            del args, kwargs
            return {
                "documents": {
                    "filing": {"rows": [{"ticker": None}]},
                    "second": {"rows": [{"ticker": "ACME"}]},
                }
            }

        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(host, "gateway_chat_json", side_effect=structured),
        ):
            result = scanned_table.extract_pdfs(
                {"filing": b"pdf", "second": b"pdf"},
                rows_schema={"ticker": {"type": ["string", "null"]}},
                min_rows_per_document=1,
            )

        self.assertTrue(result["filing"]["needs_review"])
        self.assertFalse(result["second"]["needs_review"])

    def test_group_structuring_retry_has_a_distinct_idempotency_key(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        options: list[dict[str, object]] = []

        def structured(*args: object, **kwargs: object) -> dict[str, object]:
            del args
            options.append(kwargs)
            if len(options) == 1:
                return {"documents": {}}
            return {
                "documents": {"a": {"rows": []}, "b": {"rows": []}}
            }

        with mock.patch.object(host, "gateway_chat_json", side_effect=structured):
            result = scanned_table._extract_pdf_document_group(
                [("a", b"a"), ("b", b"b")],
                normalized_schema={"type": "object", "properties": {}},
                rows_model="model",
                rows_retries=1,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                extra_fields_by_key=None,
                idempotency_key="group-key",
            )

        self.assertEqual(set(result), {"a", "b"})
        self.assertFalse(result["a"]["needs_review"])
        self.assertIsNone(result["a"]["apparent_table_rows"])
        self.assertEqual(
            [option["idempotency_key"] for option in options],
            ["group-key.0", "group-key.1"],
        )

    def test_schema_batch_rejects_non_positive_explicit_partition_size(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        with self.assertRaisesRegex(ValueError, "must be positive"):
            scanned_table.extract_pdfs(
                {"filing": b"pdf"},
                rows_schema={"ticker": "string"},
                documents_per_request=0,
            )

    def test_schema_batch_partitions_only_at_real_byte_ceiling(
        self,
    ) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        calls: list[list[str]] = []

        def structured(*args: object, **kwargs: object) -> dict[str, object]:
            del args
            source_ids = list(
                kwargs["json_schema"]["properties"]["documents"]["items"][
                    "properties"
                ]["source_id"]["enum"]
            )
            calls.append(list(source_ids))
            return {
                "documents": [
                    {"source_id": source_id, "rows": [{"ticker": "ACME"}]}
                    for source_id in source_ids
                ]
            }

        documents = {"a": b"123", "b": b"456", "c": b"789"}
        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table, "DEFAULT_EXTRACT_ROWS_REQUEST_MAX_BYTES", 6
            ),
            mock.patch.object(host, "gateway_chat_json", side_effect=structured),
        ):
            result = scanned_table.extract_pdfs(
                documents,
                rows_schema={"ticker": "string"},
                instructions="Return the requested rows.",
                max_workers=1,
            )

        self.assertEqual(calls, [["a", "b"], ["c"]])
        self.assertEqual(len(result), 3)

    def test_schema_batch_accepts_host_fetch_results_without_manual_hydration(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        tigris = mock.Mock()
        tigris.read_fetch_result.side_effect = [b"pdf-a", b"pdf-b"]
        receipts = {
            "filing-a": {
                "ok": True,
                "data": {"objectKey": "fetch/a.pdf", "bucket": "bucket"},
            },
            "filing-b": {
                "ok": True,
                "data": {"objectKey": "fetch/b.pdf", "bucket": "bucket"},
            },
        }

        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.dict("sys.modules", {"nexustrade.tigris": tigris}),
            mock.patch.object(
                host,
                "gateway_chat_json",
                return_value={
                    "documents": {
                        "filing-a": {"rows": [{"ticker": "AAA"}]},
                        "filing-b": {"rows": [{"ticker": "BBB"}]},
                    }
                },
            ),
        ):
            result = scanned_table.extract_pdfs(
                receipts,
                rows_schema={"ticker": "string"},
                instructions="Return the requested rows.",
                max_workers=1,
            )

        self.assertEqual(tigris.read_fetch_result.call_count, 2)
        self.assertEqual(list(result), ["filing-a", "filing-b"])
        self.assertEqual(result["filing-a"]["rows"][0]["source_id"], "filing-a")
        self.assertEqual(result["filing-b"]["rows"][0]["ticker"], "BBB")

    def test_failed_fetch_receipt_is_source_local_and_preserves_result_order(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        progress_calls: list[dict[str, object]] = []

        with (
            mock.patch.object(
                scanned_table, "_gateway_json", return_value={"ok": True}
            ) as gateway_json,
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(
                scanned_table,
                "_document_batch_progress",
                side_effect=lambda **kwargs: progress_calls.append(kwargs),
            ),
            mock.patch.object(
                host,
                "gateway_chat_json",
                return_value={
                    "documents": {
                        "good": {"rows": [{"ticker": "GOOD"}]},
                        "later": {"rows": [{"ticker": "LATER"}]},
                    }
                },
            ),
        ):
            result = scanned_table.extract_pdfs(
                {
                    "good": b"pdf-good",
                    "failed": {"ok": False, "error": "HTTP 404"},
                    "later": b"pdf-later",
                },
                rows_schema={"ticker": "string"},
                instructions="Return the requested rows.",
                max_workers=1,
            )

        self.assertEqual(list(result), ["good", "failed", "later"])
        self.assertIn("not successful", result["failed"]["error"])
        self.assertEqual(result["failed"]["rows"], [])
        self.assertEqual(result["later"]["rows"][0]["source_id"], "later")
        self.assertEqual(gateway_json.call_args.args[1]["total"], 3)
        self.assertEqual(progress_calls[-1]["total"], 3)
        self.assertEqual(progress_calls[-1]["completed"], 2)
        self.assertEqual(progress_calls[-1]["failed"], 1)
        self.assertTrue(progress_calls[-1]["done"])

    def test_schema_batch_carries_official_inventory_metadata_mechanically(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")

        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table,
                "extract_pdf_markdown_with_audit",
                return_value=("| Event date |\n|---|\n| 2025-01-02 |", []),
            ),
            mock.patch.object(
                host,
                "gateway_chat_json",
                return_value={"rows": [{"event_date": "2025-01-02"}]},
            ),
        ):
            result = scanned_table.extract_pdfs(
                {"notice-a": b"pdf"},
                rows_schema={"event_date": "string"},
                documents_per_request=2,
                max_workers=1,
                extra_fields_by_key={
                    "notice-a": {"publisher_filing_date": "2025-01-05"}
                },
            )

        self.assertEqual(
            result["notice-a"]["document"]["publisher_filing_date"],
            "2025-01-05",
        )
        self.assertEqual(
            result["notice-a"]["rows"][0]["publisher_filing_date"],
            "2025-01-05",
        )

    def test_schema_batch_preserves_valid_results_when_one_document_is_missing(
        self,
    ) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")

        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                host,
                "gateway_chat_json",
                return_value={
                    "documents": {"a": {"rows": []}}
                },
            ),
        ):
            result = scanned_table.extract_pdfs(
                {"a": b"pdf-a", "b": b"pdf-b"},
                rows_schema={"ticker": "string"},
                documents_per_request=2,
                rows_retries=0,
                max_workers=1,
            )

        self.assertIsNone(result["a"]["error"])
        self.assertFalse(result["a"]["needs_review"])
        self.assertIn("source_id", result["b"]["error"])
        self.assertEqual(result["a"]["rows"], [])
        self.assertEqual(result["b"]["rows"], [])

    def test_schema_batch_collapses_identical_duplicate_without_losing_valid_results(
        self,
    ) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        duplicated = {"source_id": "a", "rows": [{"ticker": "AAA"}]}

        with mock.patch.object(
            host,
            "gateway_chat_json",
            return_value={
                "documents": [
                    duplicated,
                    dict(duplicated),
                    {"source_id": "b", "rows": [{"ticker": "BBB"}]},
                ]
            },
        ):
            result = scanned_table._extract_pdf_document_group(
                [("a", b"a"), ("b", b"b")],
                normalized_schema=scanned_table.normalize_rows_schema(
                    {"ticker": "string"}
                ),
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                extra_fields_by_key=None,
            )

        self.assertIsNone(result["a"]["error"])
        self.assertEqual(result["a"]["rows"][0]["ticker"], "AAA")
        self.assertIsNone(result["b"]["error"])
        self.assertEqual(result["b"]["rows"][0]["ticker"], "BBB")

    def test_schema_batch_isolates_conflicting_duplicate_and_missing_ids(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")

        with mock.patch.object(
            host,
            "gateway_chat_json",
            return_value={
                "documents": [
                    {"source_id": "a", "rows": [{"ticker": "AAA"}]},
                    {"source_id": "a", "rows": [{"ticker": "DIFFERENT"}]},
                    {"source_id": "b", "rows": [{"ticker": "BBB"}]},
                    {"source_id": "unknown", "rows": [{"ticker": "NOPE"}]},
                ]
            },
        ):
            result = scanned_table._extract_pdf_document_group(
                [("a", b"a"), ("b", b"b"), ("c", b"c")],
                normalized_schema=scanned_table.normalize_rows_schema(
                    {"ticker": "string"}
                ),
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                extra_fields_by_key=None,
            )

        self.assertIn("conflicting duplicate", result["a"]["error"])
        self.assertEqual(result["a"]["rows"], [])
        self.assertIsNone(result["b"]["error"])
        self.assertEqual(result["b"]["rows"][0]["ticker"], "BBB")
        self.assertIn("omitted source_id", result["c"]["error"])
        self.assertEqual(result["c"]["rows"], [])

    def test_failed_document_group_does_not_multiply_paid_requests(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        calls: list[list[str]] = []

        def extract_group(
            group: list[tuple[str, bytes]],
            **kwargs: object,
        ) -> dict[str, dict[str, object]]:
            del kwargs
            source_ids = [key for key, _data in group]
            calls.append(source_ids)
            raise host.GatewayChatTransportError(
                "group request exceeded its deadline"
            )

        with (
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record") as record,
            mock.patch.object(scanned_table, "_document_batch_progress") as progress,
            mock.patch.object(
                scanned_table,
                "_extract_pdf_document_group",
                side_effect=extract_group,
            ),
        ):
            result = scanned_table._extract_pdf_document_groups(
                [("a", b"a"), ("b", b"b"), ("c", b"c")],
                request_keys={"a": "key-a", "b": "key-b", "c": "key-c"},
                batch_key="batch",
                normalized_schema={"type": "object", "properties": {}},
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                documents_per_request=3,
                max_workers=1,
                extra_fields_by_key=None,
            )

        self.assertEqual(calls, [["a", "b", "c"]])
        record.assert_not_called()
        self.assertTrue(
            all("exceeded its deadline" in result[key]["error"] for key in result)
        )
        self.assertEqual(progress.call_args.kwargs["completed"], 0)
        self.assertEqual(progress.call_args.kwargs["failed"], 3)
        self.assertTrue(progress.call_args.kwargs["done"])

    def test_child_cache_entries_do_not_replace_exact_parent_request(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        items = [("a", b"a"), ("b", b"b"), ("c", b"c")]

        def keys_for(group: list[tuple[str, bytes]]) -> dict[str, str]:
            peers = "-".join(key for key, _data in group)
            return {key: f"key-{peers}-{key}" for key, _data in group}

        extracted = {
            key: {"document": {}, "rows": [{"source_id": key}], "error": None}
            for key, _data in items
        }
        child_cache = {
            "key-a-a": {"document": {}, "rows": [], "error": None},
            "key-b-b": {"document": {}, "rows": [], "error": None},
            "key-c-c": {"document": {}, "rows": [], "error": None},
        }

        with (
            mock.patch.object(
                scanned_table,
                "_document_result_lookup",
                side_effect=lambda key: child_cache.get(key),
            ) as lookup,
            mock.patch.object(scanned_table, "_document_result_record") as record,
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table,
                "_extract_pdf_document_group",
                return_value=extracted,
            ) as extract_group,
        ):
            scanned_table._extract_pdf_document_groups(
                items,
                request_keys=keys_for(items),
                request_keys_for_group=keys_for,
                batch_key="batch",
                normalized_schema={"type": "object", "properties": {}},
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                documents_per_request=3,
                max_workers=1,
                extra_fields_by_key=None,
            )

        self.assertEqual(extract_group.call_count, 1)
        # Exact-parent lookup stops on its first miss. It never consults the
        # single-document child entries as substitutes for the parent corpus.
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(record.call_count, 3)
        self.assertEqual(
            {call.kwargs["request_key"] for call in record.call_args_list},
            {"key-a-b-c-a", "key-a-b-c-b", "key-a-b-c-c"},
        )

    def test_partial_child_replay_still_attempts_complete_parent(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        items = [("a", b"a"), ("b", b"b")]

        def keys_for(group: list[tuple[str, bytes]]) -> dict[str, str]:
            peers = "-".join(key for key, _data in group)
            return {key: f"key-{peers}-{key}" for key, _data in group}

        extracted = {
            key: {"document": {}, "rows": [{"source_id": key}], "error": None}
            for key, _data in items
        }
        with (
            mock.patch.object(
                scanned_table,
                "_document_result_lookup",
                side_effect=lambda key: (
                    {"document": {}, "rows": [], "error": None}
                    if key == "key-a-a"
                    else None
                ),
            ),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table,
                "_extract_pdf_document_group",
                return_value=extracted,
            ) as extract,
        ):
            scanned_table._extract_pdf_document_groups(
                items,
                request_keys=keys_for(items),
                request_keys_for_group=keys_for,
                batch_key="batch",
                normalized_schema={"type": "object", "properties": {}},
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                documents_per_request=2,
                max_workers=1,
                extra_fields_by_key=None,
            )

        self.assertEqual(
            [key for key, _data in extract.call_args.args[0]], ["a", "b"]
        )

    def test_failed_document_group_preserves_exact_peer_context(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")

        def extract_group(
            group: list[tuple[str, bytes]],
            **kwargs: object,
        ) -> dict[str, dict[str, object]]:
            del kwargs
            raise host.GatewayChatTransportError(
                "group request exceeded its deadline"
            )

        with (
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record") as record,
            mock.patch.object(scanned_table, "_document_batch_progress") as progress,
            mock.patch.object(
                scanned_table,
                "_extract_pdf_document_group",
                side_effect=extract_group,
            ),
        ):
            result = scanned_table._extract_pdf_document_groups(
                [("good-a", b"a"), ("bad", b"bad"), ("good-b", b"b")],
                request_keys={
                    "good-a": "key-good-a",
                    "bad": "key-bad",
                    "good-b": "key-good-b",
                },
                batch_key="batch",
                normalized_schema={"type": "object", "properties": {}},
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                documents_per_request=3,
                max_workers=1,
                extra_fields_by_key=None,
            )

        record.assert_not_called()
        self.assertTrue(
            all("exceeded its deadline" in result[key]["error"] for key in result)
        )
        self.assertEqual(progress.call_args.kwargs["completed"], 0)
        self.assertEqual(progress.call_args.kwargs["failed"], 3)
        self.assertTrue(progress.call_args.kwargs["done"])

    def test_cache_commit_failure_does_not_repeat_paid_extraction(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        extracted = {
            "a": {"document": {}, "rows": [], "error": None},
            "b": {"document": {}, "rows": [], "error": None},
        }

        with (
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(
                scanned_table,
                "_document_result_record",
                side_effect=RuntimeError("cache unavailable"),
            ),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table,
                "_extract_pdf_document_group",
                return_value=extracted,
            ) as extract_group,
        ):
            result = scanned_table._extract_pdf_document_groups(
                [("a", b"a"), ("b", b"b")],
                request_keys={"a": "key-a", "b": "key-b"},
                batch_key="batch",
                normalized_schema={"type": "object", "properties": {}},
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                documents_per_request=2,
                max_workers=1,
                extra_fields_by_key=None,
            )

        self.assertEqual(extract_group.call_count, 1)
        self.assertIn("cache unavailable", result["a"]["error"])
        self.assertIn("cache unavailable", result["b"]["error"])

    def test_permanent_gateway_rejection_does_not_split_and_multiply_calls(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")

        with (
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table,
                "_extract_pdf_document_group",
                side_effect=host.GatewayChatRequestError("budget denied"),
            ) as extract_group,
        ):
            result = scanned_table._extract_pdf_document_groups(
                [("a", b"a"), ("b", b"b"), ("c", b"c")],
                request_keys={"a": "key-a", "b": "key-b", "c": "key-c"},
                batch_key="batch",
                normalized_schema={"type": "object", "properties": {}},
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                documents_per_request=3,
                max_workers=1,
                extra_fields_by_key=None,
            )

        self.assertEqual(extract_group.call_count, 1)
        self.assertTrue(
            all("budget denied" in result[key]["error"] for key in ("a", "b", "c"))
        )

    def test_schema_batch_uses_legacy_ocr_path_when_any_pdf_is_oversized(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        extracted = scanned_table.ExtractedRows(
            rows=[{"ticker": "ACME"}],
            markdown="ocr",
            source_id="a",
        )
        with (
            mock.patch.object(scanned_table, "_gateway_json", return_value={"ok": True}),
            mock.patch.object(scanned_table, "_document_result_lookup", return_value=None),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(scanned_table, "extract_rows", return_value=extracted) as extract,
            mock.patch.object(scanned_table, "_extract_pdf_document_groups") as grouped,
            mock.patch.object(
                scanned_table,
                "_document_request_key",
                wraps=scanned_table._document_request_key,
            ) as request_key,
        ):
            result = scanned_table.extract_pdfs(
                {"a": b"small", "b": b"oversized"},
                rows_schema={"ticker": "string"},
                rows_pdf_max_bytes=5,
                documents_per_request=2,
                max_workers=1,
            )

        grouped.assert_not_called()
        self.assertEqual(extract.call_count, 2)
        self.assertTrue(
            all(
                call.kwargs["documents_per_request"] == 1
                for call in request_key.call_args_list
            )
        )
        self.assertIsNone(result["a"]["error"])
        self.assertIsNone(result["b"]["error"])

    def test_group_system_prompt_changes_invalidate_replay_key(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        common = {
            "markdown": False,
            "max_pages": None,
            "target_schema": None,
            "rows_schema": {"type": "object"},
            "rows_model": "model-a",
            "rows_retries": 1,
            "rows_include_pdf": True,
            "rows_pdf_max_bytes": 1024,
            "documents_per_request": 3,
        }
        before = scanned_table._document_request_key("a", b"pdf", **common)
        with mock.patch.object(
            scanned_table,
            "_EXTRACT_PDF_GROUP_SYSTEM",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM + " changed",
        ):
            after = scanned_table._document_request_key("a", b"pdf", **common)

        self.assertNotEqual(before, after)

    def test_group_peer_corpus_changes_invalidate_replay_key(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        common = {
            "markdown": False,
            "max_pages": None,
            "target_schema": None,
            "rows_schema": {"type": "object"},
            "rows_model": "model-a",
            "rows_retries": 1,
            "rows_include_pdf": True,
            "rows_pdf_max_bytes": 1024,
            "documents_per_request": 2,
        }
        first_context = scanned_table._document_group_context_key(
            [("a", b"same-pdf"), ("b", b"peer-b")]
        )
        second_context = scanned_table._document_group_context_key(
            [("a", b"same-pdf"), ("c", b"peer-c")]
        )

        first = scanned_table._document_request_key(
            "a", b"same-pdf", group_context_key=first_context, **common
        )
        second = scanned_table._document_request_key(
            "a", b"same-pdf", group_context_key=second_context, **common
        )

        self.assertNotEqual(first, second)

    def test_partial_group_replay_does_not_shrink_peer_context(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        extracted = {
            "a": {"document": {}, "rows": [{"version": "fresh"}], "error": None},
            "b": {"document": {}, "rows": [{"version": "fresh"}], "error": None},
        }

        with (
            mock.patch.object(
                scanned_table,
                "_document_result_lookup",
                side_effect=[
                    {"document": {}, "rows": [{"version": "cached"}], "error": None},
                    None,
                ],
            ),
            mock.patch.object(scanned_table, "_document_result_record"),
            mock.patch.object(scanned_table, "_document_batch_progress"),
            mock.patch.object(
                scanned_table,
                "_extract_pdf_document_group",
                return_value=extracted,
            ) as extract_group,
        ):
            result = scanned_table._extract_pdf_document_groups(
                [("a", b"a"), ("b", b"b")],
                request_keys={"a": "key-a", "b": "key-b"},
                batch_key="batch",
                normalized_schema={"type": "object", "properties": {}},
                rows_model="model",
                rows_retries=0,
                rows_pdf_max_bytes=1024,
                instructions="extract rows",
                documents_per_request=2,
                max_workers=1,
                extra_fields_by_key=None,
            )

        self.assertEqual(
            [key for key, _data in extract_group.call_args.args[0]], ["a", "b"]
        )
        self.assertEqual(result["a"]["rows"], [{"version": "fresh"}])

    def test_serial_replay_key_covers_ocr_and_schema_name(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        common = {
            "markdown": False,
            "max_pages": None,
            "target_schema": None,
            "rows_schema": {"type": "object"},
            "rows_model": "model-a",
            "rows_retries": 1,
            "rows_include_pdf": True,
            "rows_pdf_max_bytes": 1024,
        }

        default_key = scanned_table._document_request_key(
            "filing-1",
            b"same-pdf",
            **common,
            rows_force_ocr=True,
            rows_schema_name="extract_rows",
        )
        no_ocr_key = scanned_table._document_request_key(
            "filing-1",
            b"same-pdf",
            **common,
            rows_force_ocr=False,
            rows_schema_name="extract_rows",
        )
        renamed_schema_key = scanned_table._document_request_key(
            "filing-1",
            b"same-pdf",
            **common,
            rows_force_ocr=True,
            rows_schema_name="pelosi_rows",
        )

        self.assertNotEqual(default_key, no_ocr_key)
        self.assertNotEqual(default_key, renamed_schema_key)

    def test_replay_key_covers_document_schema(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        common = {
            "markdown": False,
            "max_pages": None,
            "target_schema": None,
            "rows_schema": {"type": "object"},
            "rows_model": "model-a",
            "rows_retries": 1,
            "rows_include_pdf": True,
            "rows_pdf_max_bytes": 1024,
        }
        first = scanned_table._document_request_key(
            "filing-1",
            b"same-pdf",
            document_schema={"report_date": "string"},
            **common,
        )
        second = scanned_table._document_request_key(
            "filing-1",
            b"same-pdf",
            document_schema={"filed_at": "string"},
            **common,
        )
        self.assertNotEqual(first, second)

    def test_document_and_rows_are_extracted_and_replayed_together(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        recorded: dict[str, dict[str, object]] = {}

        def gateway(
            path: str,
            payload: dict[str, object],
            *,
            timeout_sec: int = 300,
        ) -> dict[str, object]:
            del timeout_sec
            if path == "document-extractions/lookup":
                cached = recorded.get(str(payload["requestKey"]))
                return (
                    {"ok": True, "hit": True, "payload": cached}
                    if cached is not None
                    else {"ok": True, "hit": False}
                )
            if path == "document-extractions/record":
                result_payload = payload["payload"]
                if not isinstance(result_payload, dict):
                    raise AssertionError("record payload must be an object")
                recorded[str(payload["requestKey"])] = result_payload
            return {"ok": True}

        with (
            mock.patch.object(scanned_table, "_gateway_json", side_effect=gateway),
            mock.patch.object(
                scanned_table,
                "extract_pdf_markdown_with_audit",
                return_value=(
                    "Report date: 2026-01-02\n| Asset | Action |\n|---|---|\n| ACME | P |",
                    [{"apparent_table_rows": 1, "needs_review": False}],
                ),
            ),
            mock.patch.object(
                host,
                "gateway_chat_json",
                return_value={
                    "document": {"report_date": "2026-01-02"},
                    "rows": [{"asset": "ACME", "action": "P"}],
                },
            ) as gateway_chat_json,
        ):
            first = scanned_table.extract_rows(
                b"same-pdf",
                schema={"asset": "string", "action": "string"},
                document_schema={"report_date": "string"},
                source_id="filing-1",
            )
            second = scanned_table.extract_rows(
                b"same-pdf",
                schema={"asset": "string", "action": "string"},
                document_schema={"report_date": "string"},
                source_id="filing-1",
            )

        self.assertEqual(gateway_chat_json.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(
            first.document,
            {"report_date": "2026-01-02", "source_id": "filing-1"},
        )
        self.assertEqual(first.rows[0]["source_id"], "filing-1")
        self.assertEqual(first.rows[0]["_source_row_index"], 0)

    def test_repeated_serial_extract_rows_replays_exact_result(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")
        gateway_calls: list[tuple[str, dict[str, object]]] = []
        recorded: dict[str, dict[str, object]] = {}

        def gateway(
            path: str,
            payload: dict[str, object],
            *,
            timeout_sec: int = 300,
        ) -> dict[str, object]:
            del timeout_sec
            gateway_calls.append((path, payload))
            if path == "document-extractions/lookup":
                cached = recorded.get(str(payload["requestKey"]))
                return (
                    {"ok": True, "hit": True, "payload": cached}
                    if cached is not None
                    else {"ok": True, "hit": False}
                )
            if path == "document-extractions/record":
                result_payload = payload["payload"]
                if not isinstance(result_payload, dict):
                    raise AssertionError("record payload must be an object")
                recorded[str(payload["requestKey"])] = result_payload
            return {"ok": True}

        with (
            mock.patch.object(scanned_table, "_gateway_json", side_effect=gateway),
            mock.patch.object(
                scanned_table,
                "extract_pdf_markdown_with_audit",
                return_value=(
                    "| Asset | Action |\n|---|---|\n| ACME | Purchase |",
                    [{"apparent_table_rows": 1, "needs_review": False}],
                ),
            ),
            mock.patch.object(
                host,
                "gateway_chat_json",
                return_value={"rows": [{"asset": "ACME", "action": "Purchase"}]},
            ) as gateway_chat_json,
        ):
            first = scanned_table.extract_rows(
                b"same-pdf",
                schema={"asset": "string", "action": "string"},
                source_id="filing-1",
            )
            second = scanned_table.extract_rows(
                b"same-pdf",
                schema={"asset": "string", "action": "string"},
                source_id="filing-1",
            )

        self.assertEqual(gateway_chat_json.call_count, 1)
        self.assertEqual(second, first)
        self.assertEqual(
            [path for path, _payload in gateway_calls].count(
                "document-extractions/lookup"
            ),
            2,
        )
        self.assertEqual(
            [path for path, _payload in gateway_calls].count(
                "document-extractions/record"
            ),
            1,
        )
        serial_record = next(
            payload
            for path, payload in gateway_calls
            if path == "document-extractions/record"
        )
        self.assertIs(serial_record["trackProgress"], False)
        self.assertNotIn("document-extractions/begin", [path for path, _ in gateway_calls])
        self.assertNotIn(
            "document-extractions/progress", [path for path, _ in gateway_calls]
        )

    def test_repeated_batches_replay_exact_result_within_gateway_scope(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        gateway_calls: list[tuple[str, dict[str, object]]] = []
        recorded: dict[str, dict[str, object]] = {}

        def gateway(
            path: str,
            payload: dict[str, object],
            *,
            timeout_sec: int = 300,
        ) -> dict[str, object]:
            del timeout_sec
            gateway_calls.append((path, payload))
            if path == "document-extractions/lookup":
                cached = recorded.get(str(payload["requestKey"]))
                return (
                    {"ok": True, "hit": True, "payload": cached}
                    if cached is not None
                    else {"ok": True, "hit": False}
                )
            if path == "document-extractions/record":
                result_payload = payload["payload"]
                if not isinstance(result_payload, dict):
                    raise AssertionError("record payload must be an object")
                recorded[str(payload["requestKey"])] = result_payload
            return {"ok": True}

        with (
            mock.patch.object(scanned_table, "_gateway_json", side_effect=gateway),
            mock.patch.object(
                scanned_table,
                "extract_pdf",
                return_value=[{"ticker": "FIRST"}],
            ) as extract_pdf,
        ):
            first = scanned_table.extract_pdfs({"filing": b"same-pdf"})
            second = scanned_table.extract_pdfs({"filing": b"same-pdf"})

        self.assertEqual(extract_pdf.call_count, 1)
        self.assertEqual(first["filing"]["rows"], [{"ticker": "FIRST"}])
        self.assertEqual(second, first)
        self.assertEqual(
            [path for path, _payload in gateway_calls].count(
                "document-extractions/lookup"
            ),
            2,
        )
        records = [
            payload
            for path, payload in gateway_calls
            if path == "document-extractions/record"
        ]
        self.assertEqual(len(records), 1)
        begins = [
            payload
            for path, payload in gateway_calls
            if path == "document-extractions/begin"
        ]
        self.assertEqual(len(begins), 2)
        self.assertEqual(begins[0]["batchKey"], begins[1]["batchKey"])
        final_progress = [
            payload
            for path, payload in gateway_calls
            if path == "document-extractions/progress" and payload["done"] is True
        ]
        self.assertEqual(final_progress[-1]["cacheHits"], 1)

    def test_pdf_transport_exhaustion_falls_back_once_to_ocr_only(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")

        with (
            mock.patch.object(
                scanned_table,
                "extract_pdf_markdown_with_audit",
                return_value=(
                    "| Asset | Action |\n|---|---|\n| ACME | Purchase |",
                    [{"apparent_table_rows": 1, "needs_review": False}],
                ),
            ),
            mock.patch.object(
                host,
                "gateway_chat_json",
                side_effect=[
                    host.GatewayChatTransportError("gateway chat HTTP 524"),
                    {"rows": [{"asset": "ACME", "action": "Purchase"}]},
                ],
            ) as gateway_chat_json,
        ):
            result = scanned_table.extract_rows(
                b"pdf-bytes",
                schema={"asset": "string", "action": "string"},
                source_id="filing-1",
                retries=1,
                include_pdf=True,
            )

        self.assertEqual(gateway_chat_json.call_count, 2)
        self.assertIn("messages", gateway_chat_json.call_args_list[0].kwargs)
        self.assertNotIn("prompt", gateway_chat_json.call_args_list[0].kwargs)
        self.assertEqual(
            gateway_chat_json.call_args_list[1].kwargs["prompt"],
            "source_id: filing-1\n\n| Asset | Action |\n|---|---|\n| ACME | Purchase |",
        )
        self.assertNotIn("messages", gateway_chat_json.call_args_list[1].kwargs)
        self.assertFalse(result.pdf_attached)
        self.assertEqual(result.rows[0]["source_id"], "filing-1")

    def test_serial_extraction_applies_task_instructions(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")
        host = importlib.import_module("nexustrade.host")

        with (
            mock.patch.object(
                scanned_table,
                "extract_pdf_markdown_with_audit",
                return_value=(
                    "| Asset | Action |\n|---|---|\n| ACME | Purchase |",
                    [{"apparent_table_rows": 1, "needs_review": False}],
                ),
            ),
            mock.patch.object(
                host,
                "gateway_chat_json",
                return_value={"rows": [{"asset": "ACME", "action": "Purchase"}]},
            ) as gateway_chat_json,
        ):
            scanned_table.extract_rows(
                b"pdf-bytes",
                schema={"asset": "string", "action": "string"},
                source_id="filing-1",
                instructions="Return purchases only.",
                include_pdf=False,
            )

        prompt = gateway_chat_json.call_args.kwargs["prompt"]
        self.assertIn("# Task instructions\nReturn purchases only.", prompt)
        self.assertIn("# Source document\nsource_id: filing-1", prompt)


if __name__ == "__main__":
    unittest.main()
