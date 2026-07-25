"""Shared cross-language conformance suite.

Drives every client method through a recording transport and pins the exact wire
traffic. The TypeScript SDK runs the same cases from a byte-identical copy of
`conformance/client-cases.json`, so the two clients cannot disagree about what
they put on the wire.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from nexustrade.client import NexusTradeApiError, NexusTradeClient

CASES_PATH = Path(__file__).resolve().parent / "conformance" / "client-cases.json"
NO_BODY_METHODS = {"get_backtest", "get_optimization", "get_walk_forward"}
# Pollers take wait options, not an idempotency key. Zero interval so the
# fixture pins the REQUEST SEQUENCE without spending its cadence in wall-clock
# time.
WAIT_METHODS = {
    "wait_for_backtest",
    "wait_for_optimization",
    "wait_for_walk_forward",
}


class RecordingTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body if body is None else json.loads(json.dumps(body)),
                "idempotency_key": idempotency_key,
            }
        )
        if not self.responses:
            raise AssertionError(f"no scripted response for {method} {path}")
        return self.responses.pop(0)


def _invoke(client: NexusTradeClient, case: dict[str, Any]) -> Any:
    method = getattr(client, case["method"])
    if case["method"] in WAIT_METHODS:
        return method(case["input"], poll_interval_seconds=0)
    if case["method"] in NO_BODY_METHODS:
        return method(case["input"])
    return method(case["input"], idempotency_key=case["idempotency_key"])


class ClientConformanceTests(unittest.TestCase):
    maxDiff = None

    def test_cases(self) -> None:
        fixture = json.loads(CASES_PATH.read_text())
        self.assertGreater(len(fixture["cases"]), 0)

        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                transport = RecordingTransport(case["responses"])
                client = NexusTradeClient(transport=transport)
                expected_error = case.get("expected_error")

                if expected_error is None:
                    result = _invoke(client, case)
                    self.assertEqual(result, case["expected_result"])
                elif expected_error["kind"] == "api_error":
                    with self.assertRaises(NexusTradeApiError) as raised:
                        _invoke(client, case)
                    self.assertEqual(raised.exception.code, expected_error["code"])
                else:
                    with self.assertRaises(ValueError) as raised_value:
                        _invoke(client, case)
                    self.assertIn(
                        expected_error["message_contains"],
                        str(raised_value.exception),
                    )

                self.assertEqual(transport.calls, case["expected_calls"])


if __name__ == "__main__":
    unittest.main()
