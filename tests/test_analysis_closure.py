"""Held-out construction shapes and durable semantic findings; no model calls."""
import json
import unittest
from unittest.mock import patch

from nexustrade import report_analysis as analysis
from nexustrade import report_readiness as readiness
from nexustrade.analysis_outputs import missing_required_outputs, validate_output_requirements
from tests import test_report_analysis as fixtures


class OutputContractTests(unittest.TestCase):
    setUp = fixtures.AnalysisConstructionTests.setUp
    tearDown = fixtures.AnalysisConstructionTests.tearDown
    plan = fixtures.AnalysisConstructionTests.plan
    inputs = fixtures.AnalysisConstructionTests.inputs
    def test_populated_container_does_not_complete_its_null_result(self):
        self.components[1].pop('decisions')
        self.components[1]['outputPaths'] = ['/historical']
        self.plan(); analysis.execute_analysis()
        task = analysis.analysis_status()['nextTasks'][0]
        self.assertEqual(task['missingRequiredOutputs'], [{'criterionId':'future','path':'/future','type':'number','reason':'missing_or_null'}])
        with patch.object(readiness.urllib.request, 'urlopen') as paid:
            self.assertEqual(readiness.validate(inputs_path=self.save_inputs())['status'], 'needs_construction')
            paid.assert_not_called()

    def save_inputs(self):
        target = self.root / 'inputs.json'
        target.write_text(json.dumps(self.inputs()))
        return str(target)

    def test_legacy_plan_requires_declaration_migration(self):
        self.plan(); analysis.execute_analysis()
        target = self.root / analysis.PLAN_PATH
        plan = json.loads(target.read_text()); plan['version'] = 1
        for row in plan['components']: row.pop('outputRequirements')
        target.write_text(json.dumps(plan))
        status = analysis.analysis_status()
        self.assertTrue(all(task['operation'] == 'declare_outputs' for task in status['nextTasks']))
        self.assertFalse(any(row['status'] == 'computed' for row in status['components']))

    def test_host_receipt_is_saved_without_patching_calculations_or_rebuying_validation(self):
        from io import BytesIO
        self.components[1].pop('decisions')
        producer = self.root / 'producer.py'
        producer.write_text(producer.read_text().replace("'future':None", "'future':0"))
        self.plan(); analysis.execute_analysis()
        target = self.save_inputs(); before = json.loads(self.root.joinpath('model.json').read_text())
        result = {'status':'ready','summary':'Fixture independent acceptance','criteria':[{'id':criterion,'status':'met','reason':'Observed result'} for criterion in ['history','future']],
                  'findings':[], 'hostReceipt':'opaque-fixture-host-proof'}
        with patch.object(readiness,'_gateway_credentials',return_value=('https://fixture.invalid','not-a-credential')), \
             patch.object(readiness,'_touch_host_activity'), \
             patch.object(readiness.urllib.request,'urlopen',side_effect=lambda *args,**kwargs:BytesIO(json.dumps(result).encode())) as paid:
            self.assertEqual(readiness.validate(inputs_path=target), result)
            self.assertEqual(readiness.validate(inputs_path=target), result)
            self.assertEqual(paid.call_count, 1)
        saved = json.loads(self.root.joinpath('inputs.json').read_text())
        self.assertEqual(saved['analysisWork']['readinessReceipt'], result['hostReceipt'])
        self.assertEqual(saved['calculationModel'], before)
        self.assertEqual(json.loads(self.root.joinpath('model.json').read_text()), before)
        changed = {**saved,'readerFacingResult':'Edited while review was active'}
        self.root.joinpath('inputs.json').write_text(json.dumps(changed))
        supplied = {**saved,'analysisWork':{key:value for key,value in saved['analysisWork'].items() if key!='readinessReceipt'}}
        with self.assertRaisesRegex(ValueError, 'changed during validation'):
            readiness._save_acceptance(self.root/'inputs.json', supplied, result)
        self.assertEqual(json.loads(self.root.joinpath('inputs.json').read_text()), changed)

    def test_rejected_work_survives_comment_only_execution_without_paid_review(self):
        self.components[1].pop('decisions')
        producer = self.root / 'producer.py'
        producer.write_text(producer.read_text().replace("'future':None", "'future':0"))
        self.plan(); analysis.execute_analysis()
        receipt = self.root / '.nexustrade/report-readiness/rejected.json'
        receipt.parent.mkdir(parents=True)
        result = {'status':'needs_repair','criteria':[{'id':'history','status':'gap'},{'id':'future','status':'met'}],
                  'findings':[{'criterionId':'history','severity':'blocking','nextAction':'Reconcile selected boundaries','problem':'Misaligned observations'}]}
        receipt.write_text(json.dumps({'result':result}))
        analysis.record_readiness(receipt, result, self.inputs())
        producer.write_text(producer.read_text() + '\n# Producer edited, economics unchanged\n')
        analysis.execute_analysis()
        status = analysis.analysis_status()
        self.assertEqual(status['components'][0]['status'], 'review_rejected')
        self.assertEqual(status['nextTasks'][0]['findings'], result['findings'])
        with patch.object(readiness.urllib.request, 'urlopen') as paid:
            result = readiness.validate(inputs_path=self.save_inputs())
            self.assertEqual(result['nextTasks'][0]['operation'], 'repair_producer')
            paid.assert_not_called()
        report_repair = self.inputs()
        report_repair['readerFacingResult'] = 'The actual selected output is now explicitly delivered'
        self.assertIsNone(analysis.construction_checkpoint(report_repair))
        self.assertEqual(analysis.analysis_status()['components'][0]['status'], 'review_rejected')
        (self.root / 'source.json').write_text('{"opening":90,"closing":100,"income":9}')
        analysis.execute_analysis()
        self.assertIsNone(analysis.construction_checkpoint(self.inputs()))
        self.assertEqual(analysis.analysis_status()['components'][0]['status'], 'review_rejected')


class HeldOutScalarTests(unittest.TestCase):
    def test_unseen_shapes_zero_false_and_escaped_keys(self):
        model = {'assembly/line': {'~stations':[{'yield':0, 'approved':False, 'basis':'Observed'}]}}
        rows = [{'criterionId':'throughput', 'path':'/assembly~1line/~0stations/0/'+key, 'type':kind}
                for key,kind in [('yield','number'),('approved','boolean'),('basis','string')]]
        validate_output_requirements(rows, ['throughput'])
        self.assertEqual(missing_required_outputs(model,rows), [])
        for value in [None, {}, [], '', True, float('inf'), float('nan')]:
            with self.subTest(value=value):
                model['assembly/line']['~stations'][0]['yield'] = value
                self.assertEqual(len(missing_required_outputs(model,rows)), 1)

    def test_every_criterion_needs_exact_result_leaves(self):
        for value in [None, [], [{'criterionId':[], 'path':'/x','type':'number'}],
                      [{'criterionId':'a','path':'/x','type':'object'}],
                      [{'criterionId':'a','path':'/x','type':'number'}]*2]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_output_requirements(value,['a','b'])
