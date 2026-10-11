import unittest
from unittest.mock import patch
import nexustrade as nt


class SourceInputCoverageTests(unittest.TestCase):
    def test_exact_period_inputs_distinguish_zero_null_absent_and_ambiguous(self):
        payload = {"source_id": "retained", "rows": [
            {"period_end": "2015-12-31", "cash": 0, "liabilities": None},
            {"period_end": "2016-12-31", "cash": 3},
            {"period_end": "2016-12-31", "cash": 4},
        ]}
        selectors = {"zero": {"period_end": "2015-12-31", "field": "cash"},
                     "null": {"period_end": "2015-12-31", "field": "liabilities"},
                     "absent": {"period_end": "2015-12-31", "field": "investments"},
                     "period": {"period_end": "2014-12-31", "field": "cash"},
                     "ambiguous": {"period_end": "2016-12-31", "field": "cash"}}
        results = nt.sec.statement_availability(payload, selectors)
        self.assertEqual(results["zero"]["value"], 0)
        self.assertEqual({key: value["status"] for key, value in results.items()}, {
            "zero": "selected", "null": "null_in_response", "absent": "field_not_in_response",
            "period": "period_not_in_response", "ambiguous": "ambiguous"})
        self.assertTrue(all(result["source_absence_established"] is False for result in results.values()))
        results["zero"]["row"]["cash"] = 99
        self.assertEqual(payload["rows"][0]["cash"], 0)

    def test_filing_sends_exact_accession_form_cutoff_without_guessing(self):
        with patch.object(nt.sec, "_run_payload", return_value={"status": "located"}) as run:
            nt.sec.filing(ticker="syn", accession="0000999997-15-000001", form="10-K", as_of="2015-02-21")
            self.assertEqual(run.call_args.kwargs["payload"], {
                "accession": "0000999997-15-000001", "forms": ["10-K"], "as_of": "2015-02-21"})
            self.assertEqual(run.call_args.kwargs["ticker"], "SYN")
            with self.assertRaises(ValueError):
                nt.sec.filing(ticker="SYN", accession="guessed", form="10-K", as_of="2015-02-21")

    def test_filing_uses_the_existing_host_transport_and_public_surface(self):
        self.assertTrue({"filing", "statement_availability", "fact_availability"} <= set(nt.sec.__all__))
        with patch.object(nt.sec.host, "read_result", return_value=None), patch.object(
            nt.sec.host, "run_sec", return_value={"ok": True, "data": {"status": "located"}}
        ) as run:
            self.assertEqual(nt.sec.filing(ticker="syn", accession="0000999997-15-000001", form="10-K", as_of="2015-02-21"),
                             {"status": "located"})
            request = run.call_args.args[0]
            self.assertEqual({key: value for key, value in request.items() if key != "id"}, {
                "action": "filing", "ticker": "SYN", "accession": "0000999997-15-000001",
                "forms": ["10-K"], "asOf": "2015-02-21"})
            self.assertTrue(request["id"].startswith("sec:"))


if __name__ == "__main__":
    unittest.main()
