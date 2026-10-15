import unittest

import nexustrade as nt


class NewsSentimentContractTests(unittest.TestCase):
    def test_median_uses_the_native_canonical_window(self) -> None:
        self.assertEqual(
            nt.NewsSentiment(nt.stock_asset("NVDA"), "Median", 5, "Day").to_dict(),
            {"type": "NewsSentiment", "targetAsset": nt.stock_asset("NVDA"),
             "measure": "Median", "window": {"length": 5, "interval": "Day"}},
        )

    def test_mean_latest_snapshot_remains_the_default(self) -> None:
        indicator = nt.NewsSentiment(nt.stock_asset("NVDA")).to_dict()
        self.assertEqual(indicator["measure"], "Mean")
        self.assertEqual(indicator["window"], {"length": 1, "interval": "Day"})
        self.assertNotIn("lookbackDays", indicator)
