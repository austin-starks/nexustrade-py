"""Inspect-before-schema receipts for extract_rows / extract_pdfs.

``inspect_document`` and schema-bound extract must not share one
``sandbox_run``. The observation has to be available to the next reasoning
turn before a schema is bound.

Receipts are ordinary files under ``{work}/.nexustrade/inspect_receipts`` —
operator-writable, not a host attestation. Extract refuses unless at least
one receipt's mtime is older than this process. Local SDK tests without a
work tree, and ``NEXUSTRADE_REQUIRE_INSPECT_BEFORE_EXTRACT=0``, skip the gate.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_PROCESS_START_NS = time.time_ns()

_RECEIPT_RELATIVE = Path(".nexustrade") / "inspect_receipts"


class InspectBeforeExtractError(RuntimeError):
    """Schema-bound extract ran before a prior-turn inspect_document receipt."""


def work_dir() -> Path:
    return Path(os.environ.get("NEXUSTRADE_WORK_DIR", "/work"))


def receipt_dir() -> Path:
    return work_dir() / _RECEIPT_RELATIVE


def persist_inspect_receipt(result: dict[str, Any]) -> Path | None:
    """Write an inspect receipt. No-ops when the work directory is absent."""
    root = work_dir()
    if not root.is_dir():
        return None
    dest = receipt_dir()
    dest.mkdir(parents=True, exist_ok=True)
    analysis = result.get("analysis")
    payload = {
        "persisted_at_ns": time.time_ns(),
        "kind": result.get("kind"),
        "pages_inspected": result.get("pages_inspected"),
        "model": result.get("model"),
        "analysis": analysis if isinstance(analysis, dict) else None,
    }
    path = dest / f"{payload['persisted_at_ns']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def require_prior_inspect_receipt() -> None:
    """Refuse schema-bound extract unless inspect completed in a prior process.

    Skipped when the work directory does not exist (local SDK, unit tests) or
    when ``NEXUSTRADE_REQUIRE_INSPECT_BEFORE_EXTRACT`` is ``0``/``false``.
    """
    flag = os.environ.get("NEXUSTRADE_REQUIRE_INSPECT_BEFORE_EXTRACT", "1")
    if flag.strip().lower() in {"0", "false", "no"}:
        return
    root = work_dir()
    if not root.is_dir():
        return
    dest = receipt_dir()
    prior: list[Path] = []
    if dest.is_dir():
        for path in dest.glob("*.json"):
            try:
                if path.stat().st_mtime_ns < _PROCESS_START_NS:
                    prior.append(path)
            except OSError:
                continue
    if prior:
        return
    raise InspectBeforeExtractError(
        "schema-bound extract_rows/extract_pdfs requires inspect_document to "
        "complete in a prior sandbox_run so the observation is available "
        "before the schema is bound. End this run after inspect_document; "
        "bind the schema and extract on the next turn."
    )
