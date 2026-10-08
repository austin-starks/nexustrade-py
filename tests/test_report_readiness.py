import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexustrade import report
from nexustrade import report_readiness as readiness


class ReportReadinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.env = patch.dict(os.environ, {"NEXUSTRADE_WORK_DIR": str(self.root)})
        self.env.start()
        (self.root / "acceptance").mkdir()
        (self.root / "acceptance/acceptance_criteria.json").write_text(json.dumps({
            "acceptanceCriteria": [{"id": "cash", "kind": "required", "text": "Calculate cash flows"}]}))
        self.inputs = self.root / "inputs.json"
        self.inputs.write_text(json.dumps({"model": {"cashFlow": None}}))
        self.result = {"status": "needs_repair", "summary": "Calculation unfinished",
            "criteria": [{"id": "cash", "status": "gap", "reason": "Cash flows absent"}],
            "findings": [{"id": "readiness:one", "criterionId": "cash", "severity": "blocking",
                "inputPath": "/model/cashFlow", "problem": "Calculation absent",
                "missingState": "analysis_not_performed", "nextAction": "Compute the sourced forecast", "researchQuestion": None}]}
        self.credentials = patch.object(readiness, "_gateway_credentials", return_value=("https://gateway.example/api/v1", "test-token"))
        self.credentials.start()
        self.activity = patch.object(readiness, "_touch_host_activity")
        self.activity.start()

    def tearDown(self):
        self.activity.stop(); self.credentials.stop(); self.env.stop(); self.directory.cleanup()

    def response(self):
        from io import BytesIO
        return BytesIO(json.dumps(self.result).encode())

    def test_calls_host_once_and_reuses_unchanged_artifacts(self):
        before = self.inputs.read_bytes()
        with patch.object(readiness.urllib.request, "urlopen", side_effect=lambda *a, **k: self.response()) as fetch:
            first = report.validate(inputs_path=str(self.inputs))
            self.assertEqual(first, report.validate(inputs_path=str(self.inputs)))
            self.assertEqual(fetch.call_count, 1)
            body = json.loads(fetch.call_args.args[0].data)
            self.assertNotIn("request", body)
            self.assertNotIn("model", body)
            self.inputs.write_text(json.dumps({"model": {"cashFlow": None, "newEvidence": "source-one"}}))
            report.validate(inputs_path=str(self.inputs))
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(json.loads(before)["model"]["cashFlow"], None)

    def test_ambiguous_failure_uses_replay_only_without_paid_retry(self):
        with patch.object(readiness.urllib.request, "urlopen", side_effect=TimeoutError("ambiguous response")) as fetch:
            with self.assertRaises(TimeoutError): report.validate(inputs_path=str(self.inputs))
            self.assertEqual(fetch.call_count, 1)
        with patch.object(readiness.urllib.request, "urlopen", side_effect=lambda *a, **k: self.response()) as fetch:
            report.validate(inputs_path=str(self.inputs))
            self.assertTrue(fetch.call_args.args[0].full_url.endswith("/report/validate/replay"))

    def test_does_not_accept_a_ready_verdict_over_missing_calculations(self):
        self.result["status"] = "ready"
        with patch.object(readiness.urllib.request, "urlopen", side_effect=lambda *a, **k: self.response()):
            with self.assertRaisesRegex(ValueError, "contradicts"):
                report.validate(inputs_path=str(self.inputs))
        self.assertFalse(list((self.root / ".nexustrade/report-readiness").glob("*.json")))

    def test_no_paid_call_for_missing_method_or_oversized_artifact(self):
        with patch.object(readiness.urllib.request, "urlopen") as fetch:
            (self.root / "acceptance/acceptance_criteria.json").unlink()
            with self.assertRaises(FileNotFoundError): report.validate(inputs_path=str(self.inputs))
            self.inputs.write_text("x" * (readiness.MAX_INPUT_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "nothing was truncated"): report.validate(inputs_path=str(self.inputs))
            fetch.assert_not_called()

    def test_rejects_unresolvable_source_locations(self):
        self.result["findings"][0]["inputPath"] = "/invented"
        with patch.object(readiness.urllib.request, "urlopen", side_effect=lambda *a, **k: self.response()):
            with self.assertRaises(KeyError): report.validate(inputs_path=str(self.inputs))

    def test_refuses_outside_workspace(self):
        with self.assertRaisesRegex(ValueError, "inside the current workspace"):
            report.validate(inputs_path="/etc/passwd")

    def test_ready_limitations_do_not_trigger_a_repair_loop(self):
        self.result = {"status": "ready", "summary": "Supported within permitted limits", "criteria": [
            {"id": "cash", "status": "justified_limitation", "reason": "Actual source absence is documented and permitted"}], "findings": []}
        with patch.object(readiness.urllib.request, "urlopen", side_effect=lambda *a, **k: self.response()):
            self.assertEqual(report.validate(inputs_path=str(self.inputs))["status"], "ready")
