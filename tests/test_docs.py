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
