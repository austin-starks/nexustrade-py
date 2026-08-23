import importlib
import threading
import time
import unittest
from unittest import mock


class ScannedTableExtractionReplayTests(unittest.TestCase):
    def test_group_prompt_forbids_cross_document_field_copying(self) -> None:
        scanned_table = importlib.import_module("nexustrade.scanned_table")

        self.assertIn(
            "Treat each attachment as an isolated source",
            scanned_table._EXTRACT_PDF_GROUP_SYSTEM,
        )
        self.assertIn(
            "verify every non-null row field against that same attachment",
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
            source_ids = schema["properties"]["documents"]["items"]["properties"][
                "source_id"
            ]["enum"]
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

    def test_schema_batch_rejects_duplicate_or_missing_document_groups(self) -> None:
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
                    "documents": [
                        {"source_id": "a", "rows": []},
                        {"source_id": "a", "rows": []},
                    ]
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

        self.assertIn("duplicated source_id", result["a"]["error"])
        self.assertEqual(result["b"]["rows"], [])

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
