"""Fail when installed direct dependencies differ from one pyproject extra."""

from __future__ import annotations

import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^;\s]+)$")


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: verify_extra_versions.py <pyproject.toml> <extra>",
            file=sys.stderr,
        )
        return 2
    pyproject = Path(sys.argv[1])
    extra = sys.argv[2]
    project = tomllib.loads(pyproject.read_text())["project"]
    requirements = project["optional-dependencies"].get(extra)
    if not isinstance(requirements, list) or not requirements:
        print(f"unknown or empty extra: {extra!r}", file=sys.stderr)
        return 2
    mismatches: list[str] = []
    for requirement in requirements:
        match = _PIN.fullmatch(requirement)
        if match is None:
            mismatches.append(f"{requirement!r} is not an exact pin")
            continue
        package, expected = match.groups()
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{package} is not installed (expected {expected})")
            continue
        if installed != expected:
            mismatches.append(f"{package}=={installed} (expected {expected})")
    if mismatches:
        print("extra version mismatch:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"- {mismatch}", file=sys.stderr)
        return 1
    print(f"installed [{extra}] versions match {pyproject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
