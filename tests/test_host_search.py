import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from nexustrade import host


class DirectSearchTests(unittest.TestCase):
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
