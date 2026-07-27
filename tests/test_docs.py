"""The docs teach an API that must actually exist.

README.md and AGENTS.md are what a human and a coding agent copy from, so a
method named there and missing here is a broken example, not a typo. The two
files use one convention: ``nt`` is the package (builders), ``client`` is a
``NexusTradeClient``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import nexustrade as nt

DOCS = Path(__file__).resolve().parent.parent
CLIENT_CALL = re.compile(r"\bclient\.([a-z_][a-z0-9_]*)\(")
PACKAGE_CALL = re.compile(r"\bnt\.([A-Za-z_][A-Za-z0-9_]*)\(")


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name, None))
    }


def _method_table_names() -> set[str]:
    """Every method named in README.md's Complete method reference table.

    Scoped to that one section on purpose. AGENTS.md tells readers the table is
    exhaustive, so the test has to enforce the table rather than "mentioned
    somewhere" — a passing mention in prose, or a sentence saying a method is
    NOT supported, would otherwise satisfy a gate that promises a reference.
    """
    readme = (DOCS / "README.md").read_text()
    section = re.search(
        r"^##+ Complete method reference\s*$(.*?)(?=^##+ |\Z)",
        readme,
        re.M | re.S,
    )
    if section is None:
        raise AssertionError(
            "README.md has no 'Complete method reference' section — the "
            "completeness gate has nothing to check against."
        )
    table = section.group(1)
    # Rows name a method without calling it: `create_backtest`.
    return set(re.findall(r"`([a-z_][a-z0-9_]{2,})`", table)) | set(
        re.findall(r"\b([a-z_][a-z0-9_]{2,})\(", table)
    )


class DocumentationCompletenessTests(unittest.TestCase):
    """Nothing public may be absent from the docs.

    The 1.0.0 docs omitted 11 of 36 client methods, including the whole
    portfolio lifecycle. None of it was a decision — sections got written and
    the rest drifted. A table fixes it once; this keeps it fixed.
    """

    def test_every_client_method_is_documented(self) -> None:
        documented = _method_table_names()
        missing = sorted(_public_methods(nt.NexusTradeClient) - documented)
        self.assertEqual(
            missing,
            [],
            f"{len(missing)} public NexusTradeClient method(s) are absent from "
            f"README.md's Complete method reference: {', '.join(missing)}. Add "
            "them to that table.",
        )

    def test_every_portfolio_handle_method_is_documented(self) -> None:
        documented = _method_table_names()
        handle_methods = _public_methods(nt.Portfolio) - _public_methods(dict)
        missing = sorted(handle_methods - documented)
        self.assertEqual(
            missing,
            [],
            f"{len(missing)} public Portfolio method(s) are absent from "
            f"README.md's Complete method reference: {', '.join(missing)}.",
        )

    def test_claude_md_points_at_the_complete_reference(self) -> None:
        """CLAUDE.md stays a pointer on purpose.

        Duplicating AGENTS.md into it would create exactly the drift the
        pointer exists to prevent, so the requirement is that it names the
        complete reference rather than restating it.
        """
        claude = (DOCS / "CLAUDE.md").read_text()
        self.assertIn("AGENTS.md", claude)


class DocumentedSymbolTests(unittest.TestCase):
    def test_every_documented_symbol_resolves(self) -> None:
        for name in ("README.md", "AGENTS.md"):
            text = (DOCS / name).read_text()
            with self.subTest(doc=name):
                for method in sorted(set(CLIENT_CALL.findall(text))):
                    self.assertTrue(
                        hasattr(nt.NexusTradeClient, method),
                        f"{name} calls client.{method}(), which does not exist",
                    )
                for symbol in sorted(set(PACKAGE_CALL.findall(text))):
                    self.assertTrue(
                        hasattr(nt, symbol),
                        f"{name} calls nt.{symbol}(), which the package does "
                        "not export",
                    )

    def test_docs_do_not_call_client_methods_on_the_package(self) -> None:
        """`nt` is the package. A client method reached through it is a
        NameError waiting for the reader, and the two spellings look alike."""
        for name in ("README.md", "AGENTS.md"):
            text = (DOCS / name).read_text()
            for symbol in sorted(set(PACKAGE_CALL.findall(text))):
                with self.subTest(doc=name, symbol=symbol):
                    self.assertFalse(
                        hasattr(nt.NexusTradeClient, symbol)
                        and not hasattr(nt, symbol),
                        f"{name} calls nt.{symbol}(), but {symbol} is a client "
                        "method — write client." + f"{symbol}() instead",
                    )


if __name__ == "__main__":
    unittest.main()
