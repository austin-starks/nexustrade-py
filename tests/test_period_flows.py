import unittest

from nexustrade import finance


class PeriodFlowTests(unittest.TestCase):
    def flow(self, value, start, end, **kwargs):
        options = dict(as_of='2027-09-04', unit='USD billions', definition='NOPAT+D&A-capex-dNWC',
                       provenance={'sourceId': 'filing', 'status': 'derived'})
        options.update(kwargs)
        return finance.period_flow(value, period_start=start, period_end=end, **options)

    def test_elapsed_gap_never_becomes_future_cash_or_automatic_proration(self):
        annual = self.flow(100, '2027-01-01', '2027-12-31')
        h1 = self.flow(60, '2027-01-01', '2027-06-30')
        result = finance.remaining_period_flow(annual, [h1], valuation_date='2027-09-04')
        self.assertIsNone(result['value'])
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['missing_intervals'], [{'period_start': '2027-07-01', 'period_end': '2027-09-04'}])
        self.assertEqual(result['period_start'], '2027-09-05')
        stub = self.flow(20, '2027-07-01', '2027-09-04', provenance={'status': 'model_assumption'})
        completed = finance.remaining_period_flow(annual, [stub, h1], valuation_date='2027-09-04')
        self.assertEqual(completed['value'], 20)
        self.assertEqual(completed['status'], 'derived')
        self.assertEqual(completed['elapsed_flows'][1]['provenance']['status'], 'model_assumption')
        self.assertEqual(completed['definition'], annual['definition'])
        self.assertEqual(completed['as_of'], '2027-09-04')

    def test_incompatible_or_overlapping_flows_are_rejected(self):
        annual = self.flow(100, '2027-01-01', '2027-12-31')
        h1 = self.flow(60, '2027-01-01', '2027-06-30')
        for overrides in ({'unit': 'EUR billions'}, {'definition': 'CFO-capex'}, {'as_of': '2027-09-05'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                finance.remaining_period_flow(annual, [dict(h1, **overrides)], valuation_date='2027-09-04')
        for other in (h1, self.flow(5, '2027-06-30', '2027-07-31'), self.flow(5, '2027-07-01', '2027-09-30')):
            with self.assertRaises(ValueError):
                finance.remaining_period_flow(annual, [h1, other], valuation_date='2027-09-04')

    def test_missing_amount_and_metadata_do_not_silently_become_zero(self):
        annual = self.flow(100, '2027-01-01', '2027-12-31')
        missing = self.flow(None, '2027-01-01', '2027-09-04', status='unavailable')
        result = finance.remaining_period_flow(annual, [missing], valuation_date='2027-09-04')
        self.assertIsNone(result['value'])
        self.assertEqual(result['missing_intervals'][0]['period_start'], '2027-01-01')
        for field in ('definition', 'unit', 'as_of', 'period_start'):
            bad = dict(annual)
            del bad[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                finance.remaining_period_flow(bad, [], valuation_date='2027-09-04')

    def test_same_date_h1_bridge_and_metadata_are_isolated(self):
        annual = self.flow(100, '2027-01-01', '2027-12-31', as_of='2027-06-30')
        h1 = self.flow(70, '2027-01-01', '2027-06-30', as_of='2027-06-30')
        result = finance.remaining_period_flow(annual, [h1], valuation_date='2027-06-30')
        self.assertEqual(result['value'], finance.forecast_remainder(100, 70)['remaining_forecast'])
        h1['provenance']['sourceId'] = 'changed'
        self.assertEqual(result['elapsed_flows'][0]['provenance']['sourceId'], 'filing')

    def test_invalid_dates_and_missing_forecast_fail_explicitly(self):
        for start, end in (('20270101', '2027-12-31'), ('2027-02-29', '2027-12-31'),
                           ('2027-12-31', '2027-01-01')):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.flow(100, start, end)
        annual = self.flow(None, '2027-01-01', '2027-12-31', status='unavailable')
        elapsed = self.flow(60, '2027-01-01', '2027-09-04')
        result = finance.remaining_period_flow(annual, [elapsed], valuation_date='2027-09-04')
        self.assertEqual(result['status'], 'incomplete')
        self.assertIsNone(result['full_period']['value'])
        self.assertIsNone(result['value'])
        for cutoff in ('2026-12-31', '2027-12-31', '2027-02-29'):
            with self.assertRaises(ValueError):
                finance.remaining_period_flow(annual, [], valuation_date=cutoff)


class FlowBasisTests(unittest.TestCase):
    """Frozen from the stopped 2026-09-07 run.

    ``remaining_period_flow`` rejected an elapsed reported CFO-minus-capex row
    against a modeled NOPAT+D&A-capex-dNWC forecast. The next edit gave both the
    same ``definition`` string. The exception went away; the amounts and the
    accounting gap did not change.
    """

    reported = {'measure': 'reported CFO less capital expenditures', 'origin': 'reported'}
    modeled = {'measure': 'NOPAT plus D&A less capex less change in operating NWC',
               'origin': 'modeled'}

    def flow(self, value, start, end, **kwargs):
        options = dict(as_of='2026-09-04', unit='USD', definition='free cash flow to the firm')
        options.update(kwargs)
        return finance.period_flow(value, period_start=start, period_end=end, **options)

    def test_a_shared_definition_string_no_longer_hides_a_different_measure(self):
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        h1 = self.flow(60, '2026-01-01', '2026-09-04', basis=self.reported)
        result = finance.remaining_period_flow(annual, [h1], valuation_date='2026-09-04')
        # The composer still computes; the mismatch is disclosed, not gated.
        self.assertEqual(result['value'], 40)
        reconciliation = result['basis_reconciliation']
        self.assertTrue(reconciliation['declared'])
        self.assertEqual(reconciliation['agreement'], 'differing-bases')
        self.assertEqual(reconciliation['origins'], ['modeled', 'reported'])
        self.assertEqual(len(reconciliation['measures']), 2)
        self.assertIn('different measurements', reconciliation['note'])

    def test_matching_declared_bases_reconcile(self):
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        h1 = self.flow(60, '2026-01-01', '2026-09-04', basis=self.modeled)
        reconciliation = finance.remaining_period_flow(
            annual, [h1], valuation_date='2026-09-04')['basis_reconciliation']
        # One declared basis is NOT a demonstrated bridge, and the record says so.
        self.assertEqual(reconciliation['agreement'], 'single-basis')
        self.assertIn('not evidence', reconciliation['note'])
        self.assertEqual(reconciliation['origins'], ['modeled'])

    def test_dropping_a_basis_is_visible_rather_than_silent(self):
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        h1 = self.flow(60, '2026-01-01', '2026-09-04')
        reconciliation = finance.remaining_period_flow(
            annual, [h1], valuation_date='2026-09-04')['basis_reconciliation']
        self.assertEqual(reconciliation['agreement'], 'undeclared')
        self.assertEqual(reconciliation['flows_without_declared_basis'], 1)

    def test_no_elapsed_flow_reconciles_nothing(self):
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        reconciliation = finance.remaining_period_flow(
            annual, [], valuation_date='2026-09-04')['basis_reconciliation']
        self.assertTrue(reconciliation['declared'])
        self.assertEqual(reconciliation['agreement'], 'no-elapsed-flow')
        self.assertIn('nothing was compared', reconciliation['note'])

    def test_no_declared_basis_keeps_the_previous_shape_and_says_so(self):
        annual = self.flow(100, '2026-01-01', '2026-12-31')
        h1 = self.flow(60, '2026-01-01', '2026-09-04')
        reconciliation = finance.remaining_period_flow(
            annual, [h1], valuation_date='2026-09-04')['basis_reconciliation']
        self.assertFalse(reconciliation['declared'])
        self.assertEqual(reconciliation['agreement'], 'undeclared-by-all')
        self.assertIn('do not establish', reconciliation['note'])

    def test_declared_adjustments_are_named_and_quantified(self):
        basis = dict(self.reported, adjustments=[
            {'name': 'stock-based compensation', 'value': -14.751},
            {'name': 'after-tax accrued interest', 'value': 1.521}])
        record = self.flow(60, '2026-01-01', '2026-09-04', basis=basis)
        self.assertEqual([row['name'] for row in record['basis']['adjustments']],
                         ['stock-based compensation', 'after-tax accrued interest'])
        self.assertAlmostEqual(record['basis']['total_adjustment'], -13.23)

    def test_a_malformed_basis_fails_instead_of_being_stored(self):
        for basis in ({'measure': 'x'}, {'origin': 'reported'}, {'measure': ' ', 'origin': 'reported'},
                      {'measure': 'x', 'origin': 'guessed'},
                      {'measure': 'x', 'origin': 'reported', 'adjustments': [{'name': 'a'}]},
                      {'measure': 'x', 'origin': 'reported', 'adjustments': [{'value': 1}]},
                      {'measure': 'x', 'origin': 'reported', 'adjustments': {'name': 'a', 'value': 1}}):
            with self.subTest(basis=basis), self.assertRaises(ValueError):
                self.flow(60, '2026-01-01', '2026-09-04', basis=basis)

    def test_a_basis_supplied_as_a_raw_dict_is_validated_by_the_composer(self):
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        h1 = dict(self.flow(60, '2026-01-01', '2026-09-04'), basis={'measure': 'x'})
        with self.assertRaises(ValueError):
            finance.remaining_period_flow(annual, [h1], valuation_date='2026-09-04')

    def test_a_quantified_bridge_is_distinguished_from_a_bare_mismatch(self):
        bridged = dict(self.reported, adjustments=[
            {'name': 'stock-based compensation', 'value': -14.751},
            {'name': 'cash tax timing', 'value': 5.13}])
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        h1 = self.flow(60, '2026-01-01', '2026-09-04', basis=bridged)
        rec = finance.remaining_period_flow(annual, [h1], valuation_date='2026-09-04')['basis_reconciliation']
        # Differing bases stay differing. Quantified adjustments are reported as
        # amounts, and no status claims they close the gap.
        self.assertEqual(rec['agreement'], 'differing-bases')
        self.assertAlmostEqual(rec['total_declared_adjustment'], -9.621)
        self.assertIn('for review', rec['note'])

    def test_collapsing_to_one_basis_is_not_reported_as_a_bridge(self):
        # The move that bought a full letter grade: drop the reported basis, put
        # every leg on the modeled one, and assert success. The record must not
        # supply that success.
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        h1 = self.flow(60, '2026-01-01', '2026-09-04', basis=self.modeled)
        rec = finance.remaining_period_flow(annual, [h1], valuation_date='2026-09-04')['basis_reconciliation']
        self.assertEqual(rec['agreement'], 'single-basis')
        self.assertNotIn('reconciled', rec)

    def test_no_status_can_be_asserted_as_a_holding_bridge(self):
        # A boolean was gamed by deleting a declaration. A "bridged" status was
        # WORSE: one adjustment of value 0.0 bought it. No status may mean the
        # bridge holds, and every value is enumerated for a caller to branch on.
        self.assertNotIn('bridged', finance.BASIS_AGREEMENTS)
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        zero = dict(self.reported, adjustments=[{'name': 'stock-based compensation', 'value': 0.0}])
        h1 = self.flow(60, '2026-01-01', '2026-09-04', basis=zero)
        rec = finance.remaining_period_flow(annual, [h1], valuation_date='2026-09-04')['basis_reconciliation']
        self.assertEqual(rec['agreement'], 'differing-bases')
        self.assertEqual(rec['total_declared_adjustment'], 0.0)

    def test_every_reachable_agreement_is_enumerated(self):
        annual = self.flow(100, '2026-01-01', '2026-12-31', basis=self.modeled)
        seen = {
            finance.remaining_period_flow(annual, [], valuation_date='2026-09-04')['basis_reconciliation']['agreement'],
            finance.remaining_period_flow(annual, [self.flow(60, '2026-01-01', '2026-09-04', basis=self.modeled)],
                valuation_date='2026-09-04')['basis_reconciliation']['agreement'],
            finance.remaining_period_flow(annual, [self.flow(60, '2026-01-01', '2026-09-04', basis=self.reported)],
                valuation_date='2026-09-04')['basis_reconciliation']['agreement'],
            finance.remaining_period_flow(annual, [self.flow(60, '2026-01-01', '2026-09-04')],
                valuation_date='2026-09-04')['basis_reconciliation']['agreement'],
            finance.remaining_period_flow(self.flow(100, '2026-01-01', '2026-12-31'),
                [self.flow(60, '2026-01-01', '2026-09-04')],
                valuation_date='2026-09-04')['basis_reconciliation'].get('agreement', 'undeclared-by-all'),
        }
        self.assertTrue(seen <= set(finance.BASIS_AGREEMENTS), seen)
