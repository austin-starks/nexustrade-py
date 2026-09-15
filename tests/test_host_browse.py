import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexustrade import host


class HostBrowseTests(unittest.TestCase):
    def test_queue_browse_writes_browse_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            requests = Path(directory) / "requests.jsonl"
            with patch.object(host, "HOST_REQUESTS_PATH", str(requests)), patch.object(
                host, "_pending_requests", []
            ):
                host.queue_browse(
                    "b1",
                    "list posts",
                    start_url="https://www.tiktok.com/@creator",
                    limit=25,
                )
                self.assertTrue(host.flush_requests())
                row = json.loads(requests.read_text())
                self.assertEqual(row["kind"], "browse")
                self.assertEqual(row["instruction"], "list posts")
                self.assertEqual(row["startUrl"], "https://www.tiktok.com/@creator")
                self.assertEqual(row["limit"], 25)

    def test_queue_browse_rejects_blank_instruction(self):
        with self.assertRaises(ValueError):
            host.queue_browse("b1", "   ")

    def test_browse_queues_and_exits_when_uncached(self):
        with tempfile.TemporaryDirectory() as directory:
            requests = Path(directory) / "requests.jsonl"
            results = Path(directory) / "results.jsonl"
            with patch.object(host, "HOST_REQUESTS_PATH", str(requests)), patch.object(
                host, "HOST_RESULTS_PATH", str(results)
            ), patch.object(host, "_pending_requests", []):
                with self.assertRaises(SystemExit):
                    host.browse("scroll the profile")
                row = json.loads(requests.read_text())
                self.assertEqual(row["kind"], "browse")
                self.assertTrue(row["id"].startswith("browse:"))

    def test_browse_returns_cached_guest_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results.jsonl"
            payload = {
                "id": "browse:cached",
                "ok": True,
                "data": {
                    "urls": ["https://www.tiktok.com/@creator/video/1"],
                    "hasMore": False,
                    "exhausted": True,
                    "session": {"profileId": "user_abc", "proxy": "residential"},
                },
            }
            results.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with patch.object(host, "HOST_RESULTS_PATH", str(results)), patch.object(
                host, "HOST_REQUESTS_PATH", str(Path(directory) / "requests.jsonl")
            ), patch.object(host, "_pending_requests", []):
                out = host.browse(
                    "list posts",
                    request_id="browse:cached",
                )
                self.assertEqual(out["session"]["profileId"], "user_abc")
                self.assertNotIn("cdpUrl", json.dumps(out))
