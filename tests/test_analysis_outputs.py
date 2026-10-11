import unittest

from nexustrade.analysis_outputs import required_result_binding_findings, missing_required_outputs, validate_output_requirements


class IndependentResultTests(unittest.TestCase):
    def setUp(self):
        # A held-out logistics brief, not a hardcoded financial checklist.
        self.criteria = [{'id': 'capacity', 'results': [
            {'id': 'throughput', 'description': 'Measured throughput for three shifts', 'type': 'number', 'minimumCount': 3},
            {'id': 'constraint', 'description': 'Explain the binding constraint', 'type': 'string', 'minimumCount': 1}]}]
        self.bindings = [{'criterionId': 'capacity', 'resultId': 'throughput', 'path': f'/shifts/{i}', 'type': 'number'} for i in range(3)]
        self.bindings.append({'criterionId': 'capacity', 'resultId': 'constraint', 'path': '/constraint', 'type': 'string'})

    def test_complete_independent_contract_and_real_zero(self):
        self.assertEqual(required_result_binding_findings(self.bindings, self.criteria), [])
        self.assertEqual(missing_required_outputs({'shifts': [0, 17, 21], 'constraint': 'Dock availability'}, self.bindings), [])

    def test_status_count_unknown_id_and_duplicate_leaves_cannot_weaken_contract(self):
        for change in ({'type': 'string', 'path': '/status'}, {'resultId': 'count'}, {'resultId': []}, {'path': '/shifts/1'}):
            bindings = [dict(row) for row in self.bindings]
            bindings[0].update(change)
            self.assertTrue(required_result_binding_findings(bindings, self.criteria))
        self.assertTrue(required_result_binding_findings(self.bindings[1:], self.criteria))

    def test_scalar_presence_still_rejects_uncomputed_results(self):
        for value in (None, 'not calculated', {}, True, float('inf')):
            missing = missing_required_outputs({'shifts': [value, 17, 21], 'constraint': 'Dock'}, self.bindings)
            self.assertEqual([row['path'] for row in missing], ['/shifts/0'])

    def test_malformed_host_contract_fails_closed(self):
        for results in (None, []):
            with self.assertRaises(ValueError):
                required_result_binding_findings(self.bindings, [{'id': 'capacity', 'results': results}])
        for change in ({'type': []}, {'minimumCount': True}, {'minimumCount': 0}, {'minimumCount': 2**53}, {'id': ''}):
            criteria = [{'id': 'capacity', 'results': [dict(self.criteria[0]['results'][0], **change)]}]
            with self.assertRaises(ValueError):
                required_result_binding_findings(self.bindings, criteria)

    def test_legacy_durable_criteria_are_not_reinterpreted(self):
        self.assertEqual(required_result_binding_findings([], [{'id': 'legacy'}]), [])

    def test_requested_history_cannot_be_satisfied_by_forecasts_or_repeated_periods(self):
        criteria = [{'id': 'observations', 'results': [{'id': 'measured', 'description': 'Historical observations', 'type': 'number', 'minimumCount': 2, 'coverage': [{'period': 2019}, {'period': 2020}]}]}]
        bindings = [{'criterionId': 'observations', 'resultId': 'measured', 'type': 'number', 'path': f'/rows/{i}/value', 'coveragePaths': {'period': f'/rows/{i}/period'}} for i in range(2)]
        validate_output_requirements(bindings, ['observations'])
        self.assertEqual(required_result_binding_findings(bindings, criteria), [])
        model = {'rows': [{'period': 2019.0, 'value': 0}, {'period': 2020, 'value': 17}]}
        self.assertEqual(missing_required_outputs(model, bindings), [])
        self.assertEqual(required_result_binding_findings(bindings, criteria, model), [])
        for periods in ([2036, 2037], [2019, 2019], ['2019', 2020], [None, 2020]):
            model = {'rows': [{'period': p, 'value': .1} for p in periods]}
            self.assertEqual(missing_required_outputs(model, bindings), [])
            self.assertTrue(required_result_binding_findings(bindings, criteria, model))
        self.assertTrue(required_result_binding_findings(bindings, criteria, {}))
        for selectors in (None, {'period': '2019'}, {'year': '/rows/0/period'}):
            changed = [dict(row, coveragePaths=selectors) for row in bindings]
            self.assertTrue(required_result_binding_findings(changed, criteria))

    def test_arbitrary_paired_scope_does_not_require_an_invented_cross_product(self):
        criteria = [{'id': 'ports', 'results': [{'id': 'flow', 'description': 'Port throughput', 'type': 'number', 'minimumCount': 2, 'coverage': [{'window': 'morning', 'port': 'east'}, {'window': 'evening', 'port': 'west'}]}]}]
        bindings = [{'criterionId': 'ports', 'resultId': 'flow', 'path': f'/rows/{i}/value', 'type': 'number', 'coveragePaths': {'window': f'/rows/{i}/window', 'port': f'/rows/{i}/port'}} for i in range(2)]
        model = {'rows': [{'window': 'morning', 'port': 'east', 'value': 0}, {'window': 'evening', 'port': 'west', 'value': 10}]}
        self.assertEqual(required_result_binding_findings(bindings, criteria, model), [])

    def test_malformed_explicit_coverage_is_not_a_weaker_contract(self):
        for coverage in ([], [{'period': True}], [{'period': ''}], [{'period': 2019}, {'period': '2020'}], [{'period': 2019}, {'year': 2020}], [{'period': 2019}, {'period': 2019}]):
            with self.assertRaises(ValueError):
                required_result_binding_findings([], [{'id': 'history', 'results': [{'id': 'r', 'description': 'Required result', 'type': 'number', 'minimumCount': 2, 'coverage': coverage}]}])
