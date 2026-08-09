"""Unit tests for nexustrade.nl natural-language screening (no network)."""

from __future__ import annotations

import unittest
from unittest import mock

import nexustrade as nt


def _completed(result: dict[str, object]) -> dict[str, object]:
    return {"id": "ns-1", "status": "completed", "result": result}


def _screen(result: dict[str, object]) -> "nt.nl.NlScreen":
    client = mock.Mock()
    client.create_nl_screen.return_value = {"id": "ns-1", "status": "running"}
    client.wait_for_nl_screen.return_value = _completed(result)
    return nt.nl.screen_stocks("large-cap non-tech", client=client)


class NlScreenFieldTests(unittest.TestCase):
    """The wire envelope is camelCase; the dataclass is snake_case."""

    def test_used_fallback_tables_is_mapped_from_the_wire_field(self) -> None:
        """A dropped degradation signal reads identically to a healthy screen.

        The server has always serialized `usedFallbackTables`; the dataclass
        used to ignore it, so a screen whose table-selector round failed and
        fell back to the whole index was indistinguishable from one that
        narrowed cleanly.
        """
        screen = _screen(
            {
                "outcome": "ROWS",
                "rows": [{"ticker": "AAPL"}],
                "rowCount": 1,
                "tables": ["sec_daily_ohlc"],
                "usedFallbackTables": True,
            }
        )
        self.assertTrue(screen.used_fallback_tables)

    def test_used_fallback_tables_defaults_false_when_absent(self) -> None:
        screen = _screen({"outcome": "ROWS", "rows": [], "rowCount": 0})
        self.assertFalse(screen.used_fallback_tables)

    def test_used_fallback_tables_is_falsy_not_none_on_a_clean_screen(self) -> None:
        """`bool(...)` coercion, so callers can branch without a None check."""
        screen = _screen(
            {
                "outcome": "ROWS",
                "rows": [{"ticker": "AAPL"}],
                "rowCount": 1,
                "usedFallbackTables": False,
            }
        )
        self.assertIs(screen.used_fallback_tables, False)

    def test_snake_case_attributes_carry_the_camel_case_payload(self) -> None:
        screen = _screen(
            {
                "outcome": "ROWS",
                "rows": [{"ticker": "AAPL"}],
                "rowCount": 1,
                "asOfDate": "2026-08-08",
                "catalogVersion": "v7",
                "usedFallbackTables": True,
            }
        )
        self.assertEqual(screen.row_count, 1)
        self.assertEqual(screen.as_of_date, "2026-08-08")
        self.assertEqual(screen.catalog_version, "v7")
        # The camelCase spellings are the wire format only.
        self.assertFalse(hasattr(screen, "usedFallbackTables"))
        self.assertFalse(hasattr(screen, "rowCount"))


class NlScreenOutcomeTests(unittest.TestCase):
    """EMPTY and CLARIFICATION both have no rows and mean different things."""

    def test_empty_is_an_answer_not_a_clarification(self) -> None:
        screen = _screen({"outcome": "EMPTY", "rows": [], "rowCount": 0})
        self.assertTrue(screen.is_empty)
        self.assertFalse(screen.needs_clarification)

    def test_clarification_is_completed_not_raised(self) -> None:
        screen = _screen(
            {"outcome": "CLARIFICATION", "clarification": "Which sector?"}
        )
        self.assertTrue(screen.needs_clarification)
        self.assertFalse(screen.is_empty)
        self.assertEqual(screen.clarification, "Which sector?")

    def test_generation_failure_raises_and_keeps_the_sql(self) -> None:
        client = mock.Mock()
        client.create_nl_screen.return_value = {"id": "ns-1", "status": "running"}
        client.wait_for_nl_screen.return_value = {
            "id": "ns-1",
            "status": "failed",
            "result": {"outcome": "GENERATION_FAILED", "sql": "SELECT bad"},
            "error": {"message": "generation failed"},
        }
        with self.assertRaises(nt.nl.NlScreenFailed) as ctx:
            nt.nl.screen_stocks("bad question", client=client)
        self.assertEqual(ctx.exception.sql, "SELECT bad")


if __name__ == "__main__":
    unittest.main()
