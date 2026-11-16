import unittest
from unittest.mock import patch

import nexustrade as nt
from nexustrade import host

BOOK = {"name": "Book", "strategies": [{"name": "s"}]}
BTC = {"type": "Cryptocurrency", "symbol": "BTC-USD", "name": "BTC-USD"}


class BrokeredBacktestBaselineTests(unittest.TestCase):
    def test_baseline_asset_reaches_the_host_request(self):
        spec = host._normalize_backtest_spec(
            nt.backtest(
                BOOK, start_date="2024-01-01", end_date="2024-12-31", baseline_asset=BTC
            )
        )
        self.assertEqual(spec["baseline_asset"], BTC)
        with patch.object(host, "_pending_requests", []) as pending, patch.object(
            host, "_preflight_portfolio"
        ):
            host.queue_backtest("bt:1", spec)
        self.assertEqual(pending[0]["baselineAsset"], BTC)
        self.assertNotIn("baseline", pending[0])

    def test_baseline_asset_distinguishes_ids_and_leaves_existing_ids_unchanged(self):
        plain = host._normalize_backtest_spec(
            nt.backtest(BOOK, start_date="2024-01-01", end_date="2024-12-31")
        )
        typed = host._normalize_backtest_spec(
            nt.backtest(
                BOOK, start_date="2024-01-01", end_date="2024-12-31", baseline_asset=BTC
            )
        )
        self.assertNotIn("baseline_asset", plain)
        self.assertNotEqual(host._stable_backtest_id(plain), host._stable_backtest_id(typed))
        # An unset baseline_asset must not change the id of a spec without one.
        self.assertEqual(
            host._stable_backtest_id(plain),
            host._stable_backtest_id({**plain, "baseline_asset": None}),
        )


if __name__ == "__main__":
    unittest.main()
