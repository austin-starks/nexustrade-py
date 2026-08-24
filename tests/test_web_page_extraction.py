from __future__ import annotations

import json
import sys
import unittest
from types import ModuleType
from unittest import mock

from nexustrade import scanned_table


SCHEMA = {
    "type": "object",
    "properties": {
        "quality": {"type": "string", "enum": ["substantive", "shell"]},
        "facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["quality", "facts"],
    "additionalProperties": False,
}


class WebPageExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lookup = mock.patch.object(
            scanned_table, "_document_result_lookup", return_value=None
        )
        self.record = mock.patch.object(scanned_table, "_document_result_record")
        self.progress = mock.patch.object(scanned_table, "_document_batch_progress")
        self.lookup.start()
        self.record_mock = self.record.start()
        self.progress.start()
        self.addCleanup(self.lookup.stop)
        self.addCleanup(self.record.stop)
        self.addCleanup(self.progress.stop)

    @staticmethod
    def _response(*args: object, **kwargs: object) -> dict[str, object]:
        prompt = kwargs.get("prompt")
        assert isinstance(prompt, str)
        payload = json.loads(prompt)
        return {
            "documents": [
                {
                    "source_id": page["source_id"],
                    "quality": "substantive" if page["visible_text"] else "shell",
                    "facts": [page["visible_text"]] if page["visible_text"] else [],
                }
                for page in payload["documents"]
            ]
        }

    def test_removes_page_chrome_and_preserves_metadata(self) -> None:
        html = """
        <html><head>
          <title>Rate decision</title>
          <meta name="description" content="Rates held at 4 percent">
          <meta property="article:published_time" content="2026-08-24">
          <script>expensive secret noise</script>
        </head><body>
          <nav>seventeen unrelated links</nav>
          <main><article>The committee held its policy rate at 4 percent.</article></main>
          <footer>terms and tracking</footer>
        </body></html>
        """
        with mock.patch(
            "nexustrade.host.gateway_chat_json", side_effect=self._response
        ) as chat:
            result = scanned_table.extract_web_pages(
                {"central-bank": html},
                instructions="Extract the announced policy decision.",
                schema=SCHEMA,
                max_workers=1,
            )

        prompt = json.loads(chat.call_args.kwargs["prompt"])
        page = prompt["documents"][0]
        self.assertEqual(page["title"], "Rate decision")
        self.assertEqual(page["description"], "Rates held at 4 percent")
        self.assertEqual(page["published_at_hint"], "2026-08-24")
        self.assertIn("held its policy rate", page["visible_text"])
        self.assertNotIn("unrelated links", page["visible_text"])
        self.assertNotIn("secret noise", page["visible_text"])
        self.assertIsNone(result["central-bank"]["error"])

        response_schema = chat.call_args.kwargs["json_schema"]
        document_schema = response_schema["properties"]["documents"]["items"]
        self.assertEqual(document_schema["properties"]["facts"]["type"], "array")

    def test_prefers_content_scope_and_preserves_article_header(self) -> None:
        html = """
        <html><body>
          <header>Global account links and subscription promotion</header>
          <main><article>
            <header><h1>Reservoir update</h1><time datetime="2026-08-24">Monday</time></header>
            <p>Storage reached 81 percent.</p>
          </article><aside>Related stories</aside></main>
          <div>Cookie preferences and unrelated body text</div>
        </body></html>
        """
        with mock.patch(
            "nexustrade.host.gateway_chat_json", side_effect=self._response
        ) as chat:
            scanned_table.extract_web_pages(
                {"reservoir": html},
                instructions="Extract the storage update.",
                schema=SCHEMA,
                max_workers=1,
            )

        page = json.loads(chat.call_args.kwargs["prompt"])["documents"][0]
        self.assertIn("Reservoir update", page["visible_text"])
        self.assertIn("Storage reached 81 percent", page["visible_text"])
        self.assertNotIn("Global account links", page["visible_text"])
        self.assertNotIn("Cookie preferences", page["visible_text"])

    def test_reads_successful_host_fetch_receipt(self) -> None:
        receipt = {
            "id": "safety-bulletin",
            "ok": True,
            "data": {
                "contentType": "text/html; charset=utf-8",
                "url": "https://example.test/bulletin",
                "objectKey": "sandbox/fetch/safety-bulletin",
            },
        }
        tigris = ModuleType("nexustrade.tigris")
        read_fetch = mock.Mock(
            return_value=b"<html><body><main>Valve inspection due Friday.</main></body></html>"
        )
        tigris.read_fetch_result = read_fetch  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"nexustrade.tigris": tigris}), mock.patch(
            "nexustrade.host.gateway_chat_json", side_effect=self._response
        ) as chat:
            result = scanned_table.extract_web_pages(
                {"safety-bulletin": receipt},
                instructions="Extract the maintenance deadline.",
                schema=SCHEMA,
                max_workers=1,
            )

        read_fetch.assert_called_once_with(receipt)
        prompt = json.loads(chat.call_args.kwargs["prompt"])
        self.assertEqual(prompt["documents"][0]["url"], receipt["data"]["url"])
        self.assertIn("Friday", result["safety-bulletin"]["document"]["facts"][0])

    def test_non_html_fetch_is_an_explicit_source_error(self) -> None:
        receipt = {
            "id": "filing",
            "ok": True,
            "data": {
                "contentType": "application/pdf",
                "objectKey": "sandbox/fetch/filing",
            },
        }
        with mock.patch("nexustrade.host.gateway_chat_json") as chat:
            result = scanned_table.extract_web_pages(
                {"filing": receipt},
                instructions="Extract rows.",
                schema=SCHEMA,
            )
        self.assertIn("not HTML", result["filing"]["error"])
        chat.assert_not_called()

    def test_batches_pages_and_keeps_one_result_per_source(self) -> None:
        pages = {
            "one": "<html><body>First fact</body></html>",
            "two": "<html><body>Second fact</body></html>",
            "three": "<html><body>Third fact</body></html>",
        }
        with mock.patch(
            "nexustrade.host.gateway_chat_json", side_effect=self._response
        ) as chat:
            result = scanned_table.extract_web_pages(
                pages,
                instructions="Extract each fact.",
                schema=SCHEMA,
                documents_per_request=2,
                max_workers=1,
            )
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(list(result), list(pages))
        self.assertTrue(all(item["error"] is None for item in result.values()))
        self.assertEqual(self.record_mock.call_count, 3)

    def test_replays_success_without_an_llm_call(self) -> None:
        replay = {
            "document": {
                "source_id": "cached",
                "quality": "substantive",
                "facts": ["already extracted"],
            },
            "error": None,
        }
        with mock.patch.object(
            scanned_table, "_document_result_lookup", return_value=replay
        ), mock.patch("nexustrade.host.gateway_chat_json") as chat:
            result = scanned_table.extract_web_pages(
                {"cached": "<html><body>already extracted</body></html>"},
                instructions="Extract the fact.",
                schema=SCHEMA,
            )
        self.assertEqual(result["cached"], replay)
        chat.assert_not_called()

    def test_replay_identity_includes_prepared_source_metadata(self) -> None:
        html = "<html><body><main>Same bytes</main></body></html>"
        with mock.patch(
            "nexustrade.host.gateway_chat_json", side_effect=self._response
        ):
            scanned_table.extract_web_pages(
                {"source": {"html": html, "url": "https://example.test/one"}},
                instructions="Extract the fact.",
                schema=SCHEMA,
                max_workers=1,
            )
            first_key = self.record_mock.call_args.kwargs["request_key"]
            scanned_table.extract_web_pages(
                {"source": {"html": html, "url": "https://example.test/two"}},
                instructions="Extract the fact.",
                schema=SCHEMA,
                max_workers=1,
            )
            second_key = self.record_mock.call_args.kwargs["request_key"]

        self.assertNotEqual(first_key, second_key)

    def test_invalid_source_mapping_fails_the_whole_group_explicitly(self) -> None:
        with mock.patch(
            "nexustrade.host.gateway_chat_json",
            return_value={
                "documents": [
                    {"source_id": "wrong", "quality": "shell", "facts": []}
                ]
            },
        ):
            result = scanned_table.extract_web_pages(
                {
                    "one": "<html><body>First</body></html>",
                    "two": "<html><body>Second</body></html>",
                },
                instructions="Extract facts.",
                schema=SCHEMA,
                retries=0,
                max_workers=1,
            )
        self.assertIn("unknown source_id", result["one"]["error"])
        self.assertIn("unknown source_id", result["two"]["error"])

    def test_repairs_nonexact_evidence_excerpt_before_recording(self) -> None:
        evidence_schema = {
            "type": "object",
            "properties": {
                "observations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"evidence_excerpt": {"type": "string"}},
                        "required": ["evidence_excerpt"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["observations"],
            "additionalProperties": False,
        }
        responses = [
            {
                "documents": [
                    {
                        "source_id": "notice",
                        "observations": [{"evidence_excerpt": "Deadline ... Friday"}],
                    }
                ]
            },
            {
                "documents": [
                    {
                        "source_id": "notice",
                        "observations": [
                            {"evidence_excerpt": "Deadline is Friday"}
                        ],
                    }
                ]
            },
        ]
        with mock.patch(
            "nexustrade.host.gateway_chat_json", side_effect=responses
        ) as chat:
            result = scanned_table.extract_web_pages(
                {"notice": "<html><body>Deadline is Friday</body></html>"},
                instructions="Extract the deadline with exact evidence.",
                schema=evidence_schema,
                retries=1,
                max_workers=1,
            )

        self.assertEqual(chat.call_count, 2)
        repair_prompt = json.loads(chat.call_args_list[1].kwargs["prompt"])
        self.assertIn("validation_feedback", repair_prompt)
        self.assertEqual(
            result["notice"]["document"]["observations"][0]["evidence_excerpt"],
            "Deadline is Friday",
        )
        self.record_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
