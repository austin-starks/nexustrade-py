"""Optional receipts for targeted ``inspect_document`` diagnostics.

Schema-bound extraction no longer consumes these receipts. They remain useful
as ordinary operator-owned evidence when a batch diagnostic leads to targeted
visual inspection.

Receipts are ordinary files under ``{work}/.nexustrade/inspect_receipts`` —
operator-writable diagnostic notes, not host attestations or extraction gates.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_RECEIPT_RELATIVE = Path(".nexustrade") / "inspect_receipts"


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
