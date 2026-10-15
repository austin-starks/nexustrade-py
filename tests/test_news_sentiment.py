import unittest

import nexustrade as nt


class NewsSentimentContractTests(unittest.TestCase):
    def test_median_uses_daily_trading_session_lookback(self) -> None:
        self.assertEqual(
            nt.NewsSentiment(nt.stock_asset("NVDA"), "Median", 5).to_dict(),
            {"type": "NewsSentiment", "targetAsset": nt.stock_asset("NVDA"),
             "measure": "Median", "lookbackDays": 5},
        )

    def test_mean_latest_snapshot_remains_the_default(self) -> None:
        indicator = nt.NewsSentiment(nt.stock_asset("NVDA")).to_dict()
        self.assertEqual(indicator["measure"], "Mean")
        self.assertEqual(indicator["lookbackDays"], 1)
        self.assertNotIn("window", indicator)
