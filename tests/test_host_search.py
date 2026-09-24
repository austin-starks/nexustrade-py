import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from nexustrade import host


class DirectSearchTests(unittest.TestCase):
    def test_search_rejects_durable_receipt_envelope_access(self):
        payload = {'query': 'research', 'candidates': [{'url': 'https://example.com'}]}
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / 'results.jsonl'
            with patch.object(host, 'HOST_RESULTS_PATH', str(results)), \
                 patch.object(host, '_gateway_search', return_value=payload):
                direct = host.search('research')
                self.assertEqual(direct['candidates'], payload['candidates'])
                with self.assertRaisesRegex(KeyError, "result\\['candidates'\\]"):
                    direct.get('data', {}).get('candidates', [])
                cached = host.search('research')
                with self.assertRaisesRegex(KeyError, "result\\['candidates'\\]"):
                    cached['data']

    def test_neutral_default_and_explicit_dataset_preference_have_separate_cached_results(self):
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / 'results.jsonl'
            with patch.object(host, 'HOST_RESULTS_PATH', str(results)), \
                 patch.object(host, '_gateway_search', side_effect=lambda query, prefer: {'query': query, 'candidates': [], 'dataset_preference': prefer}) as gateway:
                neutral = host.search('public policy evidence')
                dataset = host.search('public policy evidence', prefer_machine_readable=True)
                self.assertFalse(neutral['dataset_preference'])
                self.assertTrue(dataset['dataset_preference'])
                self.assertEqual(host.search('public policy evidence', prefer_machine_readable=False), neutral)
                self.assertEqual(host.search('public policy evidence', prefer_machine_readable=True), dataset)
                self.assertEqual(gateway.call_count, 2)

    def test_broker_and_queue_preserve_neutral_default_and_dataset_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            requests = Path(directory) / 'requests.jsonl'
            with patch.object(host, 'HOST_REQUESTS_PATH', str(requests)), \
                 patch.object(host, 'HOST_RESULTS_PATH', str(Path(directory) / 'results.jsonl')), \
                 patch.object(host, '_pending_requests', []), \
                 patch.object(host, '_gateway_search', return_value=None):
                with self.assertRaises(SystemExit):
                    host.search('court decision')
                self.assertFalse(json.loads(requests.read_text())['preferMachineReadable'])
                host._pending_requests.clear()
                with self.assertRaises(SystemExit):
                    host.search('daily observations', prefer_machine_readable=True)
                self.assertTrue(json.loads(requests.read_text())['preferMachineReadable'])
                host._pending_requests.clear()
                host.queue_search('neutral', 'macro outlook')
                host.queue_search('dataset', 'macro series', prefer_machine_readable=True)
                self.assertEqual([r['preferMachineReadable'] for r in host._pending_requests], [False, True])

    def test_concurrent_pending_children_do_not_replace_primary_broker_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            requests = Path(directory) / 'host_requests.jsonl'
            results = Path(directory) / 'host_results.jsonl'
            original = json.dumps({'id': 'primary-request', 'type': 'search', 'query': 'primary'}) + '\n'
            requests.write_text(original)
            rendezvous = Barrier(2)

            def pending(*args):
                rendezvous.wait(timeout=5)
                return None

            def child(query):
                try:
                    host.search(query, allow_broker_fallback=False)
                except RuntimeError as error:
                    return str(error)
                self.fail('pending direct search did not raise')

            with patch.object(host, 'HOST_REQUESTS_PATH', str(requests)), \
                 patch.object(host, 'HOST_RESULTS_PATH', str(results)), \
                 patch.object(host, '_pending_requests', []), \
                 patch.object(host, '_gateway_search', side_effect=pending), \
                 ThreadPoolExecutor(max_workers=2) as pool:
                errors = list(pool.map(child, ['question one', 'question two']))
                self.assertTrue(all('broker fallback disabled' in error for error in errors))
                self.assertEqual(host._pending_requests, [])
                self.assertEqual(requests.read_text(), original)
                self.assertFalse(results.exists())

    def test_legacy_fallback_and_direct_success_cache_still_work(self):
        with tempfile.TemporaryDirectory() as directory:
            requests = Path(directory) / 'requests.jsonl'
            results = Path(directory) / 'results.jsonl'
            with patch.object(host, 'HOST_REQUESTS_PATH', str(requests)), \
                 patch.object(host, 'HOST_RESULTS_PATH', str(results)), \
                 patch.object(host, '_pending_requests', []), \
                 patch.object(host, '_gateway_search', return_value=None):
                with self.assertRaises(SystemExit):
                    host.search('legacy query')
                self.assertEqual(json.loads(requests.read_text())['query'], 'legacy query')
            payload = {'candidates': [], 'query': 'direct query'}
            with patch.object(host, 'HOST_RESULTS_PATH', str(results)), \
                 patch.object(host, '_gateway_search', return_value=payload) as gateway:
                self.assertEqual(host.search('direct query', allow_broker_fallback=False), payload)
                self.assertEqual(host.search('direct query', allow_broker_fallback=False), payload)
                gateway.assert_called_once()
