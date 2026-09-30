"""Generated authoring surface for generic option funding."""

import unittest

import nexustrade as nt


class OptionFundingTests(unittest.TestCase):
    def test_equity_reserve_and_weighted_whole_contract_mode_round_trip(self):
        reserve = {"type": "percent of portfolio", "amount": nt.Value(25)}
        shares = nt.dynamic_rebalance(
            universe_config={"source": "ALL_US_STOCKS"}, pipeline=[],
            weight_indicator=nt.Value(1), reserve_option_budget=reserve,
        )
        options = nt.rebalance_option(
            universe_config={"source": "ALL_US_STOCKS"}, pipeline=[],
            weight_indicator=nt.Value(1), structure_templates=[],
            total_budget=reserve, sizing_mode="proportionalToWeightWholeContracts",
        )
        self.assertEqual(shares["reserveOptionBudget"], {"type": "percent of portfolio", "amount": nt.Value(25).to_dict()})
        self.assertEqual(options["sizingMode"], "proportionalToWeightWholeContracts")
        self.assertNotIn("reserveOptionBudget", nt.dynamic_rebalance(
            universe_config={"source": "ALL_US_STOCKS"}, pipeline=[], weight_indicator=nt.Value(1),
        ))


if __name__ == "__main__":
    unittest.main()
