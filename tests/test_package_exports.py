"""Installed-package authoring surface contracts."""

from __future__ import annotations

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

    def test_wildcard_import_works_without_optional_extras(self) -> None:
        # `import *` resolves every name in __all__ eagerly, so lazy helpers
        # (lake / stats / tigris) must NOT be listed there — otherwise a plain
        # install without the extras raises on `from nexustrade import *`.
        self.assertEqual(set(nt._LAZY_EXPORTS).intersection(nt.__all__), set())

        namespace: dict[str, object] = {}
        exec("from nexustrade import *", namespace)
        self.assertIn("NexusTradeClient", namespace)
        self.assertIn("portfolio", namespace)

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

    def test_sandbox_only_names_explain_themselves_when_absent(self) -> None:
        # Published layout: the overlay is not installed, so these must say so
        # rather than pointing at an extra that would not help.
        with self.assertRaises(AttributeError) as raised:
            nt.search
        self.assertIn(
            "only inside the NexusTrade compute sandbox", str(raised.exception)
        )
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
        for name, extra in [("spec_curve", "stats"), ("lake", "lake")]:
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


if __name__ == "__main__":
    unittest.main()
