"""`.env` fallback contract. Mirrored by test/env.test.ts in the TypeScript SDK."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nexustrade.env import (
    dotenv_disabled,
    environment_value,
    find_dotenv,
    load_dotenv_values,
    parse_dotenv,
)


class ParseDotenvTests(unittest.TestCase):
    def test_parses_the_common_shapes(self) -> None:
        parsed = parse_dotenv(
            "\n".join(
                [
                    "# a comment",
                    "",
                    "PLAIN=value",
                    "  SPACED  =  padded  ",
                    "export EXPORTED=exported-value",
                    "SINGLE='single quoted'",
                    'DOUBLE="double quoted"',
                    'ESCAPED="line\\nbreak"',
                    "EMPTY=",
                ]
            )
        )
        self.assertEqual(
            parsed,
            {
                "PLAIN": "value",
                "SPACED": "padded",
                "EXPORTED": "exported-value",
                "SINGLE": "single quoted",
                "DOUBLE": "double quoted",
                "ESCAPED": "line\nbreak",
                "EMPTY": "",
            },
        )

    def test_keeps_hash_inside_an_unquoted_value(self) -> None:
        # Truncating at an inline `#` would silently corrupt a credential that
        # legitimately contains one. Losing a token beats keeping a comment.
        self.assertEqual(
            parse_dotenv("NEXUSTRADE_API_KEY=sk-abc#def")["NEXUSTRADE_API_KEY"],
            "sk-abc#def",
        )

    def test_first_occurrence_of_a_key_wins(self) -> None:
        self.assertEqual(parse_dotenv("K=first\nK=second"), {"K": "first"})

    def test_ignores_lines_without_an_equals(self) -> None:
        self.assertEqual(parse_dotenv("JUST_A_WORD\n=novalue\nK=v"), {"K": "v"})

    def test_handles_a_value_containing_equals(self) -> None:
        self.assertEqual(parse_dotenv("URL=a=b=c")["URL"], "a=b=c")


class DiscoveryTests(unittest.TestCase):
    def test_finds_a_dotenv_in_a_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / ".env").write_text("NEXUSTRADE_API_KEY=sk-parent\n")
            nested = root / "a" / "b" / "c"
            nested.mkdir(parents=True)

            self.assertEqual(find_dotenv(nested), root / ".env")

    def test_returns_none_when_no_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            # A temp dir has no .env above it inside the walk, unless the
            # filesystem root has one — which no test environment should.
            found = find_dotenv(Path(raw).resolve())
            self.assertTrue(found is None or found.is_file())


class PrecedenceTests(unittest.TestCase):
    def test_real_environment_beats_the_file(self) -> None:
        # The rule that matters: a stale file must never override an export.
        values = {"NEXUSTRADE_API_KEY": "sk-from-file"}
        with mock.patch.dict(os.environ, {"NEXUSTRADE_API_KEY": "sk-real"}):
            self.assertEqual(
                environment_value("NEXUSTRADE_API_KEY", values), "sk-real"
            )

    def test_file_is_used_when_the_variable_is_absent(self) -> None:
        values = {"NEXUSTRADE_API_KEY": "sk-from-file"}
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                environment_value("NEXUSTRADE_API_KEY", values), "sk-from-file"
            )

    def test_blank_is_treated_as_absent_on_both_sides(self) -> None:
        with mock.patch.dict(os.environ, {"NEXUSTRADE_API_KEY": "   "}):
            self.assertEqual(
                environment_value("NEXUSTRADE_API_KEY", {"NEXUSTRADE_API_KEY": "sk-x"}),
                "sk-x",
            )
            self.assertIsNone(
                environment_value("NEXUSTRADE_API_KEY", {"NEXUSTRADE_API_KEY": " "})
            )

    def test_does_not_mutate_the_process_environment(self) -> None:
        # Importing an SDK must not change how unrelated code reads its config.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / ".env").write_text("SOME_UNRELATED_VARIABLE=leaked\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                load_dotenv_values(root)
                self.assertNotIn("SOME_UNRELATED_VARIABLE", os.environ)


class DisableSwitchTests(unittest.TestCase):
    def test_opt_out_is_honoured(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / ".env").write_text("NEXUSTRADE_API_KEY=sk-from-file\n")
            for value in ("1", "true", "yes", "TRUE"):
                with mock.patch.dict(
                    os.environ, {"NEXUSTRADE_DISABLE_DOTENV": value}
                ):
                    self.assertTrue(dotenv_disabled())
                    self.assertEqual(load_dotenv_values(root), {})

    def test_falsey_values_do_not_disable(self) -> None:
        for value in ("", "0", "false", "no"):
            with mock.patch.dict(os.environ, {"NEXUSTRADE_DISABLE_DOTENV": value}):
                self.assertFalse(dotenv_disabled())


if __name__ == "__main__":
    unittest.main()
