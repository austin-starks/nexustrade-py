from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from nexustrade import host


class HostFetchConcurrencyTest(unittest.TestCase):
    def test_gateway_fetch_concurrency_defaults_to_bounded_fanout(self):
        env = dict(os.environ)
        env.pop("SANDBOX_FETCH_CONCURRENCY", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(host._gateway_fetch_concurrency(), 4)

    def test_gateway_fetch_concurrency_honors_positive_override(self):
        with patch.dict(os.environ, {"SANDBOX_FETCH_CONCURRENCY": "7"}):
            self.assertEqual(host._gateway_fetch_concurrency(), 7)

    def test_gateway_fetch_concurrency_falls_back_on_invalid_override(self):
        with patch.dict(
            os.environ,
            {"SANDBOX_FETCH_CONCURRENCY": "not-a-number"},
        ):
            self.assertEqual(host._gateway_fetch_concurrency(), 4)


if __name__ == "__main__":
    unittest.main()
