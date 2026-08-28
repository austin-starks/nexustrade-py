import json
import tempfile
import unittest
from pathlib import Path

from nexustrade import report


class ReportWriteTests(unittest.TestCase):
    def test_write_embeds_draft_markdown_in_canonical_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            inputs_path = tmp_path / "report_inputs.json"
            markdown_path = tmp_path / "output.md"

            report.write(
                "# Alphabet\n\nA source-backed investment conclusion.",
                inputs={"title": "Alphabet", "statistics": {"irr": 0.12}},
                inputs_path=str(inputs_path),
                markdown_path=str(markdown_path),
                images_dir=str(tmp_path / "images"),
                code_dir=str(tmp_path / "code"),
                code_paths=[],
            )

            payload = json.loads(inputs_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertEqual(payload["draftMarkdown"], markdown.strip())
            self.assertEqual(payload["statistics"], {"irr": 0.12})

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
