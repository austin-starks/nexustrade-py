"""dynamic_rebalance allow_shorts — backtest-only short legs."""

from __future__ import annotations

import unittest

from nexustrade.portfolio import Value, dynamic_rebalance


def _base(**overrides):
    return {
        "universe": {"source": "SP500"},
        "pipeline": [],
        "weight_indicator": Value(1),
        **overrides,
    }


class DynamicRebalanceShortTests(unittest.TestCase):
    def test_allow_shorts_true_reaches_payload(self) -> None:
        action = dynamic_rebalance(**_base(allow_shorts=True))
        self.assertTrue(action["allowShorts"])

    def test_absent_allow_shorts_compacted(self) -> None:
        action = dynamic_rebalance(**_base())
        self.assertNotIn("allowShorts", action)


if __name__ == "__main__":
    unittest.main()
