"""PR 4 compatibility and safety contracts for nt.lake.

Each test names a defect that cost something real: a duplicate paid query, a
client giving up while the server still worked, a corrupt resume accepted, or an
empty result that could not be read at all.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nexustrade import lake
from nexustrade.client import BinaryTransport, NexusTradeApiError, Transport


class JsonOnlyTransport:
    """Implements Transport but not the binary capability."""

    def request(self, method, path, *, body=None, idempotency_key=None):
        return {"operation": {"id": "lq_1", "kind": "lake_query", "status": "queued"}}


class ExplodingTransport(JsonOnlyTransport):
    def request(self, method, path, *, body=None, idempotency_key=None):
        raise NexusTradeApiError(0, "transport_error", "connection reset")


class SubmitFailureTaxonomyTests(unittest.TestCase):
    """The retry hint must not be reachable by a broad LakeQueryFailed handler,
    and must not be attached to failures that were never accepted."""

    def _client_raising(self, status: int, code: str):
        class Raising(JsonOnlyTransport):
            def request(self, method, path, *, body=None, idempotency_key=None):
                raise NexusTradeApiError(status, code, "boom")

        return lake.NexusTradeClient(transport=Raising())

    def test_submit_failure_is_not_a_lake_query_failed(self) -> None:
        # A `except LakeQueryFailed:` retry that re-calls sql() without a key
        # would mint a new one and double-bill — the exact thing this prevents.
        self.assertFalse(issubclass(lake.LakeSubmitFailed, lake.LakeQueryFailed))
        self.assertTrue(issubclass(lake.LakeSubmitFailed, NexusTradeApiError))

        client = self._client_raising(0, "transport_error")
        with self.assertRaises(NexusTradeApiError):
            lake.submit("SELECT 1", client=client)
        try:
            lake.submit("SELECT 1", client=client)
        except lake.LakeQueryFailed:  # pragma: no cover - must not happen
            self.fail("submit ambiguity leaked into a LakeQueryFailed handler")
        except lake.LakeSubmitFailed:
            pass

    def test_5xx_is_ambiguous_and_carries_the_key(self) -> None:
        client = self._client_raising(503, "service_unavailable")
        with self.assertRaises(lake.LakeSubmitFailed) as raised:
            lake.submit("SELECT 1", client=client)
        self.assertEqual(raised.exception.status, 503)
        self.assertTrue(raised.exception.idempotency_key)

    def test_4xx_stays_a_plain_api_error_with_its_status(self) -> None:
        # Rejected outright: never accepted, so "retry with this key" is wrong,
        # and callers need the status to tell auth from validation.
        for status, code in [(400, "invalid_sql"), (401, "invalid_token"), (403, "insufficient_scope")]:
            client = self._client_raising(status, code)
            with self.assertRaises(NexusTradeApiError) as raised:
                lake.submit("SELECT 1", client=client)
            self.assertNotIsInstance(raised.exception, lake.LakeSubmitFailed)
            self.assertEqual(raised.exception.status, status)


class SqlTypeSanitizationTests(unittest.TestCase):
    def test_accepts_real_types_including_parameterized_and_arrays(self) -> None:
        for raw, expected in [
            ("VARCHAR", "VARCHAR"),
            ("bigint", "BIGINT"),
            ("DECIMAL(18,4)", "DECIMAL(18,4)"),
            ("VARCHAR(64)", "VARCHAR(64)"),
            ("INTEGER[]", "INTEGER[]"),
            (None, "VARCHAR"),
        ]:
            self.assertEqual(lake._safe_sql_type(raw), expected)

    def test_refuses_to_interpolate_anything_unvetted(self) -> None:
        # `type` arrives over the wire and lands in SQL; names were escaped and
        # types were not.
        for hostile in [
            "VARCHAR); DROP TABLE x; --",
            "INTEGER UNION SELECT 1",
            "(SELECT 1)",
            "DECIMAL(1,2) , evil",
        ]:
            self.assertEqual(lake._safe_sql_type(hostile), "VARCHAR")


class IdempotencyTests(unittest.TestCase):
    def test_submit_failure_hands_back_the_key_it_used(self) -> None:
        # The outcome is unknown after a transport error: the query may be
        # queued and billing. Retrying with a NEW key would launch a second
        # paid query, so the key must come back with the error.
        client = lake.NexusTradeClient(transport=ExplodingTransport())
        with self.assertRaises(lake.LakeSubmitFailed) as raised:
            lake.submit("SELECT 1", client=client)
        self.assertTrue(raised.exception.idempotency_key)
        self.assertIn("Retry with idempotency_key=", str(raised.exception))
        self.assertIn("LakeSubmitFailed", lake.__all__)

    def test_handle_exposes_the_key_for_reuse(self) -> None:
        client = lake.NexusTradeClient(transport=JsonOnlyTransport())
        handle = lake.submit("SELECT 1", client=client)
        self.assertTrue(handle.idempotency_key)

    def test_explicit_key_is_honoured(self) -> None:
        seen: dict[str, object] = {}

        class Recording(JsonOnlyTransport):
            def request(self, method, path, *, body=None, idempotency_key=None):
                seen["key"] = idempotency_key
                return super().request(method, path, body=body)

        client = lake.NexusTradeClient(transport=Recording())
        lake.submit("SELECT 1", idempotency_key="mine-v1", client=client)
        self.assertEqual(seen["key"], "mine-v1")


class TimeoutSeparationTests(unittest.TestCase):
    def test_wait_budget_exceeds_the_server_execution_budget(self) -> None:
        # One value for both clocks charged queue time against the client's
        # patience but not the server's allowance, so the caller gave up while
        # the query was still legitimately running.
        captured: dict[str, object] = {}

        class Recording(JsonOnlyTransport):
            def request(self, method, path, *, body=None, idempotency_key=None):
                captured["body"] = body
                return {
                    "operation": {
                        "id": "lq_1",
                        "kind": "lake_query",
                        "status": "queued",
                    }
                }

        client = lake.NexusTradeClient(transport=Recording())
        with mock.patch.object(lake.LakeQueryHandle, "wait") as waited:
            lake.sql("SELECT 1", query_timeout_seconds=90, client=client)

        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["limits"]["timeoutSeconds"], 90)
        self.assertEqual(
            waited.call_args.kwargs["timeout_seconds"],
            90 + lake.DEFAULT_QUEUE_ALLOWANCE_SECONDS,
        )

    def test_legacy_timeout_seconds_still_sets_the_server_budget(self) -> None:
        captured: dict[str, object] = {}

        class Recording(JsonOnlyTransport):
            def request(self, method, path, *, body=None, idempotency_key=None):
                captured["body"] = body
                return {
                    "operation": {"id": "x", "kind": "lake_query", "status": "queued"}
                }

        client = lake.NexusTradeClient(transport=Recording())
        with mock.patch.object(lake.LakeQueryHandle, "wait"):
            lake.sql("SELECT 1", timeout_seconds=45, client=client)
        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["limits"]["timeoutSeconds"], 45)


class BinaryTransportTests(unittest.TestCase):
    def test_json_only_transport_is_rejected_by_the_typed_check(self) -> None:
        transport = JsonOnlyTransport()
        self.assertIsInstance(transport, Transport)
        self.assertNotIsInstance(transport, BinaryTransport)

    def test_download_reports_an_unsupported_transport(self) -> None:
        client = lake.NexusTradeClient(transport=JsonOnlyTransport())
        with self.assertRaises(NexusTradeApiError) as raised:
            client.download_lake_query_part("lq_1", 0)
        self.assertEqual(raised.exception.code, "unsupported_transport")


class ManifestValidationTests(unittest.TestCase):
    SHA = "a" * 64

    def _parts(self, parts):
        return lake._validated_parts({"parts": parts})

    def test_accepts_a_contiguous_manifest(self) -> None:
        parsed = self._parts(
            [
                {"part": 1, "sha256": self.SHA, "byteSize": 2},
                {"part": 0, "sha256": self.SHA, "byteSize": 1},
            ]
        )
        self.assertEqual([item[0] for item in parsed], [0, 1])

    def test_empty_parts_is_a_valid_empty_result(self) -> None:
        self.assertEqual(self._parts([]), [])

    def test_rejects_duplicates_and_gaps(self) -> None:
        for parts in (
            [
                {"part": 0, "sha256": self.SHA, "byteSize": 1},
                {"part": 0, "sha256": self.SHA, "byteSize": 1},
            ],
            [
                {"part": 0, "sha256": self.SHA, "byteSize": 1},
                {"part": 2, "sha256": self.SHA, "byteSize": 1},
            ],
        ):
            with self.assertRaises(lake.LakeQueryFailed):
                self._parts(parts)

    def test_rejects_malformed_entries_with_a_typed_error(self) -> None:
        # Previously indexed straight in, so these raised bare KeyError.
        for parts in (
            [{"sha256": self.SHA, "byteSize": 1}],
            [{"part": 0, "byteSize": 1}],
            [{"part": 0, "sha256": "short", "byteSize": 1}],
            [{"part": -1, "sha256": self.SHA, "byteSize": 1}],
            ["not-an-object"],
        ):
            with self.assertRaises(lake.LakeQueryFailed):
                self._parts(parts)


class AtomicManifestTests(unittest.TestCase):
    def test_manifest_write_replaces_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "download_manifest.json"
            lake._write_json_atomic(target, {"a": 1})
            self.assertEqual(json.loads(target.read_text()), {"a": 1})
            # No temp file left behind.
            self.assertEqual(
                sorted(p.name for p in Path(directory).iterdir()),
                ["download_manifest.json"],
            )


class EmptyResultTests(unittest.TestCase):
    def _result(self, schema):
        return lake.LakeQueryResult(
            id="lq_1",
            status="completed",
            result={"parts": [], "schema": schema, "rowCount": 0, "byteSize": 0},
            _client=lake.NexusTradeClient(transport=JsonOnlyTransport()),
        )

    def test_empty_relation_carries_the_manifest_schema(self) -> None:
        # Globbing part-*.parquet over an empty directory fails, so a query
        # matching no rows was unreadable. The schema is still in the manifest.
        sql = self._result(
            [{"name": "ticker", "type": "VARCHAR"}, {"name": "n", "type": "BIGINT"}]
        )._empty_relation_sql()
        self.assertIn('CAST(NULL AS VARCHAR) AS "ticker"', sql)
        self.assertIn('CAST(NULL AS BIGINT) AS "n"', sql)
        self.assertIn("WHERE FALSE", sql)

    def test_missing_schema_degrades_rather_than_raising(self) -> None:
        self.assertEqual(self._result([])._empty_relation_sql(), "SELECT 1 WHERE FALSE")


if __name__ == "__main__":
    unittest.main()
