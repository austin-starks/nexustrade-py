import unittest

from nexustrade.analysis_outputs import required_result_binding_findings, missing_required_outputs


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
