from copy import deepcopy
import unittest

from nexustrade import finance, sec


class QueriedAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.selector = dict(tag="IssuerSpecificPrepayments", accession="synthetic-A",
                             period_end="2028-09-30", unit="EUR", quarters=0, dimensions=None)
        self.row = dict(tag=self.selector['tag'], adsh="synthetic-A", ddate="20280930", uom="EUR",
                        qtrs="0", dimensions_raw=None, fact_id="opaque-fact-A", value_raw="0",
                        source_url="https://example.test/original")
        self.payload = dict(source_id="sec:synthetic", queryId="query-A", datasetSnapshotId="snapshot-A",
                            status="facts_returned", truncated=False, filingPlanTruncated=False,
                            normalized_query=dict(concepts=[self.selector['tag']]), rows=[self.row])

    def inspect(self, **overrides):
        return sec.fact_availability({**self.payload, **overrides}, {"prepayments": self.selector})['prepayments']

    def test_selected_zero_and_provenance_are_retained_without_mutation(self):
        before = deepcopy(self.payload)
        selected = self.inspect()
        self.assertEqual(selected['status'], 'selected')
        self.assertEqual(selected['fact']['value_raw'], '0')
        self.assertEqual(selected['fact']['fact_id'], 'opaque-fact-A')
        self.assertEqual(selected['query_coverage']['queryId'], 'query-A')
        selected['fact']['value_raw'] = '99'
        self.assertEqual(self.payload, before)

    def test_empty_query_does_not_claim_missing_disclosure(self):
        result = self.inspect(rows=[], status="no_matching_facts")
        self.assertEqual(result['status'], 'not_found_in_query')
        self.assertIsNone(result['fact'])
        self.assertFalse(result['source_absence_established'])
        result = self.inspect(normalized_query=dict(concepts=['AnUnrelatedConcept']))
        self.assertEqual(result['status'], 'not_queried')

    def test_uncovered_source_and_incomplete_queries_are_separate_from_empty_result(self):
        result = self.inspect(rows=[], status='source_not_covered')
        self.assertEqual(result['status'], 'source_not_retrieved')
        for flags in [dict(truncated=True), dict(filingPlanTruncated=True),
                      dict(status='source_metadata_incomplete'), dict(status='query_limit_reached')]:
            with self.subTest(flags=flags):
                result = self.inspect(**flags)
                self.assertEqual(result['status'], 'incomplete_query')
                self.assertIsNone(result['fact'])
                self.assertFalse(result['source_absence_established'])

    def test_context_mismatch_exposes_actual_unit_duration_and_dimensions(self):
        wrong = {**self.row, 'uom': 'EUR/share', 'qtrs': '2', 'dimensions_raw': 'Division=North'}
        result = self.inspect(rows=[wrong])
        self.assertEqual(result['status'], 'context_mismatch')
        self.assertEqual(result['returned_tag_contexts'][0]['uom'], 'EUR/share')
        self.assertIsNone(result['fact'])
        alternate_date = self.inspect(rows=[{**self.row, 'ddate': '20280630'}])
        self.assertEqual(alternate_date['status'], 'context_mismatch')
        self.assertEqual(alternate_date['returned_tag_contexts'][0]['ddate'], '20280630')

    def test_incomplete_source_cannot_hide_malformed_selectors_or_supply_a_fact(self):
        with self.assertRaisesRegex(ValueError, 'quarters'):
            sec.fact_availability({**self.payload, 'truncated': True},
                                  {'prepayments': {**self.selector, 'quarters': True}})
        with self.assertRaisesRegex(ValueError, 'Incomplete SEC Notes coverage'):
            sec.select_facts({**self.payload, 'status': 'source_metadata_incomplete'},
                             {'prepayments': self.selector})

    def test_ambiguity_invalid_values_and_unrequested_concepts_are_reported_together(self):
        payload = {**self.payload, 'rows': [self.row, {**self.row, 'fact_id': 'other-context'},
                                          {**self.row, 'tag': 'CustomAccrual', 'value_raw': 'NaN'}],
                   'normalized_query': dict(concepts=[self.selector['tag'], 'CustomAccrual'])}
        result = sec.fact_availability(payload, {'prepayments': self.selector,
            'accrual': {**self.selector, 'tag': 'CustomAccrual'},
            'another': {**self.selector, 'tag': 'AnUnrequestedEconomicRole'}})
        self.assertEqual([row['status'] for row in result.values()], ['ambiguous', 'invalid', 'not_queried'])
        self.assertTrue(all(row['fact'] is None for row in result.values()))
        with self.assertRaises(sec.FactSelectionError):
            sec.select_facts(payload, {'prepayments': self.selector})


class DatedBalanceChangesTests(unittest.TestCase):
    def snapshot(self, value, date, **kwargs):
        return finance.balance_snapshot(value, balance_date=date, as_of='2028-10-17',
                                        unit=kwargs.pop('unit', 'EUR'), definition='Selected operating NWC', **kwargs)

    def test_aligned_change_and_original_source_evidence_are_retained(self):
        opening = self.snapshot('12.300', '2028-10-17', scale=1e6,
                                provenance={'sourceIds': ['opening-bridge']})
        closing = self.snapshot('19.75', '2028-12-31', scale=1000000)
        result = finance.balance_change(opening, closing, period_start='2028-10-18', period_end='2028-12-31')
        self.assertAlmostEqual(result['value'], 7.45)
        self.assertEqual(result['scale'], opening['scale'])
        self.assertEqual(result['opening']['provenance']['sourceIds'], ['opening-bridge'])
        result['opening']['provenance']['sourceIds'].append('changed')
        self.assertEqual(opening['provenance']['sourceIds'], ['opening-bridge'])

    def test_rejects_elapsed_period_in_future_stub_and_wrong_closing_date(self):
        opening, closing = self.snapshot(12, '2028-09-30'), self.snapshot(19, '2028-12-31')
        with self.assertRaisesRegex(ValueError, 'opening balance date.*resolve the intervening'):
            finance.balance_change(opening, closing, period_start='2028-10-18', period_end='2028-12-31')
        with self.assertRaisesRegex(ValueError, 'closing balance date'):
            finance.balance_change(self.snapshot(12, '2028-10-17'), self.snapshot(19, '2028-12-30'),
                                   period_start='2028-10-18', period_end='2028-12-31')

    def test_handles_fiscal_year_and_real_zeros_but_does_not_impute_missing(self):
        result = finance.balance_change(self.snapshot(0, '2027-09-30'), self.snapshot(0, '2028-09-30'),
                                        period_start='2027-10-01', period_end='2028-09-30')
        self.assertEqual(result['value'], 0)
        self.assertEqual(result['status'], 'derived')
        result = finance.balance_change(self.snapshot(None, '2028-10-17'), self.snapshot(0, '2028-12-31'),
                                        period_start='2028-10-18', period_end='2028-12-31')
        self.assertIsNone(result['value'])
        self.assertEqual(result['missing_balances'], ['opening'])

    def test_refuses_currency_scale_definition_and_undeclared_date_mismatches(self):
        opening = self.snapshot(12, '2028-10-17')
        closing = self.snapshot(19, '2028-12-31')
        for field, wrong in [('unit', 'USD'), ('scale', '1000'), ('definition', 'Total current assets')]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                finance.balance_change(opening, {**closing, field: wrong},
                                       period_start='2028-10-18', period_end='2028-12-31')
        with self.assertRaisesRegex(ValueError, 'balance_date'):
            finance.balance_change({'value': 12}, closing, period_start='2028-10-18', period_end='2028-12-31')


if __name__ == '__main__':
    unittest.main()
