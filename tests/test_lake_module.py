"""Unit tests for nexustrade.lake packaging + analysis helpers (no network)."""

from __future__ import annotations

import inspect
import os
import unittest
from pathlib import Path
from unittest import mock

import nexustrade as nt
from nexustrade.client import wait_for_operation


class LakePackagingTests(unittest.TestCase):
    def test_lake_is_lazy_not_in_all(self) -> None:
        self.assertNotIn("lake", nt.__all__)
        self.assertEqual(nt._LAZY_EXPORTS["lake"], ("nexustrade.lake", None))
        self.assertEqual(set(nt._LAZY_EXPORTS).intersection(nt.__all__), set())

    def test_lake_module_resolves_by_attribute(self) -> None:
        self.assertTrue(inspect.ismodule(nt.lake))
        self.assertTrue(callable(nt.lake.sql))
        self.assertTrue(callable(nt.lake.submit))
        self.assertTrue(callable(nt.lake.get))
        self.assertTrue(callable(nt.lake.catalog))
        self.assertTrue(callable(nt.lake.describe))

    def test_wait_for_operation_is_exported(self) -> None:
        self.assertIs(nt.wait_for_operation, wait_for_operation)
        self.assertIn("wait_for_operation", nt.__all__)

    def test_download_default_is_cwd_relative(self) -> None:
        params = inspect.signature(nt.lake.LakeQueryResult.download).parameters
        self.assertIsNone(params["directory"].default)

    def test_download_root_honours_cache_dir_env(self) -> None:
        # Cwd-relative for a local script; redirected inside the sandbox, whose
        # cwd is /work and whose /work is tarred whole into the pause checkpoint.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(nt.lake.LAKE_CACHE_DIR_ENV, None)
            self.assertEqual(
                nt.lake._default_download_root(),
                Path(nt.lake.DEFAULT_LAKE_CACHE_DIR),
            )
        with mock.patch.dict(
            os.environ, {nt.lake.LAKE_CACHE_DIR_ENV: "/tmp/nexustrade-lake"}
        ):
            self.assertEqual(
                nt.lake._default_download_root(), Path("/tmp/nexustrade-lake")
            )
        # An empty/whitespace value must not silently become the cwd.
        with mock.patch.dict(os.environ, {nt.lake.LAKE_CACHE_DIR_ENV: "   "}):
            self.assertEqual(
                nt.lake._default_download_root(),
                Path(nt.lake.DEFAULT_LAKE_CACHE_DIR),
            )

    def test_to_pandas_requires_positive_max_bytes(self) -> None:
        result = nt.lake.LakeQueryResult(
            id="lq_test",
            status="completed",
            result={
                "rowCount": 1,
                "byteSize": 10,
                "parts": [],
                "schema": [],
                "format": "parquet",
                "engine": "motherduck",
                "fallbackUsed": False,
                "catalogVersion": "x",
                "expiresAt": "2026-07-25T00:00:00.000Z",
            },
            _client=object(),  # type: ignore[arg-type]
        )
        with self.assertRaises(ValueError):
            result.to_pandas(max_bytes=0)

    def test_to_pandas_bound_scales_with_container_memory(self) -> None:
        # A constant ceiling was wrong in both directions: too generous on a
        # 1 GiB tier, and it rejected reasonable multi-GB frames on a 16 GiB one.
        with mock.patch.object(nt.lake, "_container_memory_bytes", lambda: None):
            self.assertEqual(
                nt.lake.default_to_pandas_max_bytes(),
                nt.lake._TO_PANDAS_FALLBACK_MAX_BYTES,
            )
        with mock.patch.object(
            nt.lake, "_container_memory_bytes", lambda: 1024 * 1024 * 1024
        ):
            small = nt.lake.default_to_pandas_max_bytes()
        with mock.patch.object(
            nt.lake, "_container_memory_bytes", lambda: 16 * 1024 * 1024 * 1024
        ):
            large = nt.lake.default_to_pandas_max_bytes()
        # Headroom for pandas' 2-3x construction spike on the small tier...
        self.assertLess(small, 1024 * 1024 * 1024 // 2)
        # ...and enough room for a multi-GB options frame on the large one.
        self.assertGreater(large, 4 * 1024 * 1024 * 1024)

    def test_to_pandas_resolves_the_default_per_call(self) -> None:
        # Bound at import time, the same wheel would carry one tier's ceiling
        # onto every other tier.
        self.assertIsNone(
            inspect.signature(nt.lake.LakeQueryResult.to_pandas)
            .parameters["max_bytes"]
            .default
        )

    def test_to_pandas_still_bounds_with_no_explicit_limit(self) -> None:
        # Pinned to a known container size so the assertion does not depend on
        # whatever machine happens to run the suite.
        bound = 1024 * 1024 * 1024 // 3
        oversized = nt.lake.LakeQueryResult(
            id="lq_test",
            status="completed",
            result={
                "rowCount": 1,
                "byteSize": bound * 4,
                "parts": [],
                "schema": [],
                "format": "parquet",
                "engine": "motherduck",
                "fallbackUsed": False,
                "catalogVersion": "x",
                "expiresAt": "2026-07-25T00:00:00.000Z",
            },
            _client=object(),  # type: ignore[arg-type]
        )
        with mock.patch.object(
            nt.lake, "_container_memory_bytes", lambda: 1024 * 1024 * 1024
        ):
            with self.assertRaises(nt.lake.LakeResultLimitError):
                oversized.to_pandas()

    def test_result_limit_error_on_byte_bound(self) -> None:
        result = nt.lake.LakeQueryResult(
            id="lq_test",
            status="completed",
            result={
                "rowCount": 1,
                "byteSize": 1000,
                "parts": [],
                "schema": [],
                "format": "parquet",
                "engine": "motherduck",
                "fallbackUsed": False,
                "catalogVersion": "x",
                "expiresAt": "2026-07-25T00:00:00.000Z",
            },
            _client=object(),  # type: ignore[arg-type]
        )
        with self.assertRaises(nt.lake.LakeResultLimitError):
            result.to_pandas(max_bytes=10)

    def test_to_pandas_reuses_the_validated_download(self) -> None:
        result = nt.lake.LakeQueryResult(
            id="lq_test",
            status="completed",
            result={
                "rowCount": 1,
                "byteSize": 10,
                "parts": [],
                "schema": [],
                "format": "parquet",
                "engine": "motherduck",
                "fallbackUsed": False,
                "catalogVersion": "x",
                "expiresAt": "2026-07-25T00:00:00.000Z",
            },
            _client=object(),  # type: ignore[arg-type]
        )
        relation = mock.Mock()
        relation.df.return_value = "frame"
        directory = Path("/tmp/lake-query-test")

        with (
            mock.patch.object(result, "download", return_value=directory) as download,
            mock.patch.object(
                result,
                "_duckdb_relation_from_directory",
                return_value=relation,
            ) as build_relation,
            mock.patch.dict(
                "sys.modules",
                {
                    "pyarrow": mock.Mock(),
                    "pyarrow.parquet": mock.Mock(),
                },
            ),
        ):
            self.assertEqual(result.to_pandas(max_bytes=100), "frame")

        download.assert_called_once_with()
        build_relation.assert_called_once_with(directory)

    def test_client_exposes_http_lake_surface_not_analysis_helpers(self) -> None:
        for name in (
            "create_lake_query",
            "get_lake_query",
            "cancel_lake_query",
            "create_lake_ask",
            "get_lake_ask",
            "cancel_lake_ask",
            "wait_for_lake_ask",
            "get_lake_query_manifest",
            "download_lake_query_part",
            "get_lake_catalog",
            "describe_lake_table",
            "wait_for_lake_query",
        ):
            self.assertTrue(callable(getattr(nt.NexusTradeClient, name)))
        for name in ("duckdb_relation", "iter_batches", "to_pandas"):
            self.assertFalse(hasattr(nt.NexusTradeClient, name))
            self.assertTrue(callable(getattr(nt.lake.LakeQueryResult, name)))


class LakeAskTests(unittest.TestCase):
    def test_ask_maps_clarification_to_needs_clarification(self) -> None:
        client = mock.Mock()
        client.create_lake_ask.return_value = {"id": "la-1", "status": "running"}
        client.wait_for_lake_ask.return_value = {
            "id": "la-1",
            "status": "completed",
            "result": {
                "outcome": "CLARIFICATION",
                "clarification": "Which ticker?",
                "question": "avg volume",
            },
        }
        ask = nt.lake.ask("avg volume", client=client)
        self.assertTrue(ask.needs_clarification)
        self.assertIsNone(ask.lake_query_id)

    def test_ask_raises_lake_ask_failed_with_sql(self) -> None:
        client = mock.Mock()
        client.create_lake_ask.return_value = {"id": "la-1", "status": "running"}
        client.wait_for_lake_ask.return_value = {
            "id": "la-1",
            "status": "failed",
            "result": {
                "outcome": "GENERATION_FAILED",
                "sql": "SELECT bad",
            },
            "error": {"message": "generation failed"},
        }
        with self.assertRaises(nt.lake.LakeAskFailed) as ctx:
            nt.lake.ask("bad question", client=client)
        self.assertEqual(ctx.exception.sql, "SELECT bad")

    def test_result_without_lake_query_id_raises(self) -> None:
        ask = nt.lake.LakeAsk(
            id="la-1",
            outcome="CLARIFICATION",
            sql=None,
            clarification="Which ticker?",
        )
        with self.assertRaises(nt.lake.LakeAskFailed):
            ask.result()


if __name__ == "__main__":
    unittest.main()
