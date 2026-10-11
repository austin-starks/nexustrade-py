"""Opt-in replay of retained real observations; no retrieval or model calls.

ANALYSIS_CONSTRUCTION_REPLAY_RECORDS names a local directory containing
initial.json, repair.json, contract.json and workspace's retained source bytes.
The original producer is replayed separately under network-disabled Docker.
This harness rematerializes observation JSON for SDK checks, retaining its
economic values and recording local transport hashes rather than claiming to
reproduce the original plan's byte formatting.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexustrade import finance, report_analysis as analysis, report_readiness as readiness
from nexustrade.analysis_outputs import required_result_binding_findings


RECORDS = os.environ.get('ANALYSIS_CONSTRUCTION_REPLAY_RECORDS')


@unittest.skipUnless(RECORDS, 'Provide local retained observations for offline replay')
class RetainedConstructionReplay(unittest.TestCase):
    def setUp(self):
        self.records = Path(RECORDS)
        self.initial = json.loads((self.records/'initial.json').read_text())['construction']
        self.repair = json.loads((self.records/'repair.json').read_text())['construction']
        self.contract = json.loads((self.records/'contract.json').read_text())['contractObservation']

    def test_real_series_loophole_and_retained_missing_outputs(self):
        criteria = self.contract['criteria']['acceptanceCriteria']
        historical = next(row for row in criteria if any(r['id']=='historical_roic_series' for r in row.get('results', [])))
        result = next(row for row in historical['results'] if row['id']=='historical_roic_series')
        self.assertEqual(result['minimumCount'], 1)
        self.assertEqual(len(self.initial['model']['historical_roic']), 2)
        with self.assertRaisesRegex(ValueError, 'series needs at least 2'):
            required_result_binding_findings([], [{**historical,'results':[{**result,'cardinality':'series'}]}])
        self.assertIsNone(self.repair['model']['shareholder_return']['long_term_irr'])
        self.assertIsNone(self.repair['model']['valuation']['enterprise_value_usd'])

    def test_real_scale_failure_is_reproduced_and_boundary_does_not_double_scale(self):
        producer = self.repair['producer']
        self.assertIn("b['short_debt']=statement_row['short_term_debt'] or 0.0", producer)
        self.assertIn("'short_debt':b.get('short_debt',0)*1e6", producer)
        # Compare a real retained annual source row with the producer's projection.
        annual_path = self.repair['plan']['sourcePaths'][0]
        annual = json.loads((self.records/'workspace'/annual_path.removeprefix('/work/')).read_text())
        rows = annual['rows']
        observed = self.repair['model']['historical_invested_capital_snapshots'][0]
        statement = next(row for row in rows if row['fiscal_year']==observed['year'] and row.get('cadence')=='annual')
        debt = statement['short_term_debt']
        self.assertEqual(observed['included_financing_side']['short_debt'], debt*1e6)
        aligned = finance.align_amounts({'debt':finance.amount(debt,unit='USD'),
            'other':finance.amount(100,unit='USD',scale=1e6)},unit='USD')
        self.assertEqual(aligned['values']['debt'], debt)
        self.assertNotEqual(aligned['values']['debt'], observed['included_financing_side']['short_debt'])
        with self.assertRaisesRegex(ValueError,'do not mix'):
            finance.wacc(finance.amount(100,unit='USD',scale=1e6),debt,.1,.05,.2)

    def test_actual_review_concerns_reach_sdk_and_withdrawal_requires_implementation(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'NEXUSTRADE_WORK_DIR':folder}):
            root = Path(folder)
            for logical in self.initial['plan']['sourcePaths']:
                target=root/logical.removeprefix('/work/');target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(self.records/'workspace'/logical.removeprefix('/work/'),target)
            (root/'acceptance').mkdir(exist_ok=True)
            (root/'acceptance/acceptance_criteria.json').write_text(json.dumps(self.contract['criteria']))
            producer=root/self.initial['plan']['producerPath'].removeprefix('/work/')
            producer.parent.mkdir(parents=True,exist_ok=True);producer.write_text(self.initial['producer'])
            plan_path=root/analysis.PLAN_PATH
            plan_path.write_text(json.dumps(self.initial['plan']))
            model_path=root/self.initial['plan']['modelPath'].removeprefix('/work/')
            model_path.parent.mkdir(parents=True,exist_ok=True);model_path.write_text(json.dumps(self.initial['model']))
            receipt={**self.initial['execution'],'planSha256':analysis._digest(plan_path),
                     'inputSha256':analysis._inputs(self.initial['plan']),'modelSha256':analysis._digest(model_path)}
            analysis._save(root/analysis.EXECUTION_PATH,receipt)
            record=self.contract['reviewReceipts'][0]['content']['records'][0]
            findings=[{**finding,'findingId':f'finding:retained-{index}','disposition':'open',
                       'readAgainstModelSha256':receipt['modelSha256']} for index,finding in enumerate(record['submission']['findings'])]
            self.assertEqual(len(findings),6)
            self.assertFalse(any(row['operation']=='repair_review_findings' for row in analysis.analysis_status()['nextTasks']))
            analysis._save(root/'.nexustrade/analysis/review-findings.json',{'version':1,'phase':'retained','findings':findings})
            status=analysis.analysis_status()
            task=next(row for row in status['nextTasks'] if row['operation']=='repair_review_findings')
            self.assertEqual(task['findings'],findings)
            report=analysis.attach_analysis_work({'calculationModel':self.initial['model']})
            target=root/'inputs.json';target.write_text(json.dumps(report))
            with patch.object(readiness.urllib.request,'urlopen') as paid:
                checked=readiness.validate(inputs_path=str(target))
                self.assertEqual(checked['status'],'needs_construction');paid.assert_not_called()
            # Rematerialize the actual post-review candidate, including withdrawn
            # outputs. Bind the same independent contract; do not fill numbers.
            plan_path.write_text(json.dumps(self.repair['plan']));producer.write_text(self.repair['producer'])
            model_path.write_text(json.dumps(self.repair['model']))
            receipt.update(planSha256=analysis._digest(plan_path),inputSha256=analysis._inputs(self.repair['plan']),modelSha256=analysis._digest(model_path))
            analysis._save(root/analysis.EXECUTION_PATH,receipt)
            tasks=analysis.analysis_status()['nextTasks']
            missing=[row for row in tasks if row.get('missingRequiredOutputs')]
            self.assertTrue(missing)
            self.assertTrue(all(row['operation']=='repair_producer' for row in missing))
            self.assertTrue(any('/shareholder_return/long_term_irr' in row['missingOutputs'] for row in missing))


if __name__ == '__main__':
    unittest.main()
