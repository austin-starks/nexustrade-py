from __future__ import annotations

import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


class TigrisSdkBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.duckdb = mock.Mock()
        with mock.patch.dict(sys.modules, {"duckdb": cls.duckdb}):
            cls.tigris = importlib.import_module("nexustrade.tigris")

    def test_sdk_inspection_does_not_claim_a_guessed_physical_key(self) -> None:
        result = mock.Mock()
        result.source_id = "lake-query:lq_inspect"
        result.to_pandas.return_value = []
        client = mock.Mock()
        client.describe_lake_table.return_value = {"columns": [{"name": "date"}]}

        with (
            mock.patch.object(self.tigris, "_sdk_lake_enabled", return_value=True),
            mock.patch.object(self.tigris, "_sdk_lake_client", return_value=client),
            mock.patch.object(
                self.tigris,
                "year_shard_url",
                side_effect=AssertionError("must not guess a private manifest key"),
            ),
            mock.patch(
                "nexustrade.lake.sql",
                return_value=result,
            ),
        ):
            inspected = self.tigris.inspect_table(
                "sec_daily_ohlc",
                year=2024,
                sample_rows=1,
            )

        self.assertIsNone(inspected["key"])
        self.assertEqual(inspected["source_id"], "lake-query:lq_inspect")
        self.assertTrue(inspected["readable"])

    def test_read_ohlc_uses_eager_result_materialization(self) -> None:
        frame = SimpleNamespace(attrs={})
        result = mock.Mock()
        result.to_pandas.return_value = frame

        with (
            mock.patch.object(self.tigris, "_sdk_lake_enabled", return_value=True),
            mock.patch("nexustrade.lake.sql", return_value=result),
            warnings_suppressed(),
        ):
            actual = self.tigris.read_ohlc("SPY", start="2024-01-01")

        self.assertIs(actual, frame)
        result.to_pandas.assert_called_once_with()
        self.assertFalse(result.duckdb_relation.called)


def warnings_suppressed():
    return mock.patch("warnings.warn")


if __name__ == "__main__":
    unittest.main()
