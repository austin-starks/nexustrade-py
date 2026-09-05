import json
import tempfile
import unittest
from pathlib import Path

from nexustrade import report


class ReportWriteTests(unittest.TestCase):
    def test_current_model_drives_repeated_values_and_sources(self):
        model = {'case': {'value': 72}, 'sources': [{'id': 'filing', 'url': 'https://example.test/filing'}]}
        payload = {'statistics': report.ref('case'),
                   'findings': [{'value': report.ref('case', 'value')}],
                   'sources': report.ref('sources'),
                   'sourceExcerpts': [{'sourceId': 'filing', 'quote': 'evidence'}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inputs.json'
            for value in (72, 94):
                model['case']['value'] = value
                report.write_inputs(payload, model=model, source_aliases={}, path=str(path))
                data = json.loads(path.read_text())
                self.assertEqual(data['statistics']['value'], value)
                self.assertEqual(data['findings'][0]['value'], value)
            before = path.read_bytes()
            for invalid in ({'value': report.ref('missing')},
                            {'sources': [{'id': 'one'}], 'sourceExcerpts': [{'sourceId': 'two'}]},
                            {'sources': [{'id': 'one'}, {'id': 'one'}]},
                            {'sources': [{'id': 'one'}], 'fetch_reconciliation': [{'id': 'two', 'used': True}]}):
                with self.assertRaises(ValueError):
                    report.write_inputs(invalid, model=model, source_aliases={}, path=str(path))
                self.assertEqual(path.read_bytes(), before)

    def test_fetch_ids_are_distinct_from_bibliography_ids_and_remain_unchanged(self):
        payload = {'sources': [{'id': 'annual-report'}],
                   'sourceExcerpts': [{'sourceId': 'fetch:annual', 'quote': 'source body'}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'inputs.json'
            # Legacy callers retain the host receipt verifier's namespace.
            report.write_inputs(payload, path=str(target))
            self.assertEqual(json.loads(target.read_text()), payload)
            # New callers explicitly bind durable fetch IDs to bibliography IDs.
            report.write(inputs=payload, model={}, source_aliases={'fetch:annual': 'annual-report'},
                         inputs_path=str(target), markdown_path=str(root/'output.md'),
                         images_dir=str(root/'images'), code_dir=str(root/'code'), code_paths=[])
            self.assertEqual(json.loads(target.read_text()), payload)
            with self.assertRaises(ValueError):
                report.write_inputs(payload, source_aliases={'fetch:annual': 'unknown'}, path=str(target))

    def test_model_references_require_explicit_model_and_valid_indices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'inputs.json')
            with self.assertRaises(ValueError):
                report.write_inputs({'x': report.ref('a')}, path=path)
            for key in (-1, True):
                with self.assertRaises(ValueError):
                    report.ref('a', key)
            report.write_inputs({'x': report.ref('a', 0)}, model={'a': [3]}, path=path)
            self.assertEqual(json.loads(Path(path).read_text())['x'], 3)

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
