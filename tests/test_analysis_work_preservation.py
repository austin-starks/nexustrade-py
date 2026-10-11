"""Executor boundaries and persistent work, using real subprocesses without APIs."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexustrade import report_analysis as analysis
from nexustrade.analysis_outputs import required_result_binding_findings


class WorkPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'NEXUSTRADE_WORK_DIR': str(self.root),
            'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        self.environment.start()
        (self.root / 'acceptance').mkdir()
        self.criteria = [{'id': 'history', 'kind': 'required', 'text': 'Measure the last ten periods', 'results': [
            {'id': 'measurement', 'description': 'Measured history', 'type': 'number', 'cardinality': 'series',
             'minimumCount': 10, 'coverageRequirement': {'dimension': 'period', 'minimumDistinct': 10, 'sourceExcerpt': 'the last ten periods'}}]},
            {'id': 'discussion', 'kind': 'required', 'text': 'Explain', 'results': [
                {'id': 'explanation', 'description': 'Explanation', 'type': 'string', 'minimumCount': 1}]}]
        (self.root / 'acceptance/acceptance_criteria.json').write_text(json.dumps({'acceptanceCriteria': self.criteria}))
        self.component = {'id': 'measure', 'kind': 'calculation', 'criterionIds': ['history'], 'dependsOn': [],
            'outputPaths': ['/rows'], 'outputRequirements': [{'criterionId': 'history', 'resultId': 'measurement',
                'path': '/rows/0/value', 'type': 'number', 'coveragePaths': {'period': '/rows/0/period'}}]}
        self.producer = self.root / 'producer.py'
        self.producer.write_text("from pathlib import Path\nPath('model.json').write_text('{\"rows\":[{\"period\":2025,\"value\":0}]}')\n")

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def plan(self, components=None):
        return analysis.plan_analysis(producer_path='/work/producer.py', model_path='/work/model.json', source_paths=[],
                                      components=components or [self.component], scope='incremental')

    def test_first_partial_result_executes_but_every_unfinished_result_keeps_its_task(self):
        self.plan()
        status = analysis.execute_analysis()
        work = next(row for row in status['requiredResults'] if row['result']['id'] == 'measurement')
        self.assertEqual(work['componentIds'], ['measure'])
        self.assertEqual(work['result']['minimumCount'], 10)
        self.assertEqual(len(status['requiredResults']), 2)
        pending = {row['result']['id'] for row in status['nextTasks'] if row['operation'] == 'construct_required_result'}
        self.assertEqual(pending, {'measurement', 'explanation'})
        self.assertTrue(status['executionCurrent'])

    def test_replacement_cannot_erase_numeric_ownership_but_can_rename_and_repair(self):
        self.plan()
        before = (self.root / analysis.PLAN_PATH).read_bytes()
        qualitative = {**self.component, 'id': 'prose', 'criterionIds': ['discussion'],
                       'outputRequirements': [{'criterionId': 'discussion', 'resultId': 'explanation', 'path': '/explanation', 'type': 'string'}]}
        with self.assertRaisesRegex(ValueError, 'ownership cannot be removed'):
            self.plan([qualitative])
        self.assertEqual((self.root / analysis.PLAN_PATH).read_bytes(), before)
        self.plan([{**self.component, 'id': 'renamed'}, qualitative])

    def test_recursion_is_refused_before_second_launch_and_restores_outputs(self):
        self.plan()
        analysis.execute_analysis()
        before = (self.root / 'model.json').read_bytes()
        plan_before = (self.root / analysis.PLAN_PATH).read_bytes()
        self.producer.write_text("from pathlib import Path\nfrom nexustrade import report_analysis as a\n"
                                 "Path('launched').open('a').write('1')\na.execute_analysis()\n")
        with self.assertRaises(subprocess.CalledProcessError):
            analysis.execute_analysis(timeout_sec=5)
        self.assertEqual((self.root / 'launched').read_text(), '1')
        self.assertEqual((self.root / 'model.json').read_bytes(), before)
        self.assertEqual((self.root / analysis.PLAN_PATH).read_bytes(), plan_before)
        self.assertFalse((self.root / analysis.EXECUTION_PATH).exists())
        self.producer.write_text("from pathlib import Path\nPath('model.json').write_text('{\"rows\":[{\"period\":2025,\"value\":1}]}')\n")
        self.assertTrue(analysis.execute_analysis()['executionCurrent'])

    def test_reentry_blocks_plan_mutation_even_before_execute(self):
        self.plan()
        before = (self.root / analysis.PLAN_PATH).read_bytes()
        with patch.dict(os.environ, {analysis._PRODUCER_EXECUTION_ENV: 'inherited'}):
            with self.assertRaisesRegex(RuntimeError, 'analysis_producer_reentry'):
                self.plan()
        self.assertEqual((self.root / analysis.PLAN_PATH).read_bytes(), before)

    def test_distinct_periods_are_required_even_without_enumerated_dates(self):
        bindings = [{**self.component['outputRequirements'][0], 'path': f'/rows/{i}/value',
                     'coveragePaths': {'period': f'/rows/{i}/period'}} for i in range(10)]
        repeated = {'rows': [{'period': 2025, 'value': i} for i in range(10)]}
        self.assertIn('requires 10 distinct period', ' '.join(required_result_binding_findings(bindings, self.criteria[:1], repeated)))
        good = {'rows': [{'period': f'FY-{i}', 'value': 0} for i in range(10)]}
        self.assertEqual(required_result_binding_findings(bindings, self.criteria[:1], good), [])
        weakened = [{**self.criteria[0], 'results': [{**self.criteria[0]['results'][0], 'minimumCount': 2}]}]
        with self.assertRaisesRegex(ValueError, 'do not shrink'):
            required_result_binding_findings([], weakened)


if __name__ == '__main__':
    unittest.main()
