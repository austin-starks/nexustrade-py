import json
import tempfile
import unittest
from pathlib import Path

from nexustrade import report


class ReportWriteTests(unittest.TestCase):
    def test_write_accepts_path_alias_and_rejects_conflicting_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'aliased-inputs.json'
            report.write(inputs={'answer': 42}, path=str(target),
                         markdown_path=str(root/'output.md'), images_dir=str(root/'images'),
                         code_dir=str(root/'code'), code_paths=[])
            self.assertEqual(json.loads(target.read_text()), {'answer': 42})
            with self.assertRaisesRegex(ValueError, 'different report input files'):
                report.write(inputs={'answer': 42}, path=str(root/'one.json'),
                             inputs_path=str(root/'two.json'),
                             markdown_path=str(root/'output.md'), images_dir=str(root/'images'),
                             code_dir=str(root/'code'), code_paths=[])

    def test_method_requirements_alias_uses_host_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'inputs.json'
            requirements = [{'requirement': 'method:x:R-one', 'answer': 'Done.'}]
            report.write_inputs({'method_requirements': requirements}, path=str(target))
            self.assertEqual(json.loads(target.read_text()), {'requirements': requirements})
            with self.assertRaisesRegex(ValueError, 'different values'):
                report.write_inputs({'requirements': [], 'method_requirements': requirements},
                                    path=str(target))

    def test_local_model_source_is_recorded_in_logical_work_namespace(self):
        original_work_dir = report.WORK_DIR
        try:
            with tempfile.TemporaryDirectory() as directory:
                report.WORK_DIR = directory
                target = Path(directory) / 'inputs.json'
                model_path = Path(directory) / 'out' / 'model.json'
                report.write_inputs(
                    {'value': report.ref('value')}, model={'value': 7},
                    preserve_references=True, model_source=str(model_path), path=str(target)
                )
                saved = json.loads(target.read_text())
                self.assertEqual(saved['modelReferences'][0]['modelSource'], '/work/out/model.json')
        finally:
            report.WORK_DIR = original_work_dir

    def test_complete_current_model_survives_partial_selection_without_mutating_caller(self):
        model = {'valuation': {'price': 70}, 'capital': {'assets': 27, 'claims': 4}}
        payload = {'statistics': report.ref('valuation'), 'calculationModel': {'stale': True}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for price in (70, 90):
                model['valuation']['price'] = price
                report.write(inputs=payload, model=model, inputs_path=str(root/'inputs.json'),
                             markdown_path=str(root/'output.md'), images_dir=str(root/'images'),
                             code_dir=str(root/'code'), code_paths=[])
                data = json.loads((root/'inputs.json').read_text())
                self.assertEqual(data['calculationModel'], model)
                self.assertEqual(data['statistics']['price'], price)
                self.assertEqual(payload['calculationModel'], {'stale': True})
                self.assertNotIn('calculationModel', model)
            report.write(model=model, inputs_path=str(root/'model-only.json'),
                         markdown_path=str(root/'output.md'), images_dir=str(root/'images'),
                         code_dir=str(root/'code'), code_paths=[])
            self.assertEqual(json.loads((root/'model-only.json').read_text()), {'calculationModel': model})

    def test_explicit_passages_keep_distant_claims_without_splicing_or_clipping(self):
        first = "Year 2025 amounts in millions. North revenue was 17."
        last = "Year 2026 amounts in millions. South revenue was 29."
        long_quote = "Long passage " + "detailed source text " * 400 + "end marker."
        text = first + " " + "unrelated background " * 1000 + last + " " + long_quote
        passages = [first, last, long_quote]
        selections = report.source_excerpts("fetch:annual", text, passages=passages)
        self.assertEqual([item['quote'] for item in selections], passages)
        self.assertTrue(all(item['sourceId'] == 'fetch:annual' for item in selections))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'inputs.json'
            report.write_inputs({'sourceExcerpts': selections}, path=str(target))
            self.assertEqual(json.loads(target.read_text())['sourceExcerpts'], selections)
        with self.assertRaisesRegex(ValueError, 'does not occur'):
            report.source_excerpts('fetch:annual', text, passages=[first + ' ' + last])

    def test_passages_report_missing_and_ambiguous_matches_without_choosing_for_the_model(self):
        for passage, message in [('missing', 'does not occur'), ('value 12', 'ambiguous')]:
            with self.subTest(passage=passage), self.assertRaisesRegex(ValueError, message):
                report.source_excerpts('fetch:annual', 'North value 12. South value 12.', passages=[passage])
        self.assertEqual(report.source_excerpts('fetch:annual', 'North\nvalue 12.', passages=['North value 12.']),
                         [{'sourceId': 'fetch:annual', 'quote': 'North value 12.'}])
        with self.assertRaises(TypeError):
            report.source_excerpts('fetch:annual', 'source', passages='source')

    def test_optional_reference_map_refreshes_value_and_provenance_together(self):
        model = {'facts': {'flow': {'value': 12, 'sourceId': 'fetch:one', 'status': 'derived',
                                   'definition': 'FCFF', 'period_end': '2027-12-31'}}}
        payload = {'statistics': {'fcff': report.ref('facts', 'flow', 'value', provenance_path=('facts', 'flow'))}}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'inputs.json'
            for value, source in ((12, 'fetch:one'), (18, 'fetch:two')):
                model['facts']['flow'].update(value=value, sourceId=source)
                report.write_inputs(payload, model=model, preserve_references=True,
                                    model_source='/work/out/model.json', path=str(target))
                data = json.loads(target.read_text())
                self.assertEqual(data['statistics']['fcff'], value)
                self.assertEqual(data['modelReferences'], [{
                    'inputPath': ['statistics', 'fcff'], 'modelPath': ['facts', 'flow', 'value'],
                    'modelSource': '/work/out/model.json', 'provenancePath': ['facts', 'flow'],
                    'provenance': model['facts']['flow'],
                }])
            original = target.read_bytes()
            with self.assertRaises(ValueError):
                report.write_inputs({'x': report.ref('facts', 'flow', 'value', provenance_path=('absent',))},
                                    model=model, preserve_references=True, path=str(target))
            self.assertEqual(target.read_bytes(), original)
            with self.assertRaises(ValueError):
                report.write_inputs({'modelReferences': []}, preserve_references=True, path=str(target))
            report.write_inputs(payload, model=model, path=str(target))
            self.assertNotIn('modelReferences', json.loads(target.read_text()))
            report.write(inputs=payload, model=model, preserve_references=True,
                         model_source='/work/out/model.json', inputs_path=str(target),
                         markdown_path=str(Path(directory) / 'output.md'),
                         images_dir=str(Path(directory) / 'images'),
                         code_dir=str(Path(directory) / 'code'), code_paths=[])
            self.assertEqual(json.loads(target.read_text())['modelReferences'][0]['modelSource'],
                             '/work/out/model.json')

    def test_reference_metadata_must_be_an_object(self):
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory) / 'inputs.json')
            for metadata in (None, 7, ['not', 'metadata']):
                with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                    report.write_inputs({'x': report.ref('value', provenance_path=('metadata',))},
                                        model={'value': 2, 'metadata': metadata},
                                        preserve_references=True, path=target)
            report.write_inputs({'values': [report.ref('value')]},
                                model={'value': 2}, preserve_references=True, path=target)
            output = json.loads(Path(target).read_text())
            self.assertEqual(output['modelReferences'], [{'inputPath': ['values', 0], 'modelPath': ['value']}])

    def test_validation_checks_require_canonical_bound_source_ids_before_write(self):
        model = {
            'values': {'left': 12, 'right': 12},
            'provenance': {
                'left': {'source_ids': ['lake:one']},
                'right': {'sourceIds': ['lake:two']},
            },
        }
        payload = {
            'findings': {
                'left': report.ref('values', 'left', provenance_path=('provenance', 'left')),
                'right': report.ref('values', 'right', provenance_path=('provenance', 'right')),
            },
            'validationChecks': [{
                'id': 'check', 'kind': 'reconciliation', 'evidenceId': 'conclusion',
                'left': {'label': 'Calculated', 'inputPath': ['findings', 'left']},
                'right': {'label': 'Observed', 'inputPath': ['findings', 'right']},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'report_inputs.json'
            with self.assertRaisesRegex(ValueError, r'validationChecks\[0\]\.left.*provenance\.sourceIds'):
                report.write_inputs(payload, model=model, preserve_references=True,
                                    model_source='/work/out/model.json', path=str(target))
            self.assertFalse(target.exists())
            model['provenance']['left'] = {'sourceIds': ['lake:one']}
            report.write_inputs(payload, model=model, preserve_references=True,
                                model_source='/work/out/model.json', path=str(target))
            saved = json.loads(target.read_text())
            self.assertEqual(saved['modelReferences'][0]['provenance']['sourceIds'], ['lake:one'])
            self.assertEqual(saved['validationChecks'], payload['validationChecks'])
            self.assertNotIn('modelReferences', payload)

            model['values']['left'] = {'value': 12}
            with self.assertRaisesRegex(ValueError, 'exact scalar report.ref'):
                report.write_inputs(payload, model=model, preserve_references=True,
                                    model_source='/work/out/model.json', path=str(target))
            model['values']['left'] = 12

            payload['validationChecks'][0]['left']['inputPath'] = ['findings', 'missing']
            with self.assertRaisesRegex(ValueError, 'found 0 references'):
                report.write_inputs(payload, model=model, preserve_references=True,
                                    model_source='/work/out/model.json', path=str(target))
            self.assertEqual(json.loads(target.read_text()), saved)

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
            self.assertEqual(json.loads(target.read_text()), {**payload, 'calculationModel': {}})
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
            self.assertEqual(payload, {"title": "Study", "statistics": {"effect": 0.12}})
            self.assertIn("An obsolete conclusion.", markdown)
            self.assertEqual(payload["statistics"], {"effect": 0.12})

    def test_structured_refresh_does_not_resurrect_previous_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "output.md"
            markdown.write_text("# Stale\n\nOld result 900.")
            report.write(
                inputs={"statistics": {"effect": 7}},
                inputs_path=str(root / "report_inputs.json"),
                markdown_path=str(markdown),
                images_dir=str(root / "images"),
                code_dir=str(root / "code"),
                code_paths=[],
            )
            payload = json.loads((root / "report_inputs.json").read_text())
            self.assertEqual(payload, {"statistics": {"effect": 7}})
            self.assertNotIn("Old result", markdown.read_text())

    def test_write_inputs_does_not_mutate_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {"findings": [{"effect": 7}]}
            target = Path(directory) / "inputs.json"
            report.write_inputs(payload, path=str(target))
            self.assertEqual(json.loads(target.read_text()), {"findings": [{"effect": 7}]})
            self.assertEqual(payload, {"findings": [{"effect": 7}]})

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
            self.assertEqual(payload, {"title": "Structured only"})


if __name__ == "__main__":
    unittest.main()
