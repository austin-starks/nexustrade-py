"""Installed-package authoring surface contracts."""

from __future__ import annotations

import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

import nexustrade as nt


class PackageExportTests(unittest.TestCase):
    def test_top_level_portfolio_is_the_builder_not_the_submodule(self) -> None:
        self.assertTrue(callable(nt.portfolio))
        book = nt.portfolio(
            "Momentum",
            [
                nt.strategy(
                    "Rotate",
                    nt.always(),
                    nt.dynamic_rebalance(
                        universe_config=nt.universe("SP500"),
                        pipeline=[
                            nt.filter(
                                nt.Price(nt.CANDIDATE)
                                > nt.SMA(nt.CANDIDATE, 200)
                            ),
                            nt.select_top(
                                nt.RSI(nt.CANDIDATE, 14),
                                10,
                            ),
                        ],
                        weight_indicator=nt.RSI(nt.CANDIDATE, 14),
                        limit=10,
                        deployment_percent=80,
                    ),
                )
            ],
            initial_value=100_000,
        )

        self.assertEqual(book["name"], "Momentum")
        action = book["strategies"][0]["action"]
        self.assertEqual(action["type"], "DynamicRebalance")
        self.assertNotIn(
            "targetAsset",
            action["pipeline"][0]["condition"]["lhs"],
        )

    def test_option_builders_emit_server_wire_names(self) -> None:
        spread = nt.structure_template(
            name="put credit spread",
            spread_type="vertical",
            legs=[
                nt.leg(
                    option_type="put",
                    direction="short",
                    min_days_to_expiration=30,
                    max_days_to_expiration=45,
                    distance=-5,
                ),
                nt.leg(
                    option_type="put",
                    direction="long",
                    min_days_to_expiration=30,
                    max_days_to_expiration=45,
                    distance=-10,
                ),
            ],
        )
        action = nt.rebalance_option(
            universe_config=nt.universe("SP500"),
            pipeline=[],
            weight_indicator=nt.RSI(nt.CANDIDATE, 14),
            structure_templates=[spread],
            total_budget={"type": "percent of portfolio", "amount": 60},
            position_scope="portfolio",
        )

        self.assertEqual(action["structureTemplates"][0]["spreadType"], "vertical")
        self.assertEqual(
            action["structureTemplates"][0]["legs"][0]["expirationSelector"],
            {
                "minDaysToExpiration": 30,
                "maxDaysToExpiration": 45,
                "preference": "nearest",
            },
        )

    def test_adaptive_allocation_builders_work_for_equity_and_options(self) -> None:
        policy = nt.mean_variance_allocation(
            lookback_periods=126,
            minimum_observations=40,
            risk_aversion=6,
        )
        equity = nt.dynamic_rebalance(
            universe_config=nt.universe("SP500"),
            pipeline=[],
            weight_indicator=nt.Value(1),
            allocation_policy=policy,
        )
        options = nt.rebalance_option(
            universe_config=nt.universe("SP500"),
            pipeline=[],
            weight_indicator=nt.Value(1),
            structure_templates=[],
            allocation_policy=policy,
        )

        self.assertEqual(equity["allocationPolicy"], policy)
        self.assertEqual(options["allocationPolicy"], policy)
        self.assertEqual(
            nt.rebalance_expected_benefit().to_dict()["metric"],
            "expectedBenefit",
        )
        self.assertEqual(
            nt.rebalance_estimated_cost().to_dict()["metric"],
            "estimatedCost",
        )

    def test_allocation_policies_compose_with_volatility_targeting(self) -> None:
        exposure = nt.volatility_target(
            target_annualized_volatility_percent=10,
        )
        policies = [
            nt.mean_variance_allocation(),
            nt.risk_parity_allocation(),
            nt.maximum_diversification_allocation(),
        ]
        for policy in policies:
            action = nt.dynamic_rebalance(
                universe_config=nt.universe("SP500"),
                pipeline=[],
                weight_indicator=nt.Value(1),
                allocation_policy=policy,
                exposure_policy=exposure,
            )
            self.assertEqual(action["allocationPolicy"], policy)
            self.assertEqual(action["exposurePolicy"], exposure)

    def test_wildcard_import_works_without_optional_extras(self) -> None:
        # `import *` resolves every name in __all__ eagerly, so lazy helpers
        # backed by optional modules must NOT be listed there — otherwise a
        # plain install without the extras raises on `from nexustrade import *`.
        eager_unsafe_names = {
            name
            for name, (module, _attribute) in nt._LAZY_EXPORTS.items()
            if module not in nt._PUBLIC_BASE_LAZY_MODULES
        }
        self.assertEqual(eager_unsafe_names.intersection(nt.__all__), set())

        namespace: dict[str, object] = {}
        exec("from nexustrade import *", namespace)
        self.assertIn("NexusTradeClient", namespace)
        self.assertIn("portfolio", namespace)
        self.assertIn("extract_pdfs", namespace)
        self.assertIn("write_rows", namespace)

    def test_lazy_helpers_resolve_by_attribute(self) -> None:
        # How compute code reaches them: nt.read_ohlc / nt.lake, not import *.
        lazy_names = sorted(nt._LAZY_EXPORTS)
        fake_module = SimpleNamespace(
            **{
                attribute: f"resolved:{attribute}"
                for _, attribute in nt._LAZY_EXPORTS.values()
                if attribute is not None
            }
        )
        self.addCleanup(
            lambda: [nt.__dict__.pop(name, None) for name in lazy_names],
        )
        with mock.patch.object(nt, "import_module", return_value=fake_module):
            for name in lazy_names:
                self.assertIsNotNone(getattr(nt, name))
        self.assertTrue(set(lazy_names).issubset(dir(nt)))

    def test_compute_helpers_are_public_and_unknown_names_still_fail(self) -> None:
        self.assertTrue(callable(nt.search))
        self.assertTrue(callable(nt.audit_inclusions))
        self.assertTrue(callable(nt.verify_semantic_citations))
        self.assertTrue(callable(nt.extract_pdfs))
        self.assertTrue(callable(nt.extract_web_pages))
        self.assertTrue(callable(nt.write_rows))
        self.assertTrue(callable(nt.sec.statement))
        self.assertTrue(callable(nt.sec.fact_candidates))
        with self.assertRaises(AttributeError) as unknown:
            nt.definitely_not_a_real_export
        self.assertIn("has no attribute", str(unknown.exception))

    def test_lake_resolves_without_its_extra_installed(self) -> None:
        # nexustrade.lake imports only stdlib + client at module level; duckdb
        # and pyarrow are imported inside the methods that need them. So the
        # module resolves on a base install and only heavy CALLS need [lake].
        self.assertTrue(hasattr(nt.lake, "sql"))

    def test_missing_extra_names_the_extra_to_install(self) -> None:
        # Each optional module names ITS extra, not a hardcoded one.
        #
        # The failure is SIMULATED rather than read off the environment:
        # `make test-sdk-python-stats` installs numpy, so asserting on a real
        # import error passed only in the suite that never had the extra —
        # and failed the one release gate that does.
        for name, extra in [
            ("spec_curve", "stats"),
            ("lake", "lake"),
            ("extract_pdfs", "documents"),
        ]:
            # __getattr__ caches into globals(), so a name another test already
            # resolved would never reach the branch under test.
            nt.__dict__.pop(name, None)
            self.addCleanup(lambda cached=name: nt.__dict__.pop(cached, None))
            with mock.patch.object(
                nt,
                "import_module",
                side_effect=ImportError("No module named 'absent'"),
            ):
                with self.assertRaises(AttributeError) as raised:
                    getattr(nt, name)
            self.assertIn(f"nexustrade[{extra}]", str(raised.exception))

    def test_stats_submodule_is_reachable_as_an_attribute(self) -> None:
        # nt.lake resolved but nt.stats raised a bare "no attribute" because
        # only its individual FUNCTIONS were mapped. Both are submodules.
        self.assertEqual(nt._LAZY_EXPORTS["stats"], ("nexustrade.stats", None))

    def test_public_distribution_owns_agent_facing_compute_modules(self) -> None:
        for name in (
            "host",
            "inspect_document",
            "report",
            "scanned_table",
            "sec",
            "semantic",
            "signal",
            "tigris",
        ):
            with self.subTest(module=name):
                self.assertIsNotNone(importlib.util.find_spec(f"nexustrade.{name}"))
        self.assertIsNone(importlib.util.find_spec("nexustrade.fetch_executor"))


if __name__ == "__main__":
    unittest.main()
