"""Realized option premium: the generated surface the Python SDK must expose.

``RealizedPremiumBuilderTests`` DEPENDS ON REGENERATION. Those builders are
emitted by ``make generate-nt-sdk`` from the server's OptionRealizedPnL and
OptionRealizedPremium factories, so until the factories land and the SDK is
regenerated every test in that class fails with AttributeError.

``RealizedPremiumAllocationTests`` holds today: the Python SDK passes an option
allocation through as a plain dict and enumerates no allocation types.

The TypeScript twin is sdk/typescript/test/realizedPremium.test.ts.
"""

from __future__ import annotations

import inspect
import json
import unittest

import nexustrade as nt

FILTER = ("IBIT", "put", "short", "custom")


class RealizedPremiumBuilderTests(unittest.TestCase):
    """DEPENDS ON REGENERATION."""

    def test_premium_takes_only_underlying_and_lookback_days(self) -> None:
        # Filtered by underlying only: a balance narrowed to puts would drop the
        # call debits it funds and overstate what is spendable.
        params = inspect.signature(nt.OptionRealizedPremium).parameters
        self.assertEqual(list(params), ["underlying", "lookback_days"])
        self.assertIsNone(params["lookback_days"].default)

    def test_premium_leaves_lookback_days_off_the_wire_when_omitted(self) -> None:
        # Omitted means the whole life of the book; null or 0 would not.
        self.assertEqual(
            nt.OptionRealizedPremium("IBIT").to_dict(),
            {"type": "OptionRealizedPremium", "underlying": "IBIT"},
        )

    def test_premium_sends_lookback_days_when_given(self) -> None:
        self.assertEqual(
            nt.OptionRealizedPremium("IBIT", lookback_days=30).to_dict(),
            {"type": "OptionRealizedPremium", "underlying": "IBIT", "lookbackDays": 30},
        )

    def test_pnl_mirrors_the_unrealized_filter_plus_lookback_days(self) -> None:
        realized = list(inspect.signature(nt.OptionRealizedPnL).parameters)
        unrealized = list(inspect.signature(nt.OptionUnrealizedPnL).parameters)
        self.assertEqual(realized, [*unrealized, "lookback_days"])
        self.assertEqual(
            nt.OptionRealizedPnL(*FILTER, lookback_days=30).to_dict(),
            {
                **nt.OptionUnrealizedPnL(*FILTER).to_dict(),
                "type": "OptionRealizedPnL",
                "lookbackDays": 30,
            },
        )

    def test_pnl_rejects_an_unknown_filter_value_in_process(self) -> None:
        with self.assertRaises(ValueError):
            nt.OptionRealizedPnL("IBIT", "straddle", "short", "custom")

    def test_both_builders_are_package_exports(self) -> None:
        for name in ("OptionRealizedPnL", "OptionRealizedPremium"):
            with self.subTest(name=name):
                self.assertTrue(name in nt.__all__, f"{name} is not in nexustrade.__all__")


class RealizedPremiumAllocationTests(unittest.TestCase):
    def test_open_option_carries_percent_of_realized_premium(self) -> None:
        allocation = {"type": "percent of realized premium", "amount": 50}
        action = nt.open_option(
            builder=nt.options_builder(
                underlying_symbol="IBIT",
                spread_type="custom",
                legs=[
                    nt.leg(
                        option_type="call",
                        direction="long",
                        min_days_to_expiration=30,
                        max_days_to_expiration=60,
                        distance=5,
                        preference="middle",
                    )
                ],
            ),
            allocation=allocation,
        )
        self.assertEqual(json.loads(json.dumps(action))["allocation"], allocation)


if __name__ == "__main__":
    unittest.main()
