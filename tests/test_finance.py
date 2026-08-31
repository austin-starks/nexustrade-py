from __future__ import annotations

import unittest

import nexustrade as nt


class FinanceSdkTests(unittest.TestCase):
    def test_accounting_bridge(self) -> None:
        operating_nwc = nt.finance.operating_nwc(40.0, 25.0)
        prior_operating_nwc = nt.finance.operating_nwc(34.0, 24.0)
        change = nt.finance.change_in_operating_nwc(
            operating_nwc, prior_operating_nwc
        )
        nopat_value = nt.finance.nopat(100.0, 0.21)

        self.assertEqual(operating_nwc, 15.0)
        self.assertEqual(change, 5.0)
        self.assertEqual(nopat_value, 79.0)
        self.assertEqual(nt.finance.fcff(nopat_value, 20.0, 30.0, change), 64.0)
        invested_capital = nt.finance.invested_capital_from_operations(
            500.0, 180.0, 25.0
        )
        net_investment = nt.finance.net_investment(30.0, 20.0, change)
        self.assertEqual(invested_capital, 345.0)
        self.assertEqual(net_investment, 15.0)
        self.assertAlmostEqual(
            nt.finance.return_on_invested_capital(nopat_value, invested_capital),
            79.0 / 345.0,
        )
        self.assertAlmostEqual(
            nt.finance.incremental_return_on_invested_capital(
                79.0, 70.0, 345.0, 300.0
            ),
            0.2,
        )
        self.assertAlmostEqual(
            nt.finance.reinvestment_rate(net_investment, nopat_value),
            15.0 / 79.0,
        )
        self.assertEqual(
            nt.finance.economic_value_added(nopat_value, invested_capital, 0.1),
            44.5,
        )

    def test_valuation_bridge_uses_one_time_zero_model(self) -> None:
        cost_of_equity = nt.finance.capm_cost_of_equity(0.04, 1.0, 0.05)
        discount_rate = nt.finance.wacc(900.0, 100.0, cost_of_equity, 0.05, 0.2)
        terminal = nt.finance.gordon_growth_terminal_value(120.0, 0.085, 0.025)
        enterprise_value = nt.finance.enterprise_value_from_fcff(
            [90.0, 100.0, 110.0, 120.0], 0.085, terminal
        )
        equity_value = nt.finance.enterprise_to_equity_value(
            enterprise_value, 200.0, 100.0, 25.0
        )

        self.assertAlmostEqual(cost_of_equity, 0.09)
        self.assertAlmostEqual(discount_rate, 0.085)
        self.assertGreater(enterprise_value, 0.0)
        self.assertAlmostEqual(equity_value, enterprise_value + 75.0)
        self.assertAlmostEqual(
            nt.finance.per_share_value(equity_value, 10.0), equity_value / 10.0
        )

    def test_scenario_and_return_math(self) -> None:
        self.assertEqual(
            nt.finance.probability_weighted_value(
                [80.0, 100.0, 140.0], [0.2, 0.5, 0.3]
            ),
            108.0,
        )
        self.assertEqual(nt.finance.margin_of_safety(125.0, 100.0), 0.2)
        self.assertAlmostEqual(
            nt.finance.internal_rate_of_return([-100.0, 0.0, 121.0]), 0.1
        )

    def test_invalid_inputs_fail_instead_of_becoming_zero(self) -> None:
        with self.assertRaisesRegex(ValueError, "tax_rate"):
            nt.finance.nopat(100.0, 1.1)
        with self.assertRaisesRegex(ValueError, "positive capital"):
            nt.finance.wacc(0.0, 0.0, 0.1, 0.05, 0.2)
        with self.assertRaisesRegex(ValueError, "invested_capital must be positive"):
            nt.finance.return_on_invested_capital(10.0, 0.0)
        with self.assertRaisesRegex(ValueError, "change in invested capital"):
            nt.finance.incremental_return_on_invested_capital(
                11.0, 10.0, 100.0, 100.0
            )
        with self.assertRaisesRegex(ValueError, "must exceed"):
            nt.finance.gordon_growth_terminal_value(10.0, 0.03, 0.03)
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            nt.finance.probability_weighted_value([1.0, 2.0], [0.4, 0.5])
        with self.assertRaisesRegex(ValueError, "conventional"):
            nt.finance.internal_rate_of_return([-100.0, 150.0, -60.0])

    def test_operating_period_metrics_reconciles_the_full_bridge(self) -> None:
        result = nt.finance.operating_period_metrics(
            operating_income=100.0,
            tax_rate=0.2,
            depreciation_and_amortization=15.0,
            capital_expenditures=25.0,
            current_operating_nwc=30.0,
            prior_operating_nwc=24.0,
            current_invested_capital=210.0,
            prior_invested_capital=190.0,
            cost_of_capital=0.1,
        )

        self.assertEqual(result["nopat"], 80.0)
        self.assertEqual(result["change_in_operating_nwc"], 6.0)
        self.assertEqual(result["fcff"], 64.0)
        self.assertEqual(result["average_invested_capital"], 200.0)
        self.assertEqual(result["roic"], 0.4)
        self.assertEqual(result["net_investment"], 16.0)
        self.assertEqual(result["reinvestment_rate"], 0.2)
        self.assertEqual(result["eva"], 60.0)

    def test_composed_valuation_and_return_cases_are_reproducible(self) -> None:
        valuation = nt.finance.fcff_valuation_case(
            forecast_fcff=[100.0, 110.0, 120.0],
            discount_rate=0.1,
            perpetual_growth_rate=0.03,
            cash_and_non_operating_assets=50.0,
            debt_and_debt_like_liabilities=20.0,
            diluted_shares=10.0,
            market_price=150.0,
        )
        expected_terminal = nt.finance.gordon_growth_terminal_value(
            120.0, 0.1, 0.03
        )
        expected_enterprise = nt.finance.enterprise_value_from_fcff(
            [100.0, 110.0, 120.0], 0.1, expected_terminal
        )
        self.assertAlmostEqual(valuation["terminal_value"], expected_terminal)
        self.assertAlmostEqual(valuation["enterprise_value"], expected_enterprise)
        self.assertAlmostEqual(
            valuation["equity_value"], expected_enterprise + 50.0 - 20.0
        )
        self.assertAlmostEqual(
            valuation["per_share_value"], valuation["equity_value"] / 10.0
        )
        self.assertAlmostEqual(
            valuation["margin_of_safety"],
            nt.finance.margin_of_safety(valuation["per_share_value"], 150.0),
        )

        returns = nt.finance.equity_return_case(
            entry_price=100.0,
            interim_distributions=[2.0, 2.0, 122.0],
            exit_price=0.0,
            required_return=0.1,
        )
        self.assertGreater(returns["irr"], 0.08)
        self.assertAlmostEqual(
            returns["hurdle_entry_price"],
            nt.finance.present_value_cash_flows([2.0, 2.0, 122.0], 0.1),
        )


if __name__ == "__main__":
    unittest.main()
