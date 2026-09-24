"""nt.lake.sql resubmits a query the server reports as failed-but-retryable."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

import nexustrade as nt


class FakeLakeClient:
    """Plays back one terminal outcome per submission."""

    def __init__(self, outcomes: list[dict[str, Any]]) -> None:
        self.outcomes = outcomes
        self.keys: list[str] = []

    def create_lake_query(self, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        self.keys.append(idempotency_key)
        return {"id": f"lq_{len(self.keys)}", "kind": "lake_query", "status": "queued"}

    def get_lake_query(self, query_id: str) -> dict[str, Any]:
        outcome = self.outcomes[int(query_id.split("_")[1]) - 1]
        return {"id": query_id, "kind": "lake_query", **outcome}


COMPLETED = {"status": "completed", "result": {"rowCount": 1}}
TRANSIENT = {
    "status": "failed",
    "error": {
        "code": "service_unavailable",
        "message": "Lake query engine is temporarily unavailable",
        "retryable": True,
    },
}
BAD_SQL = {
    "status": "failed",
    "error": {"code": "unsupported_sql", "message": "partition_range_required", "retryable": False},
}


class LakeRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(nt.lake.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_transient_failure_is_resubmitted_under_a_fresh_key(self) -> None:
        client = FakeLakeClient([TRANSIENT, COMPLETED])
        result = nt.lake.sql("SELECT 1", client=client, idempotency_key="spy-2015")
        self.assertEqual(result.status, "completed")
        self.assertEqual(client.keys, ["spy-2015", "spy-2015:retry-1"])
        self.sleep.assert_called_once()

    def test_a_request_defect_is_never_retried(self) -> None:
        client = FakeLakeClient([BAD_SQL, COMPLETED])
        with self.assertRaises(nt.lake.LakeQueryFailed) as raised:
            nt.lake.sql("SELECT 1", client=client)
        self.assertEqual(raised.exception.code, "unsupported_sql")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(len(client.keys), 1)
        self.sleep.assert_not_called()

    def test_retries_are_bounded(self) -> None:
        client = FakeLakeClient([TRANSIENT, TRANSIENT, TRANSIENT, COMPLETED])
        with self.assertRaises(nt.lake.LakeQueryFailed) as raised:
            nt.lake.sql("SELECT 1", client=client)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(len(client.keys), nt.lake.RETRYABLE_QUERY_ATTEMPTS)

    def test_a_long_key_stays_within_the_server_limit(self) -> None:
        client = FakeLakeClient([TRANSIENT, COMPLETED])
        nt.lake.sql("SELECT 1", client=client, idempotency_key="k" * 160)
        self.assertLessEqual(len(client.keys[1]), 160)
        self.assertTrue(client.keys[1].endswith(":retry-1"))


if __name__ == "__main__":
    unittest.main()
