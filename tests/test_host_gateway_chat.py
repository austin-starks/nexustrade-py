from __future__ import annotations

import json
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
        ):
            result = host.gateway_chat(prompt="extract this document")

        self.assertEqual(result["model"], "test")
        self.assertEqual(calls, 3)
        self.assertEqual(sleep.call_count, 2)

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
