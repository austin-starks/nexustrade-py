from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
from urllib.request import Request

import nexustrade as nt
from nexustrade import host, report


class SecSdkTests(unittest.TestCase):
    def test_latest_statement_uses_current_period_and_available_amendment(self) -> None:
        annual = dict(cik=999997, ticker="SYNTHETIC", period_end="2023-12-31",
                      available_at="2024-02-01T12:00:00Z", cash=80, accession="annual")
        interim = dict(annual, period_end="2024-03-31", available_at="2024-05-01T12:00:00Z",
                       cash=100, accession="interim", shares_outstanding_class="ordinary",
                       shares_outstanding_as_of="2024-03-31")
        amended = dict(interim, available_at="2024-06-01T12:00:00Z", cash=105, accession="amended")
        # A later-published amendment of an older period is not a newer snapshot.
        old_amended = dict(annual, available_at="2024-06-10T12:00:00Z", cash=85)
        payloads = [{"rows": [old_amended, annual]}, {"rows": [amended, interim]}]
        selected = nt.sec.latest_statement(*payloads, as_of="2024-05-15", required_fields=["cash"])
        self.assertEqual(selected["cash"], 100)
        self.assertEqual(selected["shares_outstanding_as_of"], "2024-03-31")
        self.assertEqual(nt.sec.latest_statement(*payloads, as_of="2024-06-15")["cash"], 105)
        selected["cash"] = 999
        self.assertEqual(interim["cash"], 100)
        self.assertEqual(nt.sec.latest_statement(*payloads, as_of="2024-04-01")["cash"], 80)

    def test_latest_statement_rejects_missing_current_values_conflicts_and_mixed_issuers(self) -> None:
        row = dict(cik=999997, ticker="SYNTHETIC", period_end="2024-03-31",
                   available_at="2024-05-01T12:00:00Z", cash=None)
        older = dict(row, period_end="2023-12-31", cash=10)
        with self.assertRaisesRegex(ValueError, "missing required field"):
            nt.sec.latest_statement({"rows": [row, older]}, as_of="2024-06-01", required_fields=["cash"])
        with self.assertRaisesRegex(ValueError, "conflicting"):
            nt.sec.latest_statement({"rows": [row, dict(row, cash=12)]}, as_of="2024-06-01")
        with self.assertRaisesRegex(ValueError, "different issuer"):
            nt.sec.latest_statement({"rows": [row, dict(row, cik=999998)]}, as_of="2024-06-01")
        with self.assertRaisesRegex(ValueError, "no supplied statement"):
            nt.sec.latest_statement({"rows": [row]}, as_of="2024-04-01")
        with self.assertRaisesRegex(ValueError, "timezone"):
            nt.sec.latest_statement({"rows": [dict(row, available_at="2024-05-01T12:00:00")]}, as_of="2024-06-01")
        self.assertEqual(nt.sec.latest_statement({"rows": [row, deepcopy(row)]}, as_of="2024-06-01"), row)

    def test_latest_statement_resolves_annual_and_derived_q4_views_of_the_same_filing(self) -> None:
        annual = dict(cik=999997, ticker="SYNTHETIC", accession="same-filing", cadence="annual",
                      period_end="2023-12-31", available_at="2024-02-01T12:00:00Z", cash=80,
                      period_start="2023-01-01", fiscal_period="FY", total_revenue=400)
        q4 = dict(annual, cadence="quarterly", period_start="2023-10-01", fiscal_period="Q4", total_revenue=110)
        self.assertEqual(nt.sec.latest_statement({"rows": [q4]}, {"rows": [annual]}, as_of="2024-03-01"), annual)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            nt.sec.latest_statement({"rows": [annual, dict(q4, cash=90)]}, as_of="2024-03-01")
        with self.assertRaisesRegex(ValueError, "conflicting"):
            nt.sec.latest_statement({"rows": [annual, dict(annual, total_revenue=999), q4]}, as_of="2024-03-01")

    def test_resolved_facts_preserve_provenance_and_refuse_incomplete_values(self) -> None:
        identity = dict(role='depreciation_and_amortization', period_end='2024-06-30', accession='synthetic-A')
        row = {**identity, 'status': 'components', 'confidence': 'derived_from_complete_components',
               'value': 12, 'selected_candidate_ids': ['dep', 'amort'], 'note': 'Complete split facts'}
        payload = {'reconciliation': [row], 'candidates': [
            {**identity, 'id': 'dep', 'value': 9, 'source_filing_url': 'https://example.test/a'},
            {**identity, 'id': 'amort', 'value': 3, 'source_filing_url': 'https://example.test/a'},
        ]}
        result = nt.sec.resolved_fact(payload, role=identity['role'], period_end=identity['period_end'])
        self.assertEqual(result['value'], 12)
        self.assertEqual(result['confidence'], row['confidence'])
        report_path = os.path.join(self.tmp.name, 'report-inputs.json')
        report.write_inputs({'statistics': {'da': report.ref('da', 'value', provenance_path=('da',))}},
                            model={'da': result}, preserve_references=True, path=report_path)
        with open(report_path, encoding='utf-8') as written:
            handoff = json.load(written)
        self.assertEqual(handoff['statistics']['da'], 12)
        lineage = handoff['modelReferences'][0]['provenance']
        self.assertEqual(lineage['status'], 'components')
        self.assertEqual(lineage['selected_candidate_ids'], ['dep', 'amort'])
        self.assertEqual(lineage['candidates'][0]['source_filing_url'], 'https://example.test/a')
        result['candidates'][0]['value'] = 99
        self.assertEqual(payload['candidates'][0]['value'], 9)
        for status, confidence in [('partial_components', 'incomplete'), ('unavailable', 'incomplete'),
                                   ('ambiguous', 'ambiguous'), ('components', 'component_facts_no_total'),
                                   ('cumulative_ytd', 'direct_filing_fact')]:
            bad = deepcopy(payload)
            bad['reconciliation'][0].update(status=status, confidence=confidence)
            with self.assertRaisesRegex(ValueError, 'unresolved'):
                nt.sec.resolved_fact(bad, role=identity['role'], period_end=identity['period_end'])
        bad = deepcopy(payload)
        bad['candidates'][0]['accession'] = 'other-filing'
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            nt.sec.resolved_fact(bad, role=identity['role'], period_end=identity['period_end'])
        bad = deepcopy(payload)
        bad['reconciliation'].append(deepcopy(row))
        with self.assertRaisesRegex(ValueError, 'expected one'):
            nt.sec.resolved_fact(bad, role=identity['role'], period_end=identity['period_end'])

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
                "stock_based_compensation",
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
                    "stock_based_compensation",
                    "diluted_shares",
                    "operating_cash_flow",
                    "capital_expenditures",
                ],
            ),
            payload,
        )

    def test_notes_queries_send_exact_bounded_contracts(self) -> None:
        requests: list[dict[str, object]] = []

        def urlopen(req: Request, timeout: int = 0) -> io.BytesIO:
            del timeout
            payload = json.loads(req.data.decode("utf-8"))
            requests.append(payload)
            return io.BytesIO(
                json.dumps(
                    {
                        "id": payload["id"],
                        "ok": True,
                        "data": {"action": payload["action"], "status": "fixture"},
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
            nt.sec.dimensioned_concepts(
                ticker="googl",
                as_of="2026-08-28",
                forms=["10-K", "10-Q"],
                max_filings=12,
                limit=25,
            )
            nt.sec.business_breakdowns(
                ticker="GOOGL",
                as_of="2026-08-28",
                concepts=["RevenueFromContractWithCustomerExcludingAssessedTax"],
                period_end_from="2024-01-01",
                period_end_to="2025-12-31",
            )
            nt.sec.fact_instances(
                ticker="GOOGL",
                as_of="2026-08-28",
                concepts=["Assets"],
                dimensional="none",
            )

        self.assertEqual(
            [{key: value for key, value in request.items() if key != "id"} for request in requests],
            [
                {
                    "action": "dimensioned_concepts",
                    "ticker": "GOOGL",
                    "asOf": "2026-08-28",
                    "forms": ["10-K", "10-Q"],
                    "maxFilings": 12,
                    "limit": 25,
                },
                {
                    "action": "business_breakdowns",
                    "ticker": "GOOGL",
                    "asOf": "2026-08-28",
                    "periodEndFrom": "2024-01-01",
                    "periodEndTo": "2025-12-31",
                    "concepts": ["RevenueFromContractWithCustomerExcludingAssessedTax"],
                },
                {
                    "action": "fact_instances",
                    "ticker": "GOOGL",
                    "asOf": "2026-08-28",
                    "concepts": ["Assets"],
                    "dimensional": "none",
                },
            ],
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
        with self.assertRaisesRegex(ValueError, "as_of is required"):
            nt.sec.dimensioned_concepts(ticker="GOOGL", as_of=None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "concepts must contain"):
            nt.sec.business_breakdowns(ticker="GOOGL", as_of="2026-08-28", concepts=[])
        self.assertFalse(os.path.exists(self.requests_path))


if __name__ == "__main__":
    unittest.main()
