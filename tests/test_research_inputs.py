import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexustrade import research


class RetainedInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'NEXUSTRADE_WORK_DIR': str(self.root)})
        self.env.start()
        self.index = self.root / 'in/data/research-inputs'
        self.index.mkdir(parents=True)
        self.sources = [
            {'id': 'irregular-old-filing', 'kind': 'fetch', 'url': 'https://publisher.example/reports/old-form',
             'path': '/work/document', 'contentType': 'application/pdf'},
            {'id': 'multipart', 'kind': 'lake', 'rowCount': 2, 'parts': [{'part': i, 'path': f'/work/part-{i}'} for i in range(2)]},
            {'id': 'concepts', 'kind': 'sec', 'path': '/work/concepts.json'},
            {'id': 'facts', 'kind': 'sec', 'path': '/work/facts.json'},
        ]
        self.manifest()
        (self.root / 'document').write_bytes(b'%PDF-1.7\nsource')
        for i in range(2): (self.root / f'part-{i}').write_bytes(b'PAR1fixture')
        self.concepts = {'action': 'filed_concepts', 'status': 'filed_concepts_returned',
                         'rows': [{'tag': 'CustomCapital', 'fact_count': 3, 'datatype': 'monetaryItemType'}]}
        self.facts = {'action': 'fact_instances', 'status': 'unreconciled', 'truncated': False,
                      'rows': [{'tag': 'CustomCapital', 'value_raw': 0, 'ddate': '2030-12-31', 'uom': 'EUR'},
                               {'tag': 'CustomCapital', 'value_raw': None, 'ddate': '2029-12-31'}]}
        (self.root / 'concepts.json').write_text(json.dumps(self.concepts))
        (self.root / 'facts.json').write_text(json.dumps(self.facts))
        (self.index / 'coverage.json').write_text(json.dumps({'sources': [
            {'id': 'facts', 'sourceAbsenceEstablished': False, 'rowSets': [{'key': 'rows', 'rowCount': 2,
             'fields': ['value_raw', 'ddate', 'uom'], 'rows': [{'numericFields': ['value_raw']}, {'nullFields': ['value_raw'], 'absentFields': ['uom']}]}]}]}))

    def manifest(self):
        (self.index / 'manifest.json').write_text(json.dumps({'schemaVersion': 1, 'sources': self.sources,
            'coveragePath': '/work/in/data/research-inputs/coverage.json'}))

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_pages_preserve_every_identity_and_lake_part_without_network(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network')):
            page = research.catalog(limit=2)
            self.assertEqual(page['nextOffset'], 2)
            tail = research.catalog(offset=page['nextOffset'], limit=2)
            self.assertIsNone(tail['nextOffset'])
            self.assertEqual([s['id'] for s in page['items'] + tail['items']], [s['id'] for s in self.sources])
            self.assertEqual([p['path'] for p in research.files('multipart')], ['/work/part-0', '/work/part-1'])
            self.assertFalse(page['sourceAbsenceEstablished'])

    def test_exact_source_identity_and_extensionless_format(self):
        with self.assertRaisesRegex(ValueError, 'no source was substituted'):
            research.files('invented-old-filing')
        self.assertEqual(research.catalog(query='publisher.example/reports/old-form')['items'][0]['id'], 'irregular-old-filing')
        self.assertEqual(research.format('irregular-old-filing')['files'][0]['container'], 'pdf')
        (self.root / 'document').write_bytes(b'PK\x03\x04xlsx container')
        self.assertEqual(research.format('irregular-old-filing')['files'][0]['container'], 'zip')

    def test_concept_metadata_is_not_a_numeric_fact_and_records_are_lossless(self):
        with self.assertRaisesRegex(ValueError, "filed_concepts.*expected 'fact_instances'") as error:
            research.response('concepts', expected='fact_instances')
        self.assertIn('available row fields', str(error.exception))
        self.assertEqual(research.response('concepts'), self.concepts)
        page = research.rows('facts', expected='fact_instances', limit=1)
        self.assertEqual(page['items'][0]['value_raw'], 0)
        self.assertEqual(page['selectors'], [['rows', 0]])
        tail = research.rows('facts', expected='fact_instances', offset=page['nextOffset'])
        self.assertIsNone(tail['items'][0]['value_raw'])
        self.assertNotIn('uom', tail['items'][0])
        self.assertEqual(page['items'] + tail['items'], self.facts['rows'])
        self.assertFalse(tail['sourceAbsenceEstablished'])

    def test_bounded_coverage_retains_zero_null_absent_and_next_page(self):
        self.assertEqual(research.coverage('facts')['rowSets'][0]['rowCount'], 2)
        page = research.coverage('facts', collection='rows', limit=1)
        self.assertEqual(page['nextOffset'], 1)
        self.assertEqual(research.coverage('facts', collection='rows', offset=1)['items'][0]['absentFields'], ['uom'])
        with self.assertRaisesRegex(ValueError, 'available='):
            research.coverage('facts', collection='invented')

    def test_malformed_and_outside_workspace_inputs_fail_closed(self):
        self.sources.append(dict(self.sources[0]))
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'Duplicate'): research.catalog()
        self.sources = self.sources[:1]
        self.sources[0]['path'] = '/work/../../etc/passwd'
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'inside'): research.files('irregular-old-filing')
        self.sources[0]['path'] = '/work/.jobenv'
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'credential'): research.files('irregular-old-filing')
        for args in ({'limit': 0}, {'offset': -1}, {'limit': True}):
            with self.assertRaises(ValueError): research.catalog(**args)
