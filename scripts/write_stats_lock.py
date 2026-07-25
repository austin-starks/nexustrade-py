"""Rewrite a `uv pip compile` output as requirements-stats.lock.

Strips uv's generated-by preamble and normalizes the `# via -r <input>`
provenance comments, both of which embed the caller's temp path and would
otherwise churn the diff on every regeneration. Prepends the header explaining
what the hashes are for. Invoked by `make lock-sdk-stats`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# uv records the input file it resolved from; that path is an implementation
# detail of the Makefile target, not something the lock should depend on.
_INPUT_REF = re.compile(r"-r \S+\.in\b")
_INPUT_LABEL = "pyproject.toml [stats]"

HEADER = """\
# Hash-verified lock for the `[stats]` extra — the actual supply-chain control.
#
# Exact `==` pins in pyproject.toml say WHICH version; only these sha256 hashes
# say which BYTES. A compromised or re-uploaded artifact at the same version
# fails the install here instead of executing.
#
# Covers every wheel/sdist for every supported platform and Python 3.10-3.13,
# and pins the transitive deps the extra itself does not name.
#
#   pip install --require-hashes -r requirements-stats.lock
#
# Regenerate after changing the pins in pyproject.toml:
#   make lock-sdk-stats
"""

TARGET = Path(__file__).resolve().parent.parent / "requirements-stats.lock"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: write_stats_lock.py <uv-compile-output>", file=sys.stderr)
        return 2

    lines = Path(sys.argv[1]).read_text().splitlines()
    body = next(
        (index for index, line in enumerate(lines) if not line.startswith("#")),
        None,
    )
    if body is None:
        print("compile output has no requirements", file=sys.stderr)
        return 1

    pinned = _INPUT_REF.sub(_INPUT_LABEL, "\n".join(lines[body:]).strip("\n"))
    if "--hash=sha256:" not in pinned:
        print("compile output carries no hashes — refusing to write", file=sys.stderr)
        return 1

    TARGET.write_text(f"{HEADER}{pinned}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
