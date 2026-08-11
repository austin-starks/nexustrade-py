"""Validate and write signal-shaped rows; read your own indicators."""

from __future__ import annotations

import json
import os
import re
import tempfile
import warnings
from typing import Any, Iterable

from . import host

DEFAULT_OUTPUT_PATH = "/work/out/rows.jsonl"
DEFAULT_REJECTED_OUTPUT_PATH = "/work/out/rejected.jsonl"
LEGACY_OUTPUT_PATH = "/work/output.jsonl"
LEGACY_REJECTED_OUTPUT_PATH = "/work/output_rejected.jsonl"
DEFAULT_LINEAGE_PATH = "/work/lineage.jsonl"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The two ways the host resolves a row to its source document: a direct source id,
# or a row identity it can join through /work/lineage.jsonl. Mirrors
# hostDatasetReconciliation.resolvedRowSourceIds.
_ROW_IDENTITY_FIELDS = ("output_row_id", "row_id")
# Fallback names the host still resolves when source_id/source_ids are absent.
_LEGACY_DIRECT_SOURCE_FIELDS = ("sourceId", "filing_id", "filingId")


def read_rows(
    indicator_id: str,
    request_id: str | None = None,
    _exit: bool = True,
) -> list[dict[str, Any]] | None:
    """
    Read one of YOUR OWN CustomIndicators' accepted points, via the host broker.

    Indicator data is tenant-private (it is NOT in the market lake), so the host
    resolves the id, verifies ownership, and returns the rows.

    Re-run-safe contract (same as host.fetch): when the result is not cached yet,
    this queues the read, flushes, and exits so the host fulfills the request and
    re-runs your script from the top. On the re-run the rows are returned directly.

      - First call queues, flushes, and raises SystemExit(0) when _exit=True (default).
      - On the re-run this returns the rows: [{"timestamp": "YYYY-MM-DD",
        "value": float, "ticker": str|absent}, ...] sorted by timestamp.

        rows = signal.read_rows("6a5323cd842a9fcdeb9a3e78")
        df = pd.DataFrame(rows)
    """
    rid = request_id or f"indicator:{indicator_id}"
    result = host.read_result(rid)
    if result is None:
        host.queue_read_indicator(rid, indicator_id)
        host.flush_requests()
        if _exit:
            raise SystemExit(0)
        return None
    if not result.get("ok"):
        raise RuntimeError(
            f"read_indicator({indicator_id}) failed: {result.get('error')}"
        )
    data = result.get("data") or {}
    rows = data.get("rows")
    return rows if isinstance(rows, list) else []


def validate_row(row: dict[str, Any]) -> None:
    ts = row.get("timestamp")
    if not isinstance(ts, str) or not ts or not _DATE_RE.match(ts):
        raise ValueError(f"invalid timestamp (expected YYYY-MM-DD): {ts!r}")
    value = row.get("value")
    if value is None or isinstance(value, bool):
        raise ValueError(f"invalid value (expected number): {value!r}")
    if not isinstance(value, (int, float)):
        raise ValueError(f"invalid value (expected number): {value!r}")
    if isinstance(value, float) and (
        value != value or value in (float("inf"), float("-inf"))
    ):
        raise ValueError(f"invalid value (expected finite number): {value!r}")
    ticker = row.get("ticker")
    if ticker is not None and (not isinstance(ticker, str) or not ticker.strip()):
        raise ValueError(f"invalid ticker: {ticker!r}")


def _fetched_document_ids(results_path: str | None = None) -> list[str]:
    """Host fetch ids that returned a PDF — the host's own document denominator.

    Read straight off the ledger rather than through host.read_results(), which
    hydrates spilled Tigris payloads; deciding whether a contract applies must not
    pull bytes over the network.
    """
    path = results_path or host.HOST_RESULTS_PATH
    if not os.path.exists(path):
        return []
    ids: list[str] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # parseHostResults THROWS on a bad line and the host swallows it
                # with inputIds=[], disarming the contract for the whole run.
                # Skipping the line instead would arm a check the host does not.
                return []
            if not isinstance(row, dict) or isinstance(row, list):
                return []
            request_id = row.get("id")
            if not isinstance(request_id, str):
                return []
            if not row.get("ok"):
                continue
            data = row.get("data")
            if not isinstance(data, dict):
                continue
            object_key = data.get("objectKey")
            if not isinstance(object_key, str) or not object_key.strip():
                continue
            mime = (
                str(data.get("contentType") or "application/pdf")
                .split(";")[0]
                .strip()
                .lower()
            )
            if mime == "application/pdf" or object_key.lower().endswith(".pdf"):
                ids.append(request_id)
    return ids


def _source_ids_from_value(value: Any) -> list[str]:
    """Mirror of hostDatasetReconciliation.sourceIdsFromValue.

    Numbers count: a filing id is routinely emitted as an int, and rejecting
    those here would block rows the host accepts. Bool is excluded because
    `isinstance(True, int)` is a Python-only quirk — in TS it is not a number.
    """
    if isinstance(value, bool):
        return []
    if isinstance(value, (str, int, float)):
        identifier = str(value).strip()
        return [identifier] if identifier else []
    if isinstance(value, (list, tuple)):
        return [
            identifier
            for item in value
            for identifier in _source_ids_from_value(item)
        ]
    return []


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _direct_source_ids(row: dict[str, Any]) -> list[str]:
    """Mirror of hostDatasetReconciliation.rowSourceIds, legacy fallback included.

    The legacy names are a fallback the host still honours, so a row keyed on
    `filing_id` reconciles there. Omitting them here would fail a run the host
    would have passed — the one thing this guard must never do.
    """
    canonical = _source_ids_from_value(row.get("source_id")) + _source_ids_from_value(
        row.get("source_ids")
    )
    if canonical:
        return _dedupe(canonical)
    for key in _LEGACY_DIRECT_SOURCE_FIELDS:
        ids = _source_ids_from_value(row.get(key))
        if ids:
            return _dedupe(ids)
    return []


def _row_identity(row: dict[str, Any]) -> str | None:
    for key in _ROW_IDENTITY_FIELDS:
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (str, int, float)):
            identity = str(value).strip()
            if identity:
                return identity
    return None


def _lineage_output_ids(lineage_path: str | None = None) -> tuple[set[str], bool]:
    """Output row ids declared in lineage.jsonl, plus whether the file is malformed.

    Mirrors hostDatasetReconciliation.lineageSources INCLUDING its duplicate rule:
    a repeated output_row_id voids the entire map, so one duplicate unattributes
    every aggregate row rather than just its own. That is a silent 100% loss on the
    host side; here it is a named error.
    """
    path = lineage_path or DEFAULT_LINEAGE_PATH
    if not os.path.exists(path):
        return set(), False
    ids: set[str] = set()
    malformed = False
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                malformed = True
                continue
            if not isinstance(row, dict):
                malformed = True
                continue
            identity = _row_identity(row)
            if not identity or not _direct_source_ids(row):
                continue
            if identity in ids:
                malformed = True
                continue
            ids.add(identity)
    return ids, malformed


def _has_provenance(
    row: dict[str, Any], lineage_ids: set[str], lineage_malformed: bool
) -> bool:
    """True when the host could attribute this row to a source document.

    A bare row_id is NOT provenance. The host resolves it only through
    lineage.jsonl, so an unjoined row_id reconciles against nothing — which is
    exactly how a run emits rows that look identified and grade as unattributed.
    """
    if _direct_source_ids(row):
        return True
    if lineage_malformed:
        return False
    identity = _row_identity(row)
    return bool(identity and identity in lineage_ids)


def assert_document_provenance(
    rows: list[dict[str, Any]],
    lineage_path: str | None = None,
    results_path: str | None = None,
) -> None:
    """Enforce the host's attribution contract at WRITE time.

    `verifyReconciliationContract` blocks any run that fetched source documents and
    then emitted rows it cannot attribute. That gate only fires at finish, after
    every page is OCR'd and the artifact is assembled, so the run burns a full
    validation cycle to learn about a stamp it could have set here. Same ledger,
    same test, one exec round instead.

    Silent on runs that fetched no documents: a lake, API or computed series has no
    per-document denominator, and demanding one there blocked most of the corpus.
    """
    if not rows:
        return
    document_ids = _fetched_document_ids(results_path)
    if not document_ids:
        return
    lineage_ids, lineage_malformed = _lineage_output_ids(lineage_path)
    missing = sum(
        1 for row in rows if not _has_provenance(row, lineage_ids, lineage_malformed)
    )
    if not missing:
        return
    if lineage_malformed:
        raise ValueError(
            f"{missing} of {len(rows)} row(s) cannot be attributed because "
            f"{lineage_path or DEFAULT_LINEAGE_PATH} is malformed — a duplicate "
            "output_row_id or a non-object line voids the WHOLE lineage map, not "
            "just the offending entry. Emit exactly one lineage object per output "
            "row, then write the rows again."
        )
    raise ValueError(
        f"{missing} of {len(rows)} row(s) lack provenance. This run fetched "
        f"{len(document_ids)} source document(s), so the host blocks every emitted "
        "row it cannot attribute — writing them now defers the identical failure to "
        "validation. Stamp direct rows with source_id: pass source_id= to "
        "scanned_table.extract_pdf or extract_rows, or use extract_pdfs, which "
        "stamps it from the document key. For aggregate rows, write "
        f"{lineage_path or DEFAULT_LINEAGE_PATH} FIRST with one "
        "{output_row_id, source_ids} object per output row — a row_id that no "
        "lineage entry joins is not provenance."
    )


def _write_jsonl_atomic(rows: list[dict[str, Any]], path: str) -> None:
    """Replace one JSONL member atomically after every byte is serializable."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def write_rows(
    rows: Iterable[dict[str, Any]],
    path: str = DEFAULT_OUTPUT_PATH,
    rejected_path: str | None = DEFAULT_REJECTED_OUTPUT_PATH,
    filter_tickers: bool | None = None,
) -> int:
    """
    Write validated signal rows to the canonical bundle member atomically.

    Validation is mechanical: timestamp, finite numeric value, and a non-empty
    string when `ticker` is present. The SDK does not classify arbitrary asset
    identities with a ticker regex. `rejected_path` and `filter_tickers` remain
    accepted for source compatibility, but ticker filtering is no longer
    performed; callers explicitly write unresolved records to rejected JSONL.

    Every row is checked BEFORE anything is written. A raise part-way through the
    loop used to leave a truncated output.jsonl on disk — a partial artifact that
    reads downstream as a complete one — so the batch is materialized and validated
    up front.
    """
    materialized = list(rows)
    for row in materialized:
        validate_row(row)
    assert_document_provenance(materialized)
    if filter_tickers is not None:
        warnings.warn(
            "filter_tickers is deprecated and ignored; write_rows no longer "
            "classifies asset identities with a ticker regex",
            DeprecationWarning,
            stacklevel=2,
        )
    _ = rejected_path
    _write_jsonl_atomic(materialized, path)
    return len(materialized)
