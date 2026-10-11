"""Remote wait progress must be visible without hiding frozen operations."""

import os
import tempfile
import unittest
from unittest.mock import patch

from nexustrade import _activity
from nexustrade.client import NexusTradeApiError, wait_for_operation


class OperationActivityTests(unittest.TestCase):
    def test_only_transitions_report_progress(self) -> None:
        states = iter(["queued", "queued", "running", "running", "completed"])
        with patch("nexustrade.client.touch_activity") as touched:
            result = wait_for_operation(
                lambda job: {"id": job, "status": next(states)}, "lq_1",
                poll_interval_seconds=0,
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(touched.call_count, 3)

    def test_frozen_operation_does_not_refresh_liveness_and_keeps_id(self) -> None:
        with patch("nexustrade.client.touch_activity") as touched, patch(
            "nexustrade.client.time.monotonic", side_effect=[0, 1, 3]
        ):
            with self.assertRaises(NexusTradeApiError) as raised:
                wait_for_operation(
                    lambda job: {"id": job, "status": "running"}, "lq_frozen",
                    timeout_seconds=2, poll_interval_seconds=0,
                )
        self.assertEqual(touched.call_count, 1)
        self.assertEqual(raised.exception.operation_id, "lq_frozen")
        self.assertEqual(raised.exception.code, "operation_timeout")

    def test_terminal_failure_also_records_a_transition(self) -> None:
        states = iter(["running", "failed"])
        with patch("nexustrade.client.touch_activity") as touched:
            with self.assertRaises(NexusTradeApiError):
                wait_for_operation(
                    lambda job: {"id": job, "status": next(states)}, "lq_1",
                    poll_interval_seconds=0,
                )
        self.assertEqual(touched.call_count, 2)

    def test_runner_file_is_touched_without_growing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "activity")
            with patch.dict(os.environ, {"NEXUSTRADE_HOST_ACTIVITY_FILE": target}):
                _activity.touch_activity()
            self.assertEqual(os.path.getsize(target), 0)

    def test_reporting_failure_is_advisory(self) -> None:
        with patch.dict(os.environ, {"NEXUSTRADE_HOST_ACTIVITY_FILE": "/missing/parent/activity"}):
            _activity.touch_activity()

    def test_no_runner_configuration_does_no_filesystem_work(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("builtins.open") as opened:
            _activity.touch_activity()
        opened.assert_not_called()
