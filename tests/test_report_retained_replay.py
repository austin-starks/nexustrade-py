"""Opt-in no-service replay of preserved calculation artifacts; never executes a producer."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nexustrade import report


@unittest.skipUnless(os.environ.get("COMPUTE_AUDIT_REPLAY_WORKSPACE"), "retained artifacts not selected")
class RetainedReportReplay(unittest.TestCase):
    def test_actual_model_serialization_and_sdk_host_equality(self):
        root = Path(os.environ["COMPUTE_AUDIT_REPLAY_WORKSPACE"])
        original = (root / "out/model.json").read_bytes()
        model = json.loads(original)
        inputs = json.loads((root / "out/report_inputs.json").read_bytes())
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "out/model.json"
            report.write_model(model, path=str(target))
            self.assertLessEqual(target.stat().st_size, report.MODEL_SOURCE_MAX_BYTES)
            self.assertEqual(json.loads(target.read_bytes()), model)
            with patch.object(report, "WORK_DIR", temp):
                report._validate_saved_model("/work/out/model.json", inputs["calculationModel"])
        self.assertEqual((root / "out/model.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
