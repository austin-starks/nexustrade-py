"""Remaining-lot authoring survives the generated SDK wire boundary."""
import unittest
import nexustrade as nt


class PoliticalRemainingLotTests(unittest.TestCase):
    def test_scope_and_metric(self):
        legacy = nt.PoliticalPurchaseShare("X000001", "Option").to_dict()
        remaining = nt.PoliticalPurchaseShare("X000001", "Option", "Midpoint", "RemainingLots").to_dict()
        self.assertEqual(legacy["purchaseScope"], "AllPurchases")
        self.assertEqual(remaining["purchaseScope"], "RemainingLots")
        metric = nt.PoliticalTrades(nt.CANDIDATE, "", "RemainingBuyAmount", 1,
                                    "Midpoint", "Option", "All", "X000001").to_dict()
        self.assertEqual(metric["metric"], "RemainingBuyAmount")
        self.assertEqual(metric["memberId"], "X000001")


if __name__ == "__main__":
    unittest.main()
