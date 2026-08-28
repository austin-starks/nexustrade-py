from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import nexustrade as nt
from nexustrade import host


class SecSdkTests(unittest.TestCase):
    def setUp(self) -> None:
        host._pending_requests.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.requests_path = os.path.join(self.tmp.name, "host_requests.jsonl")
        self.results_path = os.path.join(self.tmp.name, "host_results.jsonl")
        self.patches = [
            patch.object(host, "HOST_REQUESTS_PATH", self.requests_path),
            patch.object(host, "HOST_RESULTS_PATH", self.results_path),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self) -> None:
        host._pending_requests.clear()

    def test_statement_queues_a_deterministic_point_in_time_request(self) -> None:
        self.assertEqual(
            nt.sec.statement(
                ticker="googl",
                periods=10,
                cadence="annual",
                as_of="2026-08-28",
                _exit=False,
            ),
            {},
        )
        with open(self.requests_path, encoding="utf-8") as handle:
            first = json.loads(handle.read())
        self.assertEqual(
            {key: first[key] for key in first if key != "id"},
            {
                "kind": "sec",
                "action": "statement",
                "ticker": "GOOGL",
                "periods": 10,
                "cadence": "annual",
                "asOf": "2026-08-28",
            },
        )

        os.remove(self.requests_path)
        nt.sec.statement(
            ticker="GOOGL",
            periods=10,
            cadence="annual",
            as_of="2026-08-28",
            _exit=False,
        )
        with open(self.requests_path, encoding="utf-8") as handle:
            second = json.loads(handle.read())
        self.assertEqual(first["id"], second["id"])

    def test_fact_candidates_replays_the_host_payload(self) -> None:
        nt.sec.fact_candidates(
            ticker="GOOGL",
            roles=["operating_cash_flow", "capital_expenditures"],
            _exit=False,
        )
        with open(self.requests_path, encoding="utf-8") as handle:
            request = json.loads(handle.read())
        payload = {
            "ticker": "GOOGL",
            "candidates": [
                {"concept": "NetCashProvidedByUsedInOperatingActivities"}
            ],
            "reconciliation": [{"status": "direct"}],
        }
        with open(self.results_path, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"id": request["id"], "ok": True, "data": payload})
            )
            handle.write("\n")

        self.assertEqual(
            nt.sec.fact_candidates(
                ticker="GOOGL",
                roles=["operating_cash_flow", "capital_expenditures"],
            ),
            payload,
        )

    def test_validation_fails_before_queueing(self) -> None:
        with self.assertRaisesRegex(ValueError, "real date"):
            nt.sec.statement(ticker="GOOGL", as_of="2026-02-30", _exit=False)
        with self.assertRaisesRegex(ValueError, "unsupported SEC fact role"):
            nt.sec.fact_candidates(
                ticker="GOOGL",
                roles=["magic_number"],  # type: ignore[list-item]
                _exit=False,
            )
        self.assertFalse(os.path.exists(self.requests_path))


if __name__ == "__main__":
    unittest.main()
