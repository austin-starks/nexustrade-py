"""Opt-in regression against actual cancelled producer, contract and filing bytes.

CANCELLED_ANALYSIS_WORKSPACE supplies the preserved session workspace. It is
never modified; replay uses a temporary copy of noncredential artifacts only.
No source retrieval, model requests or production writes are made.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexustrade import report_analysis as analysis
from nexustrade.analysis_outputs import required_result_binding_findings
from nexustrade.source_tables import read_source_tables, select_source_table_cell, SourceSelectionError

WORKSPACE = os.environ.get('CANCELLED_ANALYSIS_WORKSPACE')


@unittest.skipUnless(WORKSPACE, 'Provide the preserved cancelled session workspace')
class CancelledCapitalWorkReplay(unittest.TestCase):
    def setUp(self):
        self.saved = Path(WORKSPACE)
        self.criteria = json.loads((self.saved / 'acceptance/acceptance_criteria.json').read_text())['acceptanceCriteria']
        self.model = json.loads((self.saved / 'out/alphabet_model.json').read_text())

    def test_original_literal_selector_drops_a_present_comparative_balance(self):
        original = self.saved / 'in/data/research-inputs/sources/3091420372697537ba1856e910e9498ee7fcc3413d288c6cf4f5fa49f60665c1/original'
        document = read_source_tables(original.read_bytes(), source_id='alphabet_10k_2024')
        self.assertEqual(document['sourceSha256'], hashlib.sha256(original.read_bytes()).hexdigest())
        matches = [(table, cell) for table in document['tables'] for row in table['rows'] for cell in row
                   if ' '.join(cell['text'].split()).casefold() == 'non-marketable securities']
        self.assertTrue(matches, 'Retained original must contain the row the producer missed')
        selected = []
        for table, label in matches:
            for row in table['rows']:
                for header in row:
                    if ' '.join(header['text'].split()) != '2023':
                        continue
                    column = header['column']
                    try:
                        cell = table['grid'][label['row']][column]
                    except IndexError:
                        continue
                    if cell is None or '31,008' not in cell['text']:
                        continue
                    args = dict(table=table['index'], row=label['row'], column=column,
                        row_headers=[{'row': label['row'], 'column': label['column'], 'text': label['text']}],
                        column_headers=[{'row': header['row'], 'column': header['column'], 'text': header['text']}],
                        role='nonoperating investment candidate', rationale='Original filing investment note; classification still needs semantic review')
                    selected.append(select_source_table_cell(document, **args))
                    with self.assertRaisesRegex(SourceSelectionError, 'selector miss is not absent'):
                        select_source_table_cell(document, **{**args, 'row_headers': [
                            {'row': label['row'], 'column': label['column'], 'text': 'Total non-marketable securities'}]})
        self.assertTrue(selected, 'Source coordinates must preserve the disclosed 2023 amount and header')
        self.assertIn("('nonmarketable','total non-marketable securities')", (self.saved / 'scratch/prod.py').read_text())

    def test_actual_restructured_plan_keeps_exact_unbuilt_numeric_tasks(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'NEXUSTRADE_WORK_DIR': folder}):
            root = Path(folder)
            for directory in ('acceptance', 'scratch', 'in', 'out'):
                shutil.copytree(self.saved / directory, root / directory)
            status = analysis.analysis_status(include_review=False)
            roic = next(work for work in status['requiredResults'] if work['result']['id'] == 'historical_roic')
            self.assertEqual(roic['componentIds'], [])
            task = next(task for task in status['nextTasks'] if task.get('result', {}).get('id') == 'historical_roic')
            self.assertEqual(task['operation'], 'construct_required_result')
            self.assertEqual(task['result']['type'], 'number')
            self.assertTrue(task['readyToWork'])
            self.assertTrue(any(work['result']['id'] == 'wacc' for work in status['requiredResults']))

    def test_retained_two_observation_contract_cannot_weaken_counted_ten_period_scope(self):
        criterion = next(row for row in self.criteria if any(result['id'] == 'historical_roic' for result in row.get('results', [])))
        result = next(result for result in criterion['results'] if result['id'] == 'historical_roic')
        self.assertEqual(result['minimumCount'], 2)
        scope = {'dimension': 'fiscal_period', 'minimumDistinct': 10,
                 'sourceExcerpt': 'The analysis covers the historical period of the last 10 years when such historical company data is available'}
        with self.assertRaisesRegex(ValueError, 'do not shrink'):
            required_result_binding_findings([], [{**criterion, 'results': [{**result, 'coverageRequirement': scope}]}])
        corrected = {**criterion, 'results': [{**result, 'minimumCount': 10, 'coverageRequirement': scope}]}
        model = {'observations': self.model['historical_roic_eva']}
        bindings = [{'criterionId': criterion['id'], 'resultId': result['id'], 'type': 'number',
                     'path': f'/observations/{i}/roic', 'coveragePaths': {'fiscal_period': f'/observations/{i}/year'}}
                    for i in range(len(model['observations']))]
        findings = ' '.join(required_result_binding_findings(bindings, [corrected], model))
        self.assertIn('requires at least 10', findings)
        self.assertIn('requires 10 distinct fiscal_period', findings)

    def test_actual_sdk_calls_from_the_retained_chain_fail_before_control_mutation(self):
        source = (self.saved / 'scratch/finalize.py').read_text()
        self.assertIn('def build_current(execute_contract=True)', source)
        self.assertIn('report.plan_analysis(', source)
        self.assertIn('report.execute_analysis(timeout_sec=900)', source)
        # Exercise those actual SDK calls at the inherited producer boundary.
        # This control isolates orchestration; the full model is replayed separately.
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'NEXUSTRADE_WORK_DIR': folder,
            'PYTHONPATH': str(Path(__file__).resolve().parents[1])}):
            root = Path(folder)
            (root / 'acceptance').mkdir()
            (root / 'acceptance/acceptance_criteria.json').write_text(json.dumps({'acceptanceCriteria': [
                {'id': 'boundary', 'kind': 'required', 'text': 'Boundary control'}]}))
            producer = root / 'producer.py'
            producer.write_text("from nexustrade import report_analysis as a\na.plan_analysis(producer_path='/work/producer.py',model_path='/work/model.json',source_paths=[],components=[])\n")
            analysis.plan_analysis(producer_path='/work/producer.py', model_path='/work/model.json', source_paths=[],
                components=[{'id': 'boundary', 'kind': 'calculation', 'criterionIds': ['boundary'], 'dependsOn': [],
                    'outputPaths': ['/value'], 'outputRequirements': [{'criterionId': 'boundary', 'path': '/value', 'type': 'number'}]}])
            before = (root / analysis.PLAN_PATH).read_bytes()
            with self.assertRaises(subprocess.CalledProcessError):
                analysis.execute_analysis(timeout_sec=5)
            self.assertEqual((root / analysis.PLAN_PATH).read_bytes(), before)
            self.assertFalse((root / analysis.EXECUTION_PATH).exists())


if __name__ == '__main__':
    unittest.main()
