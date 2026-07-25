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

    def test_to_pandas_max_bytes_defaults_but_still_bounds(self) -> None:
        # The obvious call must work; the guard must still be in force.
        default = inspect.signature(
            nt.lake.LakeQueryResult.to_pandas
        ).parameters["max_bytes"].default
        self.assertEqual(default, nt.lake.DEFAULT_TO_PANDAS_MAX_BYTES)
        self.assertIsInstance(default, int)
        self.assertGreater(default, 0)

        oversized = nt.lake.LakeQueryResult(
            id="lq_test",
            status="completed",
            result={
                "rowCount": 1,
                "byteSize": nt.lake.DEFAULT_TO_PANDAS_MAX_BYTES + 1,
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

    def test_client_exposes_http_lake_surface_not_analysis_helpers(self) -> None:
        for name in (
            "create_lake_query",
            "get_lake_query",
            "cancel_lake_query",
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


if __name__ == "__main__":
    unittest.main()
