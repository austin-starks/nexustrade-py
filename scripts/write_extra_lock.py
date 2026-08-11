"""Rewrite a `uv pip compile` output as one SDK extra's stable lock.

Strips uv's generated-by preamble and normalizes the `# via -r <input>`
provenance comments, both of which embed the caller's temp path and would
otherwise churn the diff on every regeneration. Prepends the header explaining
what the hashes are for. Invoked by `make lock-sdk-<extra>` targets.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# uv records the input file it resolved from; that path is an implementation
# detail of the Makefile target, not something the lock should depend on.
_INPUT_REF = re.compile(r"-r \S+\.in\b")


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: write_extra_lock.py <extra> <uv-compile-output>",
            file=sys.stderr,
        )
        return 2

    extra = sys.argv[1]
    if not re.fullmatch(r"[a-z][a-z0-9-]*", extra):
        print(f"invalid extra name: {extra!r}", file=sys.stderr)
        return 2
    target = Path(__file__).resolve().parent.parent / f"requirements-{extra}.lock"
    input_label = f"pyproject.toml [{extra}]"
    header = f"""\
# Hash-verified lock for the `[{extra}]` extra — the actual supply-chain control.
#
# Exact `==` pins in pyproject.toml say WHICH version; only these sha256 hashes
# say which BYTES. A compromised or re-uploaded artifact at the same version
# fails the install here instead of executing.
#
# Covers every wheel/sdist for every supported platform and Python 3.10-3.13,
# and pins the transitive deps the extra itself does not name.
#
#   pip install --require-hashes -r requirements-{extra}.lock
#
# Regenerate after changing the pins in pyproject.toml:
#   make lock-sdk-{extra}
"""

    lines = Path(sys.argv[2]).read_text().splitlines()
    body = next(
        (index for index, line in enumerate(lines) if not line.startswith("#")),
        None,
    )
    if body is None:
        print("compile output has no requirements", file=sys.stderr)
        return 1

    pinned = _INPUT_REF.sub(input_label, "\n".join(lines[body:]).strip("\n"))
    if "--hash=sha256:" not in pinned:
        print("compile output carries no hashes — refusing to write", file=sys.stderr)
        return 1

    target.write_text(f"{header}{pinned}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
