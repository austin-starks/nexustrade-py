from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

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

    def test_statement_blocks_on_a_deterministic_point_in_time_request(self) -> None:
        requests: list[dict[str, object]] = []

        def urlopen(req: Request, timeout: int = 0) -> io.BytesIO:
            payload = json.loads(req.data.decode("utf-8"))
            requests.append(payload)
            return io.BytesIO(
                json.dumps(
                    {
                        "id": payload["id"],
                        "ok": True,
                        "data": {"ticker": payload["ticker"], "rows": []},
                    }
                ).encode("utf-8")
            )

        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://gateway.example.test/v1",
                "OPENAI_API_KEY": "sandbox-key",
            },
        ), patch.object(host.urllib.request, "urlopen", side_effect=urlopen):
            first = nt.sec.statement(
                ticker="googl",
                periods=10,
                cadence="annual",
                as_of="2026-08-28",
            )
            second = nt.sec.statement(
                ticker="GOOGL",
                periods=10,
                cadence="annual",
                as_of="2026-08-28",
            )

        self.assertEqual(first, {"ticker": "GOOGL", "rows": []})
        self.assertEqual(second, first)
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            {key: requests[0][key] for key in requests[0] if key != "id"},
            {
                "action": "statement",
                "ticker": "GOOGL",
                "periods": 10,
                "cadence": "annual",
                "asOf": "2026-08-28",
            },
        )
        self.assertFalse(os.path.exists(self.requests_path))

    def test_fact_candidates_replays_the_recorded_gateway_payload(self) -> None:
        request = {
            "action": "fact_candidates",
            "ticker": "GOOGL",
            "periods": 10,
            "cadence": "annual",
            "roles": [
                "pretax_income",
                "income_tax_expense",
                "interest_expense",
                "cash_taxes_paid",
                "cash_interest_paid",
                "research_and_development",
                "diluted_shares",
                "operating_cash_flow",
                "capital_expenditures",
            ],
        }
        request["id"] = nt.sec._stable_request_id(request)
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
                roles=[
                    "pretax_income",
                    "income_tax_expense",
                    "interest_expense",
                    "cash_taxes_paid",
                    "cash_interest_paid",
                    "research_and_development",
                    "diluted_shares",
                    "operating_cash_flow",
                    "capital_expenditures",
                ],
            ),
            payload,
        )

    def test_no_gateway_raises_without_staging_a_broker_request(self) -> None:
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "", "OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "public blocking transport"):
                nt.sec.statement(ticker="MSFT")
        self.assertFalse(os.path.exists(self.requests_path))

    def test_validation_fails_before_queueing(self) -> None:
        with self.assertRaisesRegex(ValueError, "real date"):
            nt.sec.statement(ticker="GOOGL", as_of="2026-02-30")
        with self.assertRaisesRegex(ValueError, "unsupported SEC fact role"):
            nt.sec.fact_candidates(
                ticker="GOOGL",
                roles=["magic_number"],  # type: ignore[list-item]
            )
        self.assertFalse(os.path.exists(self.requests_path))


if __name__ == "__main__":
    unittest.main()
