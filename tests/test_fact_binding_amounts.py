from copy import deepcopy
import unittest

from nexustrade import finance, sec


class FactBindingTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(tag="InventoryNet", adsh="filing-A", ddate="20240630",
                        uom="USD", qtrs="0", dimensions_raw=None, fact_id="returned-opaque-id",
                        value_raw="1200000000.0000", source_url="https://example.test/filing-A")
        self.payload = dict(truncated=False, filingPlanTruncated=False, source_id="sec:owned",
                            normalized_query=dict(concepts=["InventoryNet"]), rows=[self.row])
        self.selector = dict(tag="InventoryNet", accession="filing-A", period_end="2024-06-30",
                             unit="USD", quarters=0, dimensions=None)

    def test_selects_original_id_without_transcription_and_preserves_evidence(self):
        before = deepcopy(self.payload)
        result = sec.select_facts(self.payload, {"inventory": self.selector})["inventory"]
        self.assertEqual(result["fact_id"], self.row["fact_id"])
        self.assertEqual(result["value_raw"], "1200000000.0000")
        self.assertEqual(result["provenance"]["sourceIds"], ["sec:owned"])
        self.assertEqual(result["provenance"]["source_url"], self.row["source_url"])
        result["provenance"]["sourceIds"].append("changed")
        result["value_raw"] = "0"
        self.assertEqual(self.payload, before)

    def test_reports_copied_id_and_unrequested_concept_in_one_preflight(self):
        selections = {"inventory": {**self.selector, "fact_id": "mistyped-id"},
                      "accrual": {**self.selector, "tag": "UnrequestedAccrual"}}
        with self.assertRaises(sec.FactSelectionError) as caught:
            sec.select_facts(self.payload, selections)
        self.assertEqual([d["reason"] for d in caught.exception.diagnostics],
                         ["fact_id_mismatch", "concept_not_requested"])
        self.assertEqual(caught.exception.diagnostics[0]["available_contexts"][0]["fact_id"],
                         "returned-opaque-id")

    def test_does_not_conflate_equal_values_alternate_contexts_or_missing_rows(self):
        self.payload["rows"].append({**self.row, "fact_id": "alternate-id"})
        with self.assertRaisesRegex(sec.FactSelectionError, "ambiguous_context"):
            sec.select_facts(self.payload, {"inventory": self.selector})
        selected = sec.select_facts(self.payload, {"inventory": {**self.selector,
                                  "fact_id": self.payload["rows"][1]["fact_id"]}})
        self.assertEqual(selected["inventory"]["fact_id"], "alternate-id")
        self.payload["rows"] = []
        with self.assertRaisesRegex(sec.FactSelectionError, "no_matching_context"):
            sec.select_facts(self.payload, {"inventory": self.selector})

    def test_matches_period_filing_unit_duration_and_dimensions_exactly(self):
        for field, wrong in (("adsh", "filing-B"), ("ddate", "20231231"), ("uom", "EUR"),
                             ("qtrs", "2"), ("dimensions_raw", "Region=Europe")):
            with self.subTest(field=field):
                payload = {**self.payload, "rows": [{**self.row, field: wrong}]}
                with self.assertRaisesRegex(sec.FactSelectionError, "no_matching_context"):
                    sec.select_facts(payload, {"inventory": self.selector})
        payload = {**self.payload, "rows": [{**self.row, "dimensions_raw": "Region=Europe"}]}
        self.assertEqual(sec.select_facts(payload, {"inventory": {**self.selector,
                         "dimensions": "Region=Europe"}})["inventory"]["dimensions_raw"], "Region=Europe")

    def test_refuses_incomplete_invalid_or_silently_misspelled_inputs(self):
        for flag in ("truncated", "filingPlanTruncated"):
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                sec.select_facts({**self.payload, flag: True}, {"inventory": self.selector})
        for value in ("NaN", "Infinity", None, True):
            with self.subTest(value=value), self.assertRaisesRegex(sec.FactSelectionError, "invalid_source"):
                sec.select_facts({**self.payload, "rows": [{**self.row, "value_raw": value}]},
                                 {"inventory": self.selector})
        with self.assertRaises(ValueError):
            sec.select_facts(self.payload, {"inventory": {**self.selector, "units": "USD"}})


class AmountAlignmentTests(unittest.TestCase):
    def test_record_boundary_normalizes_millions_and_dollars_once(self):
        # The observed failure inserted raw debt dollars into HTML millions and
        # multiplied the entire dictionary again. These operands retain scale.
        rows = {"operating_income": finance.amount(9, unit="EUR", scale=1e6),
                "depreciation_and_amortization": finance.amount(1_000_000, unit="EUR"),
                "capital_expenditures": finance.amount(2, unit="EUR", scale=1e6),
                "current_operating_nwc": finance.amount(5, unit="EUR", scale=1e6),
                "prior_operating_nwc": finance.amount(4_000_000, unit="EUR"),
                "current_invested_capital": finance.amount(100, unit="EUR", scale=1e6),
                "prior_invested_capital": finance.amount(80_000_000, unit="EUR")}
        before = deepcopy(rows)
        result = finance.operating_period_metrics(**rows, tax_rate=0, cost_of_capital=.08)
        self.assertEqual(result['average_invested_capital'], 90_000_000)
        self.assertAlmostEqual(result['roic'], .1)
        self.assertAlmostEqual(result['eva'], 1_800_000)
        self.assertEqual(result['amount_alignment']['inputs'], before)
        self.assertEqual(rows, before)
        self.assertEqual(finance.invested_capital_from_operations(
            finance.amount(100, unit='EUR', scale=1e6), finance.amount(20_000_000, unit='EUR'),
            finance.amount(0, unit='EUR')), 80_000_000)
        self.assertAlmostEqual(finance.wacc(finance.amount(1, unit='EUR', scale=1e9),
            finance.amount(200_000_000, unit='EUR'), .12, .05, .2), finance.wacc(1, .2, .12, .05, .2))
        for change in (finance.amount(80, unit='USD', scale=1e6), 80_000_000):
            with self.assertRaises(ValueError):
                finance.operating_period_metrics(**{**rows, 'prior_invested_capital': change}, tax_rate=0, cost_of_capital=.08)
        with self.assertRaisesRegex(ValueError, 'do not mix'):
            finance.wacc(finance.amount(1, unit='EUR'), 1, .1, .05, .2)

    def test_normalizes_raw_dollars_and_forecast_billions_with_retained_inputs(self):
        facts = {"opening": finance.amount("1200000000.0000", unit="USD",
                                           provenance={"sourceIds": ["sec:owned"]}),
                 "change": finance.amount(0.2, unit="USD", scale=1_000_000_000)}
        before = deepcopy(facts)
        aligned = finance.align_amounts(facts, unit="USD", scale=1_000_000_000)
        self.assertEqual(aligned["values"], {"opening": 1.2, "change": 0.2})
        self.assertAlmostEqual(sum(aligned["values"].values()), 1.4)
        aligned["inputs"]["opening"]["provenance"]["sourceIds"].append("changed")
        self.assertEqual(facts, before)
        dollars = finance.align_amounts(facts, unit="USD")
        self.assertEqual(dollars["values"], {"opening": 1_200_000_000, "change": 200_000_000})

    def test_wacc_and_operating_cash_are_equivalent_in_common_scales(self):
        raw = {"equity": finance.amount(1_000_000_000, unit="USD"),
               "debt": finance.amount(0.2, unit="USD", scale=1e9)}
        dollars = finance.align_amounts(raw, unit="USD")["values"]
        billions = finance.align_amounts(raw, unit="USD", scale=1e9)["values"]
        self.assertAlmostEqual(finance.wacc(dollars["equity"], dollars["debt"], .12, .05, .2),
                               finance.wacc(billions["equity"], billions["debt"], .12, .05, .2))
        self.assertNotAlmostEqual(finance.wacc(1_000_000_000, .2, .12, .05, .2),
                                  finance.wacc(billions["equity"], billions["debt"], .12, .05, .2))

    def test_refuses_different_currencies_per_share_units_and_undeclared_amounts(self):
        for unit in ("EUR", "USD/share", "shares"):
            with self.subTest(unit=unit), self.assertRaisesRegex(ValueError, "incompatible unit"):
                finance.align_amounts({"debt": finance.amount(1, unit=unit)}, unit="USD")
        with self.assertRaisesRegex(ValueError, "declared amount"):
            finance.align_amounts({"debt": 12}, unit="USD")
        with self.assertRaisesRegex(ValueError, "missing value/unit/scale"):
            finance.align_amounts({"debt": {"value": 12, "unit": "USD"}}, unit="USD")

    def test_preserves_real_zero_refuses_nonfinite_and_invalid_scales(self):
        self.assertEqual(finance.align_amounts({"zero": finance.amount(0, unit="USD")},
                                             unit="USD")["values"]["zero"], 0)
        for scale in (0, -1, True, "NaN", "Infinity"):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                finance.amount(12, unit="USD", scale=scale)
        for value in (True, "NaN", "Infinity", None, "missing"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                finance.amount(value, unit="USD")
        with self.assertRaisesRegex(ValueError, "outside finite"):
            finance.align_amounts({"large": finance.amount("1e999", unit="USD")}, unit="USD")
        with self.assertRaisesRegex(ValueError, "outside finite"):
            finance.align_amounts({"tiny": finance.amount("1e-999", unit="USD")}, unit="USD")


if __name__ == "__main__":
    unittest.main()
