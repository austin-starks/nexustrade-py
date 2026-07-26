"""Minimal ``.env`` support, stdlib-only.

The SDK reads process environment variables. That is the correct primary source
— but it surprises people, because writing credentials into a ``.env`` file is
the near-universal local convention, and a client that ignores one fails with
"requires an API key" while the key is sitting right there on disk.

So the client falls back to a ``.env`` file. Deliberately narrow:

* **The real environment always wins.** A ``.env`` value is used only when the
  variable is absent from ``os.environ``. A stale file must never silently
  override what you exported.
* **Nothing is mutated.** ``os.environ`` is left alone, so importing this SDK
  cannot change how unrelated code in the same process reads its own config.
* **Only NexusTrade's own variables are consumed.** The file is parsed in full,
  but the client asks for ``NEXUSTRADE_*`` and nothing else.

Kept byte-for-byte equivalent to ``env.ts`` in the TypeScript SDK; the parser
rules below are the shared contract.
"""

from __future__ import annotations

import os
from pathlib import Path

# A pathological symlink loop or a very deep tree should not turn credential
# resolution into an unbounded walk.
_MAX_PARENTS = 32

_DISABLE_VARIABLE = "NEXUSTRADE_DISABLE_DOTENV"

_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}


def dotenv_disabled() -> bool:
    """True when the caller opted out through the real environment."""
    value = os.environ.get(_DISABLE_VARIABLE, "").strip().lower()
    return value not in ("", "0", "false", "no")


def _unescape(value: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            replacement = _ESCAPES.get(value[index + 1])
            if replacement is not None:
                out.append(replacement)
                index += 2
                continue
        out.append(character)
        index += 1
    return "".join(out)


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``.env`` text.

    Rules, shared with the TypeScript SDK:

    * blank lines and lines whose first non-space character is ``#`` are skipped
    * an optional leading ``export`` is ignored
    * everything before the first ``=`` is the key; the rest is the value
    * a value wrapped in matching single or double quotes is unquoted;
      double-quoted values also resolve ``\\n``, ``\\r``, ``\\t``, ``\\\\``, ``\\"``
    * an unquoted value is taken literally to end of line, after trimming.
      Inline ``#`` comments are NOT stripped — a token may legitimately contain
      ``#``, and silently truncating a credential is worse than keeping a
      trailing comment nobody writes.
    * the FIRST occurrence of a key wins, matching the "never override" stance
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, raw_value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key or key in values:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = _unescape(value)
        values[key] = value
    return values


def find_dotenv(start: Path | None = None) -> Path | None:
    """First ``.env`` at or above ``start`` (default: the current directory)."""
    try:
        current = (start or Path.cwd()).resolve()
    except OSError:
        return None
    for _ in range(_MAX_PARENTS):
        candidate = current / ".env"
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            return None
        if current.parent == current:
            break
        current = current.parent
    return None


def load_dotenv_values(start: Path | None = None) -> dict[str, str]:
    """Values from the nearest ``.env``. Never raises; unreadable means empty."""
    if dotenv_disabled():
        return {}
    path = find_dotenv(start)
    if path is None:
        return {}
    try:
        return parse_dotenv(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        # A malformed or unreadable file must not break a client that was going
        # to be handed an explicit key anyway.
        return {}


def environment_value(name: str, dotenv: dict[str, str] | None = None) -> str | None:
    """``os.environ`` first, then ``.env``. Blank is treated as absent."""
    live = os.environ.get(name)
    if live and live.strip():
        return live
    values = load_dotenv_values() if dotenv is None else dotenv
    fallback = values.get(name)
    return fallback if fallback and fallback.strip() else None


__all__ = [
    "dotenv_disabled",
    "environment_value",
    "find_dotenv",
    "load_dotenv_values",
    "parse_dotenv",
]
