import unittest
from copy import deepcopy

from nexustrade import finance


def build_case(a):
    def flow(p):
        return p['reported'] if 'reported' in p else finance.fcff(
            finance.nopat(p['op'], a['tax']), p['da'], p['capex'], p['dnwc'])
    annual = [flow(p) for p in a['annual']]
    future = [(annual[0] - flow(a['elapsed'])) * a['remaining_fraction'], *annual[1:]]
    v = finance.fcff_valuation_case(
        forecast_fcff=future, discount_rate=a['wacc'], terminal_value=a['terminal'],
        cash_and_non_operating_assets=0, debt_and_debt_like_liabilities=0, diluted_shares=1,
        valuation_date='2028-09-30', cash_flow_dates=['2028-12-31', '2029-12-31'])
    return dict(future=future, valuation=v)


class SensitivityTests(unittest.TestCase):
    def setUp(self):
        self.a = dict(tax=.2, wacc=.1, terminal=200, remaining_fraction=.5,
            annual=[dict(op=140, da=10, capex=45, dnwc=4),
                    dict(op=170, da=12, capex=50, dnwc=5)],
            elapsed=dict(op=60, da=5, capex=55, dnwc=2))

    def test_tax_rebuilds_elapsed_and_full_year_with_independent_pv(self):
        before = deepcopy(self.a)
        result = finance.sensitivity_cases(build_case, self.a, {'base': {}, 'tax': {'tax': .35}})
        r = result['tax']['result']
        annual = 140*.65+10-45-4
        elapsed = 60*.65+5-55-2  # negative elapsed FCFF; no cached fraction
        first = (annual-elapsed)*.5
        last = 170*.65+12-50-5
        expected = first/1.1**(92/365)+(last+200)/1.1**(457/365)
        self.assertEqual(r['future'], [first, last])
        self.assertAlmostEqual(r['valuation']['per_share_value'], expected)
        original_annual = 140*.8+10-45-4
        frozen = annual * result['base']['result']['future'][0] / original_annual
        self.assertNotEqual(first, frozen)
        self.assertEqual(self.a, before)
        self.assertEqual(result['tax']['assumptions']['tax'], .35)

    def test_reported_elapsed_cash_is_fixed_and_negative_future_cash_is_allowed(self):
        self.a['elapsed'] = {'reported': 110}
        r = finance.sensitivity_cases(build_case, self.a, {'tax': {'tax': .35}})['tax']['result']
        self.assertEqual(r['future'][0], ((140*.65+10-45-4)-110)*.5)
        self.assertLess(r['future'][0], 0)

    def test_cases_and_inputs_do_not_share_mutable_state(self):
        def mutating_builder(a):
            a['annual'][0]['capex'] += 1
            return {'annual': a['annual']}
        before = deepcopy(self.a)
        variants = {'one': {}, 'two': {'annual': deepcopy(self.a['annual'])}}
        saved = deepcopy(variants)
        r = finance.sensitivity_cases(mutating_builder, self.a, variants)
        r['one']['result']['annual'][0]['capex'] = 999
        self.assertEqual(r['two']['result']['annual'][0]['capex'], 46)
        self.assertEqual(r['one']['assumptions'], before)
        self.assertEqual(self.a, before)
        self.assertEqual(variants, saved)

    def test_misspelled_inputs_and_invalid_results_fail(self):
        for variants in ({'typo': {'taz': .3}}, {'': {}}, {}, {'bad': []}):
            with self.assertRaises(ValueError):
                finance.sensitivity_cases(build_case, self.a, variants)
        with self.assertRaises(ValueError):
            finance.sensitivity_cases(lambda a: None, self.a, {'one': {}})


if __name__ == '__main__':
    unittest.main()
