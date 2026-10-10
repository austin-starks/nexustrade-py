import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexustrade import report_analysis as analysis
from nexustrade import report_readiness as readiness
from nexustrade.report_json import assert_report_envelope


class AnalysisConstructionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"NEXUSTRADE_WORK_DIR": str(self.root),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")})
        self.environment.start()
        (self.root / "acceptance").mkdir()
        (self.root / "acceptance/acceptance_criteria.json").write_text(json.dumps({"acceptanceCriteria": [
            {"id": "history", "kind": "required", "text": "Historical returns"},
            {"id": "future", "kind": "required", "text": "Future claimant return"}]}))
        (self.root / "source.json").write_text(json.dumps({"opening": 80, "closing": 100, "income": 9}))
        (self.root / "producer.py").write_text('''import json
from pathlib import Path
from nexustrade import finance
s=json.loads(Path('source.json').read_text())
metrics=finance.operating_period_metrics(operating_income=s['income'], tax_rate=0,
 depreciation_and_amortization=1, capital_expenditures=2, current_operating_nwc=5,
 prior_operating_nwc=4, current_invested_capital=s['closing'],
 prior_invested_capital=s['opening'], cost_of_capital=.08,
 period_start='2028-01-01', period_end='2028-12-31')
Path('model.json').write_text(json.dumps({'historical':metrics,'future':None})+'\\n')
''')
        self.components = [
            {"id": "prior-period", "kind": "calculation", "criterionIds": ["history"], "dependsOn": [], "outputPaths": ["/historical/roic", "/historical/eva"],
             "outputRequirements": [{"criterionId":"history", "path":path, "type":"number"} for path in ["/historical/roic", "/historical/eva"]]},
            {"id": "future-owner", "kind": "calculation", "criterionIds": ["future"], "dependsOn": [], "outputPaths": ["/future"],
             "outputRequirements":[{"criterionId":"future", "path":"/future", "type":"number"}],
             "decisions": [{"kind": "future_policy", "state": "unresolved", "explanation": "Distribution policy not chosen", "evidencePaths": ["/work/source.json"]}]},
        ]

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def plan(self, components=None):
        return analysis.plan_analysis(producer_path="/work/producer.py", model_path="/work/model.json", source_paths=["/work/source.json"], components=components or self.components)

    def inputs(self):
        return analysis.attach_analysis_work({"calculationModel": json.loads((self.root / "model.json").read_text())})

    def test_history_completes_while_future_policy_is_unresolved_without_paid_review(self):
        self.plan()
        status = analysis.execute_analysis()
        self.assertEqual([row["status"] for row in status["components"]], ["computed", "not_started"])
        model = json.loads((self.root / "model.json").read_text())
        self.assertAlmostEqual(model["historical"]["roic"], .1)
        self.assertAlmostEqual(model["historical"]["eva"], 1.8)
        saved = self.root / "inputs.json"
        saved.write_text(json.dumps(self.inputs()))
        with patch.object(readiness.urllib.request, "urlopen") as paid:
            result = readiness.validate(inputs_path=str(saved))
            self.assertEqual(result["status"], "needs_construction")
            self.assertEqual(result["nextTasks"][0]["operation"], "resolve_primitive")
            paid.assert_not_called()

    def test_unplanned_canonical_model_cannot_purchase_broad_review(self):
        saved = self.root / "inputs.json"
        saved.write_text('{"calculationModel":{"historical":null,"future":null}}')
        with patch.object(readiness.urllib.request, "urlopen") as paid:
            result = readiness.validate(inputs_path=str(saved))
            self.assertEqual(result["status"], "needs_construction")
            self.assertEqual(result["nextTasks"][0]["operation"], "plan_analysis")
            self.assertEqual(result["nextTasks"][0]["criterionIds"], ["future", "history"])
            paid.assert_not_called()

    def test_patch_output_or_producer_cannot_keep_completion_status(self):
        self.plan(); analysis.execute_analysis()
        (self.root / "model.json").write_text('{"historical":{"roic":0.5},"future":null}')
        self.assertFalse(analysis.analysis_status()["executionCurrent"])
        self.assertTrue(all(row["status"] == "not_started" for row in analysis.analysis_status()["components"]))
        analysis.execute_analysis()
        script = self.root / "producer.py"
        script.write_text(script.read_text() + '\n# Changed definition\n')
        self.assertFalse(analysis.analysis_status()["executionCurrent"])

    def test_report_cannot_use_an_independently_patched_model_or_status(self):
        self.plan(); analysis.execute_analysis()
        inputs = self.inputs()
        inputs["calculationModel"]["historical"]["roic"] = .9
        with self.assertRaisesRegex(ValueError, "differs"):
            analysis.attach_analysis_work({"calculationModel": inputs["calculationModel"]})
        with self.assertRaisesRegex(ValueError, "hand-write"):
            analysis.attach_analysis_work(inputs)

    def test_decision_evidence_is_bound_even_when_not_repeated_in_source_paths(self):
        (self.root / "definition.txt").write_text("The selected capital definition and its justification")
        self.components[0]["decisions"] = [{"kind": "accounting_definition", "state": "resolved",
            "explanation": "Reviewed aligned components", "evidencePaths": ["definition.txt"]}]
        self.plan(); analysis.execute_analysis()
        status = analysis.analysis_status()
        self.assertIn("/work/definition.txt", status["execution"]["inputSha256"])
        self.assertEqual(status["components"][0]["status"], "computed")
        (self.root / "definition.txt").write_text("Changed definition")
        self.assertFalse(analysis.analysis_status()["executionCurrent"])

    def test_model_output_and_private_credentials_are_not_frozen_decision_evidence(self):
        self.components[0]["decisions"] = [{"kind": "accounting_definition", "state": "resolved",
            "explanation": "definition", "evidencePaths": ["model.json"]}]
        with self.assertRaisesRegex(ValueError, "separate from"):
            self.plan()
        self.components[0]["decisions"][0]["evidencePaths"] = [".jobenv"]
        with self.assertRaisesRegex(ValueError, "host-private"):
            self.plan()

    def test_missing_criterion_and_dependency_cycle_fail(self):
        with self.assertRaisesRegex(ValueError, "omitted"): self.plan(self.components[:1])
        self.components[0]["dependsOn"] = ["future-owner"]
        self.components[1]["dependsOn"] = ["prior-period"]
        with self.assertRaisesRegex(ValueError, "cycle"): self.plan()

    def test_long_dependency_chain_does_not_depend_on_python_recursion_depth(self):
        components = [{"id": f"part-{index}", "kind": "calculation", "criterionIds": ["history"],
            "dependsOn": [] if index == 0 else [f"part-{index-1}"], "outputPaths": ["/historical/roic"],
            "outputRequirements":[{"criterionId":"history", "path":"/historical/roic", "type":"number"}]}
            for index in range(1200)]
        components.append(self.components[1])
        self.plan(components)
        status = analysis.execute_analysis()
        self.assertTrue(all(row["status"] == "computed" for row in status["components"][:-1]))
        self.assertEqual(status["components"][-1]["status"], "not_started")

    def test_only_current_semantic_receipt_accepts_current_outputs(self):
        self.components[1].pop("decisions")
        script = self.root / "producer.py"
        script.write_text(script.read_text().replace("'future':None", "'future':0"))
        self.plan(); analysis.execute_analysis()
        self.assertTrue(all(row["status"] == "computed" for row in analysis.analysis_status()["components"]))
        inputs = self.inputs()
        receipt = self.root / ".nexustrade/report-readiness/test.json"
        receipt.parent.mkdir(parents=True)
        result = {"status": "ready", "criteria": [{"id": "history", "status": "met"}, {"id": "future", "status": "met"}]}
        receipt.write_text(json.dumps({"result": result}))
        analysis.record_readiness(receipt, result, inputs)
        self.assertTrue(all(row["status"] == "accepted" for row in analysis.analysis_status()["components"]))
        (self.root / "source.json").write_text('{"opening":90,"closing":100,"income":9}')
        self.assertTrue(all(row["status"] == "not_started" for row in analysis.analysis_status()["components"]))

    def test_unseen_nonfinancial_topology_uses_the_same_mechanism(self):
        (self.root / "acceptance/acceptance_criteria.json").write_text(json.dumps({"acceptanceCriteria": [{"id": "capacity", "kind": "required", "text": "Throughput"}]}))
        (self.root / "producer.py").write_text("import json\nfrom pathlib import Path\ns=json.loads(Path('source.json').read_text())\nPath('model.json').write_text(json.dumps({'throughput':s['closing']-s['opening']}))\n")
        self.plan([{"id": "warehouse", "kind": "calculation", "criterionIds": ["capacity"], "dependsOn": [], "outputPaths": ["/throughput"],
                   "outputRequirements":[{"criterionId":"capacity", "path":"/throughput", "type":"number"}]}])
        self.assertEqual(analysis.execute_analysis()["components"][0]["status"], "computed")
        self.assertIsNone(analysis.construction_checkpoint(self.inputs()))

    def test_host_required_results_are_discoverable_and_cannot_be_status_strings(self):
        criteria_path = self.root / 'acceptance/acceptance_criteria.json'
        criteria = json.loads(criteria_path.read_text())
        criteria['acceptanceCriteria'][0]['results'] = [
            {'id': 'roic', 'description': 'Period capital return', 'type': 'number', 'minimumCount': 1},
            {'id': 'eva', 'description': 'Period economic value added', 'type': 'number', 'minimumCount': 1}]
        criteria_path.write_text(json.dumps(criteria))
        self.assertEqual(analysis.analysis_requirements(), criteria['acceptanceCriteria'])
        with self.assertRaisesRegex(ValueError, 'independently required'):
            self.plan()
        for binding, result in zip(self.components[0]['outputRequirements'], ('roic', 'eva')):
            binding['resultId'] = result
        self.plan()
        self.assertEqual(analysis.execute_analysis()['components'][0]['status'], 'computed')
        self.components[0]['outputRequirements'][0].update(type='string', path='/historical/status')
        with self.assertRaisesRegex(ValueError, 'independently required'):
            self.plan()

    def test_rejected_research_does_not_make_executed_inputs_unavailable_for_repair(self):
        self.components[1]['dependsOn'] = ['prior-period']
        self.plan(); analysis.execute_analysis()
        inputs = self.inputs()
        receipt = self.root / '.nexustrade/report-readiness/test.json'
        receipt.parent.mkdir(parents=True)
        result = {'status': 'needs_repair', 'criteria': [{'id': 'history', 'status': 'gap'}, {'id': 'future', 'status': 'gap'}],
                  'findings': [{'criterionId': 'history', 'severity': 'blocking', 'problem': 'Definition needs review'}]}
        receipt.write_text(json.dumps({'result': result}))
        analysis.record_readiness(receipt, result, inputs)
        status = analysis.analysis_status()
        self.assertEqual(status['components'][0]['status'], 'review_rejected')
        future = next(row for row in status['nextTasks'] if row['componentId'] == 'future-owner')
        self.assertTrue(future['readyToWork'])
        self.assertEqual(future['waitingOn'], [])
        self.assertEqual(status['nextTasks'][0]['findings'], result['findings'])

    def test_envelope_supports_deep_values_and_refuses_cycles(self):
        value = {"v": 1}
        for _ in range(30): value = {"nested": value}
        assert_report_envelope(value)
        cycle = {}; cycle["self"] = cycle
        with self.assertRaisesRegex(ValueError, "cycle"): assert_report_envelope(cycle)

    def test_failed_or_nonproducing_execution_preserves_model_but_cannot_claim_new_success(self):
        self.plan(); analysis.execute_analysis()
        before = (self.root / "model.json").read_bytes()
        (self.root / "producer.py").write_text("# This script never writes its output\n")
        with self.assertRaisesRegex(ValueError, "without writing"):
            analysis.execute_analysis()
        self.assertEqual((self.root / "model.json").read_bytes(), before)
        self.assertFalse(analysis.analysis_status()["executionCurrent"])

    def test_failed_producer_restores_the_prior_report_and_model_snapshot(self):
        self.plan(); analysis.execute_analysis()
        (self.root / "out").mkdir(exist_ok=True)
        prior_report = self.root / "out/report_inputs.json"
        prior_report.write_text(json.dumps(self.inputs()))
        before = prior_report.read_bytes(), (self.root / "model.json").read_bytes()
        (self.root / "producer.py").write_text("from pathlib import Path\nPath('out/report_inputs.json').write_text('{}')\nraise RuntimeError('failure after partial report write')\n")
        with self.assertRaises(Exception):
            analysis.execute_analysis()
        self.assertEqual((prior_report.read_bytes(), (self.root / "model.json").read_bytes()), before)
        self.assertFalse(analysis.analysis_status()["executionCurrent"])

    def test_model_only_rebuild_requests_report_export_without_overwriting_the_old_report(self):
        self.plan(); analysis.execute_analysis()
        (self.root / "out").mkdir(exist_ok=True)
        prior_report = self.root / "out/report_inputs.json"
        prior_report.write_text(json.dumps(self.inputs()))
        before = prior_report.read_bytes()
        (self.root / "source.json").write_text('{"opening":90,"closing":100,"income":9}')
        result = analysis.execute_analysis()
        self.assertTrue(result["executionCurrent"])
        self.assertTrue(result["reportExportRequired"])
        self.assertEqual(prior_report.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "stale"):
            analysis.construction_checkpoint(json.loads(prior_report.read_text()))

    def test_review_rejection_propagates_to_declared_dependents_only(self):
        self.components[1].pop("decisions")
        self.components[1]["dependsOn"] = ["prior-period"]
        script = self.root / "producer.py"
        script.write_text(script.read_text().replace("'future':None", "'future':17"))
        self.plan(); analysis.execute_analysis()
        inputs = self.inputs()
        receipt = self.root / ".nexustrade/report-readiness/test.json"
        receipt.parent.mkdir(parents=True)
        result = {"status": "needs_repair", "criteria": [{"id": "history", "status": "gap"}, {"id": "future", "status": "met"}]}
        receipt.write_text(json.dumps({"result": result}))
        analysis.record_readiness(receipt, result, inputs)
        status = analysis.analysis_status()
        self.assertEqual([row["status"] for row in status["components"]], ["review_rejected", "review_rejected"])
        self.assertEqual(status["nextTasks"][0]["operation"], "repair_producer")

    def test_producer_exports_same_model_and_receipt_is_attached_after_execution(self):
        script = self.root / "producer.py"
        script.write_text(script.read_text() + "\nfrom nexustrade import report\nmodel=json.loads(Path('model.json').read_text())\nreport.write(inputs={'statistics':{}},model=model,model_source='/work/model.json',preserve_references=True)\n")
        self.plan(); analysis.execute_analysis()
        saved = json.loads((self.root / "out/report_inputs.json").read_text())
        self.assertTrue(saved["analysisWork"]["executionCurrent"])
        self.assertEqual(saved["analysisWork"], analysis.analysis_status(include_review=False))
