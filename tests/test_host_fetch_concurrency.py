from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from nexustrade import host


class HostFetchConcurrencyTest(unittest.TestCase):
    def test_gateway_fetch_concurrency_defaults_to_bounded_fanout(self):
        env = dict(os.environ)
        env.pop("SANDBOX_FETCH_CONCURRENCY", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(host._gateway_fetch_concurrency(), 4)

    def test_gateway_fetch_concurrency_honors_positive_override(self):
        with patch.dict(os.environ, {"SANDBOX_FETCH_CONCURRENCY": "7"}):
            self.assertEqual(host._gateway_fetch_concurrency(), 7)

    def test_gateway_fetch_concurrency_falls_back_on_invalid_override(self):
        with patch.dict(
            os.environ,
            {"SANDBOX_FETCH_CONCURRENCY": "not-a-number"},
        ):
            self.assertEqual(host._gateway_fetch_concurrency(), 4)


class HostFetchIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results_path = os.path.join(self.tmp.name, "host_results.jsonl")
        self.path_patch = patch.object(host, "HOST_RESULTS_PATH", self.results_path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def write_result(self, row: dict[str, object]) -> None:
        with open(self.results_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def test_same_id_and_url_reuses_the_recorded_result(self):
        self.write_result(
            {
                "id": "pdf_1",
                "ok": False,
                "url": "https://example.gov/a.pdf",
                "status": 403,
                "error": "fetch HTTP 403",
            }
        )

        result = host.fetch({"pdf_1": "https://example.gov/a.pdf"})

        self.assertEqual(result["pdf_1"]["status"], 403)

    def test_same_id_with_changed_url_is_rejected_before_network_use(self):
        self.write_result(
            {
                "id": "pdf_1",
                "ok": False,
                "url": "https://example.gov/a.pdf",
                "status": 403,
                "error": "fetch HTTP 403",
            }
        )

        with self.assertRaisesRegex(ValueError, "already bound"):
            host.fetch({"pdf_1": "https://example.gov/b.pdf"})

    def test_legacy_result_without_request_url_fails_closed(self):
        self.write_result(
            {"id": "pdf_1", "ok": False, "status": 403, "error": "denied"}
        )

        with self.assertRaisesRegex(ValueError, "cannot be verified"):
            host.fetch({"pdf_1": "https://example.gov/a.pdf"})


if __name__ == "__main__":
    unittest.main()
