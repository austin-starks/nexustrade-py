from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

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

    def test_unknown_form_field_fails_with_supported_html_form_shape(self):
        with patch.object(host, "_gateway_fetch_many") as gateway:
            with self.assertRaisesRegex(
                ValueError,
                "URL-encode its fields into `body`.*`source_receipt`",
            ):
                host.fetch(
                    {
                        "search": {
                            "url": "https://example.gov/search",
                            "method": "POST",
                            "form": {"LastName": "Example"},
                        }
                    }
                )
        gateway.assert_not_called()

    def test_post_shape_is_normalized_before_gateway_use(self):
        gateway_result = {
            "search": {
                "id": "search",
                "ok": False,
                "url": "https://example.gov/search",
                "error": "example",
            }
        }
        with patch.object(
            host,
            "_gateway_fetch_many",
            return_value=gateway_result,
        ) as gateway:
            result = host.fetch(
                {
                    "search": {
                        "url": " https://example.gov/search ",
                        "method": "post",
                        "body": "LastName=Example",
                        "content_type": "application/x-www-form-urlencoded",
                        "source_receipt": "signed-parent",
                    }
                }
            )

        self.assertEqual(result, gateway_result)
        gateway.assert_called_once_with(
            {
                "search": {
                    "url": "https://example.gov/search",
                    "method": "POST",
                    "body": "LastName=Example",
                    "content_type": "application/x-www-form-urlencoded",
                    "source_receipt": "signed-parent",
                }
            }
        )

    def test_post_without_parent_receipt_fails_before_gateway_use(self):
        with patch.object(host, "_gateway_fetch_many") as gateway:
            with self.assertRaisesRegex(ValueError, "POST requires source_receipt"):
                host.fetch(
                    {
                        "search": {
                            "url": "https://example.gov/search",
                            "method": "POST",
                            "body": "LastName=Example",
                        }
                    }
                )
        gateway.assert_not_called()


class HostFetchGatewayOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results_path = os.path.join(self.tmp.name, "host_results.jsonl")
        self.requests_path = os.path.join(self.tmp.name, "host_requests.jsonl")
        self.path_patch = patch.object(host, "HOST_RESULTS_PATH", self.results_path)
        self.requests_patch = patch.object(
            host, "HOST_REQUESTS_PATH", self.requests_path
        )
        self.path_patch.start()
        self.requests_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.requests_patch.stop)
        self.env = patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://gateway.example.test/v1",
                "OPENAI_API_KEY": "sandbox-key",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sleep = patch.object(host.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def recorded_ids(self) -> list[str]:
        if not os.path.exists(self.results_path):
            return []
        ids: list[str] = []
        with open(self.results_path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    ids.append(json.loads(line)["id"])
        return ids

    def test_no_gateway_raises_and_does_not_write_broker_requests(self) -> None:
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "", "OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "not a fetch transport"):
                host.fetch({"pdf_1": "https://example.gov/a.pdf"})
        self.assertFalse(os.path.exists(self.requests_path))

    def test_one_transient_502_does_not_discard_siblings(self) -> None:
        attempts = {"a": 0, "b": 0, "c": 0}

        def urlopen(req: Request, timeout: int = 0) -> io.BytesIO:
            payload = json.loads(req.data.decode("utf-8"))
            rid = payload["id"]
            attempts[rid] += 1
            if rid == "b" and attempts[rid] == 1:
                raise HTTPError(
                    req.full_url,
                    502,
                    "Bad Gateway",
                    hdrs=None,
                    fp=io.BytesIO(b'{"error":{"message":"blip"}}'),
                )
            body = json.dumps(
                {
                    "id": rid,
                    "ok": True,
                    "url": payload["url"],
                    "status": 200,
                    "data": {"receipt": f"r-{rid}"},
                }
            )
            return io.BytesIO(body.encode("utf-8"))

        with patch.object(host.urllib.request, "urlopen", side_effect=urlopen):
            result = host.fetch(
                {
                    "a": "https://example.gov/a.pdf",
                    "b": "https://example.gov/b.pdf",
                    "c": "https://example.gov/c.pdf",
                }
            )

        self.assertTrue(result["a"]["ok"])
        self.assertTrue(result["b"]["ok"])
        self.assertTrue(result["c"]["ok"])
        self.assertEqual(attempts["b"], 2)
        self.assertEqual(sorted(self.recorded_ids()), ["a", "b", "c"])
        self.assertFalse(os.path.exists(self.requests_path))

    def test_persistent_502_records_ok_false_and_does_not_exit(self) -> None:
        def urlopen(req: Request, timeout: int = 0) -> io.BytesIO:
            raise HTTPError(
                req.full_url,
                502,
                "Bad Gateway",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"message":"down"}}'),
            )

        with patch.object(host.urllib.request, "urlopen", side_effect=urlopen):
            result = host.fetch({"pdf_1": "https://example.gov/a.pdf"})

        self.assertFalse(result["pdf_1"]["ok"])
        self.assertEqual(result["pdf_1"]["status"], 502)
        self.assertIn("gateway HTTP 502", result["pdf_1"]["error"])
        self.assertEqual(self.recorded_ids(), ["pdf_1"])
        self.assertFalse(os.path.exists(self.requests_path))

    def test_transport_error_retries_then_returns_row(self) -> None:
        calls = {"n": 0}

        def urlopen(req: Request, timeout: int = 0) -> io.BytesIO:
            calls["n"] += 1
            if calls["n"] < host._GATEWAY_FETCH_ATTEMPTS:
                raise URLError("timed out")
            payload = json.loads(req.data.decode("utf-8"))
            return io.BytesIO(
                json.dumps(
                    {
                        "id": payload["id"],
                        "ok": True,
                        "url": payload["url"],
                        "status": 200,
                    }
                ).encode("utf-8")
            )

        with patch.object(host.urllib.request, "urlopen", side_effect=urlopen):
            result = host.fetch({"pdf_1": "https://example.gov/a.pdf"})

        self.assertTrue(result["pdf_1"]["ok"])
        self.assertEqual(calls["n"], host._GATEWAY_FETCH_ATTEMPTS)
        self.assertFalse(os.path.exists(self.requests_path))


if __name__ == "__main__":
    unittest.main()
