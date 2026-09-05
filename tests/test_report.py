import json
import tempfile
import unittest
from pathlib import Path

from nexustrade import report


class ReportWriteTests(unittest.TestCase):
    def test_local_markdown_does_not_enter_host_report_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            inputs_path = tmp_path / "report_inputs.json"
            markdown_path = tmp_path / "output.md"

            report.write(
                "# Study\n\nAn obsolete conclusion.",
                inputs={"title": "Study", "statistics": {"effect": 0.12}},
                inputs_path=str(inputs_path),
                markdown_path=str(markdown_path),
                images_dir=str(tmp_path / "images"),
                code_dir=str(tmp_path / "code"),
                code_paths=[],
            )

            payload = json.loads(inputs_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertNotIn("draftMarkdown", payload)
            self.assertIn("An obsolete conclusion.", markdown)
            self.assertEqual(payload["statistics"], {"effect": 0.12})

    def test_structured_refresh_does_not_resurrect_previous_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "output.md"
            markdown.write_text("# Stale\n\nOld result 900.")
            report.write(
                inputs={"statistics": {"effect": 7}, "draftMarkdown": "legacy prose"},
                inputs_path=str(root / "report_inputs.json"),
                markdown_path=str(markdown),
                images_dir=str(root / "images"),
                code_dir=str(root / "code"),
                code_paths=[],
            )
            payload = json.loads((root / "report_inputs.json").read_text())
            self.assertEqual(payload, {"statistics": {"effect": 7}})
            self.assertNotIn("Old result", markdown.read_text())

    def test_write_inputs_removes_legacy_prose_without_mutating_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {"draftMarkdown": "legacy", "findings": [{"effect": 7}]}
            target = Path(directory) / "inputs.json"
            report.write_inputs(payload, path=str(target))
            self.assertEqual(json.loads(target.read_text()), {"findings": [{"effect": 7}]})
            self.assertEqual(payload["draftMarkdown"], "legacy")

    def test_write_inputs_does_not_require_a_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            inputs_path = tmp_path / "report_inputs.json"

            report.write(
                inputs={"title": "Structured only"},
                inputs_path=str(inputs_path),
                markdown_path=str(tmp_path / "output.md"),
                images_dir=str(tmp_path / "images"),
                code_dir=str(tmp_path / "code"),
                code_paths=[],
            )

            payload = json.loads(inputs_path.read_text(encoding="utf-8"))
            self.assertNotIn("draftMarkdown", payload)


if __name__ == "__main__":
    unittest.main()
