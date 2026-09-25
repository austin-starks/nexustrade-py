from __future__ import annotations

import unittest

import nexustrade as nt


class FinanceSdkTests(unittest.TestCase):
    def test_future_common_return_uses_exit_date_bridge_not_present_dcf_value(self) -> None:
        result = nt.finance.future_common_equity_return_case(
            entry_price=100, entry_date="2027-06-30", exit_date="2029-12-31",
            undiscounted_exit_enterprise_value=1300,
            exit_nonoperating_assets=100,
            exit_debt_and_debt_like_liabilities=200,
            exit_other_senior_claims=0, exit_diluted_shares=10,
            shareholder_distributions=[
                {"date": "2028-12-31", "per_share": 2},
                {"date": "2029-12-31", "per_share": 3},
            ], required_return=0.1,
        )
        self.assertEqual(result["exit_bridge"]["equity_value"], 1200)
        self.assertEqual(result["exit_bridge"]["per_share_value"], 120)
        self.assertEqual(result["cash_flows"], [-100, 2, 123])
        self.assertEqual(result["cash_flow_dates"], ["2028-12-31", "2029-12-31"])
        self.assertAlmostEqual(result["irr"], nt.finance.internal_rate_of_return(
            [-100, 2, 123], valuation_date="2027-06-30",
            cash_flow_dates=["2028-12-31", "2029-12-31"],
        ))
        self.assertAlmostEqual(result["hurdle_entry_price"],
            nt.finance.present_value_cash_flows(
                [2, 123], 0.1, valuation_date="2027-06-30",
                cash_flow_dates=["2028-12-31", "2029-12-31"],
            ))

    def test_future_common_return_rejects_elapsed_or_unfunded_exit(self) -> None:
        base = dict(entry_price=100, entry_date="2027-06-30", exit_date="2029-12-31",
            undiscounted_exit_enterprise_value=1300,
            exit_nonoperating_assets=100, exit_debt_and_debt_like_liabilities=200,
            exit_other_senior_claims=0, exit_diluted_shares=10)
        with self.assertRaises(ValueError):
            nt.finance.future_common_equity_return_case(**base,
                shareholder_distributions=[{"date": "2027-06-30", "per_share": 2}])
        with self.assertRaises(ValueError):
            nt.finance.future_common_equity_return_case(**{**base, "exit_date": "2027-06-30"})
        with self.assertRaises(ValueError):
            nt.finance.future_common_equity_return_case(**{**base,
                "exit_debt_and_debt_like_liabilities": 1500})

    def test_dated_valuation_and_irr_share_the_remaining_cash_flow_timeline(self) -> None:
        # A midyear valuation: only the remaining 30 is future cash, not the
        # full-year 100 that includes 70 already earned before the valuation.
        remainder = nt.finance.forecast_remainder(100, 70)['remaining_forecast']
        dates = ['2027-12-31', '2028-12-31']
        timing = dict(valuation_date='2027-06-30', cash_flow_dates=dates)
        terminal = 500
        expected = 30 / 1.1**(184/365) + 620 / 1.1**(550/365)
        result = nt.finance.fcff_valuation_case(
            forecast_fcff=[remainder, 120], discount_rate=0.1,
            terminal_value=terminal, cash_and_non_operating_assets=40,
            debt_and_debt_like_liabilities=20, diluted_shares=10, **timing,
        )
        self.assertAlmostEqual(result['enterprise_value'], expected)
        self.assertAlmostEqual(result['per_share_value'], (expected + 20)/10)
        self.assertAlmostEqual(nt.finance.internal_rate_of_return(
            [-expected, remainder, 620], **timing), 0.1)
        self.assertNotAlmostEqual(expected, nt.finance.enterprise_value_from_fcff(
            [100, 120], 0.1, terminal))

    def test_dated_cash_flows_reject_elapsed_misaligned_and_ambiguous_dates(self) -> None:
        for timing in (
            dict(valuation_date='2027-06-30'),
            dict(cash_flow_dates=['2027-12-31']),
            dict(valuation_date='2027-06-30', cash_flow_dates=[]),
            dict(valuation_date='2027-06-30', cash_flow_dates=['2027-06-30']),
            dict(valuation_date='2027-06-30', cash_flow_dates=['2026-12-31']),
            dict(valuation_date='2027-06-30', cash_flow_dates=['bad-date']),
        ):
            with self.subTest(timing=timing), self.assertRaises(ValueError):
                nt.finance.present_value_cash_flows([10], 0.1, **timing)
        for dates in (['2028-12-31', '2027-12-31'], ['2027-12-31'] * 2):
            with self.assertRaises(ValueError):
                nt.finance.internal_rate_of_return([-100, 60, 60],
                    valuation_date='2027-06-30', cash_flow_dates=dates)

    def test_composed_forecast_preserves_expenses_and_terminal_timing(self) -> None:
        p = nt.finance.operating_forecast_period(
            operating_income=90, tax_rate=0.2, depreciation_and_amortization=15,
            capital_expenditures=25, current_operating_nwc=14, prior_operating_nwc=10,
            prior_invested_capital=200, cost_of_capital=0.1,
        )
        self.assertEqual(p['nopat'], 72)
        self.assertEqual(p['fcff'], 58)
        self.assertEqual(p['current_invested_capital'], 214)
        self.assertAlmostEqual(p['roic'], 72/207)
        terminal = nt.finance.gordon_growth_terminal_value_from_nopat(72, 0.1, 0.02, 0.2)
        v = nt.finance.fcff_valuation_case(
            forecast_fcff=[p['fcff']], discount_rate=0.1,
            terminal_value=terminal['terminal_value'], cash_and_non_operating_assets=30,
            debt_and_debt_like_liabilities=20, diluted_shares=10,
        )
        expected = ((58 + (72*1.02*(1-0.02/0.2)/(0.1-0.02)))/1.1 + 10)/10
        self.assertAlmostEqual(v['per_share_value'], expected)
        with self.assertRaises(ValueError):
            nt.finance.operating_forecast_period(
                operating_income=90, tax_rate=0.2, depreciation_and_amortization=None,
                capital_expenditures=25, current_operating_nwc=14, prior_operating_nwc=10,
                prior_invested_capital=200, cost_of_capital=0.1,
            )

    def test_terminal_choice_is_unambiguous_and_zero_is_valid(self) -> None:
        inputs = dict(forecast_fcff=[10, 20], discount_rate=0.1,
                      cash_and_non_operating_assets=0, debt_and_debt_like_liabilities=0,
                      diluted_shares=2)
        zero = nt.finance.fcff_valuation_case(**inputs, terminal_value=0)
        self.assertAlmostEqual(zero['per_share_value'], (10/1.1+20/1.1**2)/2)
        for extra in ({}, {'terminal_value': 30, 'perpetual_growth_rate': 0.02}, {'terminal_value': float('nan')}):
            with self.assertRaises(ValueError):
                nt.finance.fcff_valuation_case(**inputs, **extra)

    def test_cash_investment_and_noncash_capital_changes_are_distinct(self) -> None:
        inputs = dict(operating_income=30, tax_rate=0.2, depreciation_and_amortization=10,
                      capital_expenditures=10, current_operating_nwc=5, prior_operating_nwc=5,
                      prior_invested_capital=100, cost_of_capital=0.1)
        cash = nt.finance.operating_forecast_period(**inputs, additional_cash_investment=100)
        noncash = nt.finance.operating_forecast_period(**inputs, noncash_invested_capital_changes=100)
        self.assertEqual(cash['current_invested_capital'], noncash['current_invested_capital'])
        self.assertEqual(cash['net_investment'], 100)
        self.assertEqual(cash['fcff'], -76)
        self.assertEqual(noncash['net_investment'], 0)
        self.assertEqual(noncash['fcff'], 24)
        self.assertEqual(cash['nopat'] - cash['net_investment'], cash['fcff'])

    def test_forecast_remainder_exposes_decline_without_passing_judgment(self) -> None:
        result = nt.finance.forecast_remainder(125, 80, prior_comparable_remainder=90)
        self.assertEqual(result['remaining_forecast'], 45)
        self.assertEqual(result['remaining_growth'], -0.5)
        self.assertEqual(nt.finance.forecast_remainder(30, 50)['remaining_forecast'], -20)
        for prior in (0, -10):
            self.assertIsNone(nt.finance.forecast_remainder(30, 50, prior_comparable_remainder=prior)['remaining_growth'])

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
        self.assertEqual(
            nt.finance.price_discount_to_intrinsic_value(125.0, 100.0), 0.2
        )
        self.assertEqual(
            nt.finance.price_upside_to_intrinsic_value(125.0, 100.0), 0.25
        )
        self.assertEqual(
            nt.finance.price_premium_to_intrinsic_value(125.0, 100.0), 0.25
        )
        self.assertAlmostEqual(
            nt.finance.internal_rate_of_return([-100.0, 0.0, 121.0]), 0.1
        )

    def test_analyst_adjustments_are_explicit_and_reproducible(self) -> None:
        self.assertEqual(
            nt.finance.cash_flow_after_equity_compensation(100.0, 12.0), 88.0
        )
        capitalized = nt.finance.capitalize_operating_expense(
            [60.0, 70.0, 80.0, 90.0], amortization_years=3
        )
        self.assertEqual(capitalized["current_expense"], 90.0)
        self.assertEqual(capitalized["current_amortization"], 70.0)
        self.assertAlmostEqual(
            capitalized["unamortized_asset"], 90.0 + 80.0 * 2 / 3 + 70.0 / 3
        )
        self.assertEqual(capitalized["operating_income_adjustment"], 20.0)

        terminal = nt.finance.gordon_growth_terminal_value_from_nopat(
            final_forecast_nopat=100.0,
            discount_rate=0.09,
            perpetual_growth_rate=0.03,
            return_on_new_invested_capital=0.15,
        )
        self.assertAlmostEqual(terminal["reinvestment_rate"], 0.2)
        self.assertAlmostEqual(terminal["next_period_nopat"], 103.0)
        self.assertAlmostEqual(terminal["terminal_fcff"], 82.4)
        self.assertAlmostEqual(terminal["terminal_value"], 82.4 / 0.06)

    def test_irr_accepts_an_initial_investment_phase(self) -> None:
        for cash_flows, expected in (
            ([-100.0, -10.0, 132.0], 0.1),
            ([-100.0, -10.0, 72.0], -0.2),
            ([-100.0, -10.0, 0.0, 145.2], 0.1),
        ):
            with self.subTest(cash_flows=cash_flows):
                actual = nt.finance.internal_rate_of_return(cash_flows)
                self.assertAlmostEqual(actual, expected)
                self.assertAlmostEqual(
                    sum(value / (1.0 + actual) ** period
                        for period, value in enumerate(cash_flows)),
                    0.0,
                )

    def test_irr_rejects_outflows_after_inflows_start(self) -> None:
        for cash_flows in (
            [-100.0, -10.0, 180.0, -30.0],
            [-100.0, 150.0, 0.0, -60.0, 10.0],
        ):
            with self.subTest(cash_flows=cash_flows):
                with self.assertRaisesRegex(ValueError, "conventional"):
                    nt.finance.internal_rate_of_return(cash_flows)

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
        with self.assertRaisesRegex(ValueError, "stock_based_compensation"):
            nt.finance.cash_flow_after_equity_compensation(100.0, -1.0)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            nt.finance.capitalize_operating_expense([10.0], amortization_years=0)
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            nt.finance.gordon_growth_terminal_value_from_nopat(
                100.0, 0.09, 0.05, 0.04
            )

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

    def test_stub_operating_return_matches_annual_capital_cost_to_stub_period(self) -> None:
        inputs = dict(
            operating_income=50.76987835616439,
            tax_rate=0.16,
            depreciation_and_amortization=10.0,
            capital_expenditures=20.0,
            current_operating_nwc=10.0,
            prior_operating_nwc=10.0,
            current_invested_capital=521.6655606027397,
            prior_invested_capital=498.17,
            cost_of_capital=0.085,
        )
        stub = nt.finance.operating_period_metrics(
            **inputs, period_start="2026-09-10", period_end="2026-12-31"
        )
        self.assertAlmostEqual(stub["period_years"], 113 / 365)
        self.assertAlmostEqual(stub["period_roic"],
                               stub["nopat"] / stub["average_invested_capital"])
        self.assertAlmostEqual(stub["roic"], stub["period_roic"] / (113 / 365))
        self.assertAlmostEqual(stub["eva"], stub["nopat"] -
                               stub["average_invested_capital"] * 0.085 * (113 / 365))
        self.assertGreater(stub["eva"], 0)
        full_leap_year = nt.finance.operating_period_metrics(
            **inputs, period_start="2028-01-01", period_end="2028-12-31"
        )
        self.assertEqual(full_leap_year["period_years"], 1.0)
        with self.assertRaisesRegex(ValueError, "supplied together"):
            nt.finance.operating_period_metrics(**inputs, period_start="2026-09-10")

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


class ObservationSelectionTests(unittest.TestCase):
    """Frozen from the stopped 2026-09-07 run.

    The lake returned a 2026-09-04 close of 338.46 stamped ``2026-09-04
    20:00:00``. A later query filtered ``"date" <= '2026-09-04'``, which compares
    the timestamp against midnight, dropped that row, and the valuation used the
    prior session's 342.48 while asserting no 09-04 bar existed.
    """

    rows = [
        {"date": "2026-09-02 20:00:00", "closingPrice": 337.12},
        {"date": "2026-09-03 20:00:00", "closingPrice": 342.48},
        {"date": "2026-09-04 20:00:00", "closingPrice": 338.46},
    ]

    def select(self, as_of: str, rows=None):
        return nt.finance.observation_as_of(
            self.rows if rows is None else rows,
            as_of=as_of,
            timestamp_field="date",
            value_field="closingPrice",
        )

    def test_an_intraday_stamp_still_belongs_to_its_calendar_date(self) -> None:
        selected = self.select("2026-09-04")
        self.assertEqual(selected["value"], 338.46)
        self.assertEqual(selected["observed_at"], "2026-09-04T20:00:00")
        self.assertEqual(selected["observed_date"], "2026-09-04")
        self.assertTrue(selected["is_as_of_date"])
        self.assertEqual(selected["observations_after_as_of"], 0)

    def test_a_later_observation_is_excluded_not_clamped(self) -> None:
        selected = self.select("2026-09-03")
        self.assertEqual(selected["value"], 342.48)
        self.assertFalse(selected["value"] == 338.46)
        self.assertEqual(selected["observations_after_as_of"], 1)

    def test_a_date_with_no_observation_falls_back_to_the_latest_prior(self) -> None:
        selected = self.select("2026-09-06")
        self.assertEqual(selected["observed_date"], "2026-09-04")
        self.assertFalse(selected["is_as_of_date"])

    def test_the_selected_row_travels_with_the_value(self) -> None:
        selected = self.select("2026-09-04")
        self.assertEqual(selected["row"]["closingPrice"], 338.46)
        selected["row"]["closingPrice"] = 0.0
        self.assertEqual(self.rows[2]["closingPrice"], 338.46)

    def test_an_offset_is_converted_not_discarded(self) -> None:
        # 2026-09-05T01:00+05:00 IS 2026-09-04T20:00Z. Dropping the offset would
        # file it under calendar date 09-05, exclude it, and hand back the prior
        # session's close - the exact defect this helper exists to prevent.
        rows = [
            {"date": "2026-09-03 20:00:00", "closingPrice": 342.48},
            {"date": "2026-09-05T01:00:00+05:00", "closingPrice": 338.46},
        ]
        selected = self.select("2026-09-04", rows=rows)
        self.assertEqual(selected["value"], 338.46)
        self.assertEqual(selected["observed_at"], "2026-09-04T20:00:00")
        self.assertTrue(selected["is_as_of_date"])

    def test_a_z_suffix_and_an_explicit_utc_offset_agree(self) -> None:
        for stamp in ("2026-09-04T20:00:00Z", "2026-09-04T20:00:00+00:00"):
            with self.subTest(stamp=stamp):
                selected = self.select(
                    "2026-09-04", rows=[{"date": stamp, "closingPrice": 338.46}]
                )
                self.assertEqual(selected["observed_at"], "2026-09-04T20:00:00")

    def test_an_aware_datetime_object_is_converted_too(self) -> None:
        from datetime import datetime, timedelta, timezone

        rows = [
            {
                "date": datetime(
                    2026, 9, 5, 1, 0, tzinfo=timezone(timedelta(hours=5))
                ),
                "closingPrice": 338.46,
            }
        ]
        selected = self.select("2026-09-04", rows=rows)
        self.assertEqual(selected["observed_at"], "2026-09-04T20:00:00")

    def test_an_exact_tie_is_broken_by_supplied_order(self) -> None:
        # A corrected row appended after the one it supersedes must win.
        rows = [
            {"date": "2026-09-04 20:00:00", "closingPrice": 1.0},
            {"date": "2026-09-04 20:00:00", "closingPrice": 2.0},
        ]
        self.assertEqual(self.select("2026-09-04", rows=rows)["value"], 2.0)
        self.assertEqual(
            self.select("2026-09-04", rows=list(reversed(rows)))["value"], 1.0
        )

    def test_selection_is_chronological_not_lexicographic(self) -> None:
        rows = [
            {"date": "2026-09-04T20:00:00.500000", "closingPrice": 2.0},
            {"date": "2026-09-04T20:00:00", "closingPrice": 1.0},
        ]
        self.assertEqual(self.select("2026-09-04", rows=rows)["value"], 2.0)

    def test_datetime_and_date_objects_are_accepted(self) -> None:
        from datetime import date, datetime

        rows = [
            {"t": date(2026, 9, 3), "v": 1.0},
            {"t": datetime(2026, 9, 4, 20, 0), "v": 2.0},
        ]
        selected = nt.finance.observation_as_of(
            rows, as_of="2026-09-04", timestamp_field="t", value_field="v"
        )
        self.assertEqual(selected["value"], 2.0)

    def test_empty_and_malformed_inputs_fail_explicitly(self) -> None:
        with self.assertRaises(ValueError):
            self.select("2026-09-01")
        with self.assertRaises(ValueError):
            self.select("2026-09-04", rows=[{"date": "not-a-date", "closingPrice": 1}])
        with self.assertRaises(ValueError):
            self.select("2026-09-04", rows=[{"closingPrice": 1}])
        with self.assertRaises(ValueError):
            self.select("2026-09-04", rows=[{"date": "2026-09-04"}])
        with self.assertRaises(ValueError):
            self.select("09/04/2026")


class ElapsedFractionTests(unittest.TestCase):
    def test_the_as_of_day_is_elapsed(self) -> None:
        # July 1 to September 4 inclusive is 66 days, not 65. The run used 65.
        result = nt.finance.elapsed_period_fraction(
            period_start="2026-07-01", period_end="2026-12-31", as_of="2026-09-04"
        )
        self.assertEqual(result["elapsed_days"], 66)
        self.assertEqual(result["total_days"], 184)
        self.assertEqual(result["remaining_days"], 118)
        self.assertAlmostEqual(result["elapsed_fraction"], 66 / 184)
        self.assertEqual(result["remaining_period_start"], "2026-09-05")

    def test_the_convention_matches_remaining_period_flow(self) -> None:
        flow = dict(as_of="2026-09-04", unit="USD", definition="FCFF")
        full = nt.finance.period_flow(
            100.0, period_start="2026-07-01", period_end="2026-12-31", **flow
        )
        stub = nt.finance.period_flow(
            10.0, period_start="2026-07-01", period_end="2026-09-04", **flow
        )
        remaining = nt.finance.remaining_period_flow(
            full, [stub], valuation_date="2026-09-04"
        )
        fraction = nt.finance.elapsed_period_fraction(
            period_start="2026-07-01", period_end="2026-12-31", as_of="2026-09-04"
        )
        self.assertEqual(
            remaining["period_start"], fraction["remaining_period_start"]
        )

    def test_a_whole_elapsed_period_leaves_no_remainder(self) -> None:
        result = nt.finance.elapsed_period_fraction(
            period_start="2026-07-01", period_end="2026-09-04", as_of="2026-09-04"
        )
        self.assertEqual(result["remaining_days"], 0)
        self.assertIsNone(result["remaining_period_start"])

    def test_an_as_of_outside_the_period_fails(self) -> None:
        for as_of in ("2026-06-30", "2027-01-01"):
            with self.subTest(as_of=as_of), self.assertRaises(ValueError):
                nt.finance.elapsed_period_fraction(
                    period_start="2026-07-01", period_end="2026-12-31", as_of=as_of
                )


class PriceComparisonTests(unittest.TestCase):
    """Frozen from cycle 2 of the stopped run.

    The model stored ``discount -0.5701`` and ``upside -0.3631`` correctly. The
    host report labelled its -57.0% column ``Discount = (IV / market) - 1``, the
    formula for the OTHER number, and the grader then blamed the model.
    """

    def test_each_comparison_ships_with_its_own_denominator(self) -> None:
        comparison = nt.finance.price_comparison(215.5615, 338.46)
        self.assertAlmostEqual(
            comparison["discount_to_intrinsic_value"], -0.5701, places=4
        )
        self.assertAlmostEqual(
            comparison["upside_to_intrinsic_value"], -0.3631, places=4
        )
        self.assertAlmostEqual(
            comparison["premium_to_intrinsic_value"], 0.5701, places=4
        )
        self.assertEqual(
            comparison["definitions"]["discount_to_intrinsic_value"],
            "(intrinsic_value - market_price) / intrinsic_value",
        )
        self.assertEqual(
            comparison["definitions"]["upside_to_intrinsic_value"],
            "(intrinsic_value - market_price) / market_price",
        )
        self.assertNotEqual(
            comparison["discount_to_intrinsic_value"],
            comparison["upside_to_intrinsic_value"],
        )

    def test_a_valuation_case_carries_the_definitions(self) -> None:
        case = nt.finance.fcff_valuation_case(
            forecast_fcff=[10.0, 12.0],
            discount_rate=0.1,
            terminal_value=200.0,
            cash_and_non_operating_assets=5.0,
            debt_and_debt_like_liabilities=3.0,
            diluted_shares=2.0,
            market_price=100.0,
        )
        self.assertEqual(
            case["margin_of_safety"],
            case["price_comparison"]["discount_to_intrinsic_value"],
        )
        self.assertIn("definitions", case["price_comparison"])

    def test_editing_the_returned_definitions_cannot_change_the_module(self) -> None:
        nt.finance.price_comparison(200.0, 100.0)["definitions"].clear()
        self.assertIn(
            "discount_to_intrinsic_value",
            nt.finance.PRICE_COMPARISON_DEFINITIONS,
        )


class HurdleComparisonTests(unittest.TestCase):
    def test_the_sentence_follows_the_numbers(self) -> None:
        # The repaired model: bull IRR 6.59% against an 8.85% WACC. The prose
        # still said only the bull case cleared the hurdle.
        result = nt.finance.hurdle_comparison(
            {"bear": -0.0288, "base": 0.0303, "bull": 0.0659},
            0.0885,
            hurdle_name="WACC",
        )
        self.assertEqual(result["clears"], [])
        self.assertEqual(result["misses"], ["bear", "base", "bull"])
        self.assertFalse(result["any_clears"])
        self.assertAlmostEqual(result["spreads"]["bull"], 0.0659 - 0.0885)

    def test_a_return_exactly_at_the_hurdle_is_a_miss(self) -> None:
        result = nt.finance.hurdle_comparison({"base": 0.0885}, 0.0885)
        self.assertEqual(result["misses"], ["base"])
        self.assertFalse(result["all_clear"])

    def test_empty_or_nonnumeric_cases_fail_explicitly(self) -> None:
        for cases in ({}, {"base": None}, {"base": True}):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                nt.finance.hurdle_comparison(cases, 0.05)

    def test_a_blank_hurdle_name_is_rejected_before_it_names_an_error(self) -> None:
        for name in ("", "   ", None):
            with self.subTest(name=name), self.assertRaises(ValueError) as caught:
                nt.finance.hurdle_comparison({"base": 0.1}, 0.05, hurdle_name=name)
            self.assertIn("hurdle_name", str(caught.exception))
