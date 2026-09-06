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
