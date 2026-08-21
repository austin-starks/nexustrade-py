import importlib
import unittest
from unittest import mock


class ScannedTableExtractionReplayTests(unittest.TestCase):
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
