from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from io import BytesIO
from unittest.mock import patch

from nexustrade import host


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://gateway.example/chat/completions",
        status,
        "gateway error",
        {},
        BytesIO(f"error code: {status}".encode("utf-8")),
    )


class GatewayChatRetryTest(unittest.TestCase):
    def test_touches_runner_activity_file_without_growing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            activity_path = os.path.join(directory, "host-activity")
            with patch.dict(
                host.os.environ,
                {"NEXUSTRADE_HOST_ACTIVITY_FILE": activity_path},
            ):
                host._touch_host_activity()
                first_mtime = os.stat(activity_path).st_mtime_ns
                host._touch_host_activity()

            self.assertEqual(os.path.getsize(activity_path), 0)
            self.assertGreaterEqual(os.stat(activity_path).st_mtime_ns, first_mtime)

    def test_accepts_positional_prompt_text(self) -> None:
        captured_body: dict[str, object] = {}

        def fake_urlopen(request: object, **__: object) -> _Response:
            nonlocal captured_body
            data = getattr(request, "data", None)
            self.assertIsInstance(data, bytes)
            captured_body = json.loads(data.decode("utf-8"))
            return _Response(
                {
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "model": "test",
                }
            )

        with (
            patch.dict(
                host.os.environ,
                {
                    "OPENAI_BASE_URL": "https://gateway.example",
                    "OPENAI_API_KEY": "test-key",
                },
            ),
            patch.object(host.urllib.request, "urlopen", fake_urlopen),
        ):
            result = host.gateway_chat_json(
                "adjudicate this transaction",
                system="Use source evidence only.",
                json_schema={"type": "object"},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            captured_body["messages"],
            [
                {"role": "system", "content": "Use source evidence only."},
                {"role": "user", "content": "adjudicate this transaction"},
            ],
        )

    def test_accepts_positional_mapping_as_json_prompt(self) -> None:
        captured_body: dict[str, object] = {}

        def fake_urlopen(request: object, **__: object) -> _Response:
            nonlocal captured_body
            data = getattr(request, "data", None)
            self.assertIsInstance(data, bytes)
            captured_body = json.loads(data.decode("utf-8"))
            return _Response(
                {
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "model": "test",
                }
            )

        payload = {"task": "adjudicate", "records": [{"input_index": 0}]}
        with (
            patch.dict(
                host.os.environ,
                {
                    "OPENAI_BASE_URL": "https://gateway.example",
                    "OPENAI_API_KEY": "test-key",
                },
            ),
            patch.object(host.urllib.request, "urlopen", fake_urlopen),
        ):
            result = host.gateway_chat_json(payload, system="Use source evidence only.")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            captured_body["messages"],
            [
                {"role": "system", "content": "Use source evidence only."},
                {
                    "role": "user",
                    "content": '{"task":"adjudicate","records":[{"input_index":0}]}',
                },
            ],
        )

    def test_rejects_positional_and_keyword_prompt_together(self) -> None:
        with self.assertRaisesRegex(ValueError, "not both"):
            host.gateway_chat("one", prompt="two")

    def test_retries_cloudflare_524_then_succeeds(self) -> None:
        calls = 0

        def fake_urlopen(*_: object, **__: object) -> _Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise _http_error(524)
            return _Response(
                {"choices": [{"message": {"content": "ok"}}], "model": "test"}
            )

        with (
            patch.dict(
                host.os.environ,
                {
                    "OPENAI_BASE_URL": "https://gateway.example",
                    "OPENAI_API_KEY": "test-key",
                },
            ),
            patch.object(host.urllib.request, "urlopen", fake_urlopen),
            patch.object(host.time, "sleep") as sleep,
            patch.object(host.random, "uniform", return_value=0.5),
            patch.object(host, "_touch_host_activity") as touch_activity,
        ):
            result = host.gateway_chat(prompt="extract this document")

        self.assertEqual(result["model"], "test")
        self.assertEqual(calls, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(touch_activity.call_count, 6)

    def test_reports_524_as_transport_after_bounded_retries(self) -> None:
        calls = 0

        def fake_urlopen(*_: object, **__: object) -> _Response:
            nonlocal calls
            calls += 1
            raise _http_error(524)

        with (
            patch.dict(
                host.os.environ,
                {
                    "OPENAI_BASE_URL": "https://gateway.example",
                    "OPENAI_API_KEY": "test-key",
                },
            ),
            patch.object(host.urllib.request, "urlopen", fake_urlopen),
            patch.object(host.time, "sleep") as sleep,
            patch.object(host.random, "uniform", return_value=0.5),
            self.assertRaisesRegex(host.GatewayChatTransportError, "HTTP 524"),
        ):
            host.gateway_chat(prompt="extract this document")

        self.assertEqual(calls, host._GATEWAY_CHAT_MAX_ATTEMPTS)
        self.assertEqual(
            sleep.call_count,
            host._GATEWAY_CHAT_MAX_ATTEMPTS - 1,
        )

    def test_caller_can_own_transport_recovery_without_nested_retries(self) -> None:
        calls = 0

        def fake_urlopen(*_: object, **__: object) -> _Response:
            nonlocal calls
            calls += 1
            raise _http_error(524)

        with (
            patch.dict(
                host.os.environ,
                {
                    "OPENAI_BASE_URL": "https://gateway.example",
                    "OPENAI_API_KEY": "test-key",
                },
            ),
            patch.object(host.urllib.request, "urlopen", fake_urlopen),
            patch.object(host.time, "sleep") as sleep,
            self.assertRaisesRegex(host.GatewayChatTransportError, "HTTP 524"),
        ):
            host.gateway_chat(
                prompt="extract this corpus",
                max_transport_attempts=1,
            )

        self.assertEqual(calls, 1)
        sleep.assert_not_called()

    def test_forwards_caller_idempotency_key(self) -> None:
        captured: object | None = None

        def fake_urlopen(request: object, **__: object) -> _Response:
            nonlocal captured
            captured = request
            return _Response(
                {"choices": [{"message": {"content": "ok"}}], "model": "test"}
            )

        with (
            patch.dict(
                host.os.environ,
                {
                    "OPENAI_BASE_URL": "https://gateway.example",
                    "OPENAI_API_KEY": "test-key",
                },
            ),
            patch.object(host.urllib.request, "urlopen", fake_urlopen),
        ):
            host.gateway_chat(
                prompt="extract this corpus",
                idempotency_key="exact-request-key",
            )

        self.assertIsNotNone(captured)
        self.assertEqual(
            getattr(captured, "get_header")("Idempotency-key"),
            "exact-request-key",
        )

    def test_does_not_retry_permanent_403(self) -> None:
        calls = 0

        def fake_urlopen(*_: object, **__: object) -> _Response:
            nonlocal calls
            calls += 1
            raise _http_error(403)

        with (
            patch.dict(
                host.os.environ,
                {
                    "OPENAI_BASE_URL": "https://gateway.example",
                    "OPENAI_API_KEY": "test-key",
                },
            ),
            patch.object(host.urllib.request, "urlopen", fake_urlopen),
            self.assertRaisesRegex(host.GatewayChatRequestError, "HTTP 403"),
        ):
            host.gateway_chat(prompt="invalid request")

        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
