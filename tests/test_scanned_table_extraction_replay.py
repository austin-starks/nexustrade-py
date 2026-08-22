import importlib
import threading
import time
import unittest
from unittest import mock


class ScannedTableExtractionReplayTests(unittest.TestCase):
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
            mock.patch(
                "nexustrade.document_inspect_receipt.require_prior_inspect_receipt"
            ),
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
            mock.patch(
                "nexustrade.document_inspect_receipt.require_prior_inspect_receipt"
            ),
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


if __name__ == "__main__":
    unittest.main()
