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
# Legacy spelling of the canonical host identity. Domain identifiers such as
# filing_id are source facts, not provenance, and must never satisfy this join.
_LEGACY_DIRECT_SOURCE_FIELDS = ("sourceId",)


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


def _host_provenance_ids(
    results_path: str | None = None,
) -> tuple[list[str], set[str]]:
    """Return fetched PDF ids and every successful broker result id.

    Read straight off the ledger rather than through host.read_results(), which
    hydrates spilled Tigris payloads; deciding whether a contract applies must not
    pull bytes over the network. A malformed ledger disarms the early check to
    match the host parser; sandbox_finish remains the authoritative backstop.
    """
    path = results_path or host.HOST_RESULTS_PATH
    if not os.path.exists(path):
        return [], set()
    document_ids: list[str] = []
    successful_ids: set[str] = set()
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
                return [], set()
            if not isinstance(row, dict) or isinstance(row, list):
                return [], set()
            request_id = row.get("id")
            if not isinstance(request_id, str):
                return [], set()
            if not row.get("ok"):
                continue
            successful_ids.add(request_id)
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
                document_ids.append(request_id)
    return document_ids, successful_ids


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
    """Resolve canonical provenance without confusing it with source facts.

    sourceId remains as a compatibility spelling of source_id. filing_id and
    filingId deliberately do not: they identify a publisher record, not the
    exact host fetch receipt that the independent grader can open.
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


def _lineage_sources(
    lineage_path: str | None = None,
) -> tuple[dict[str, list[str]], bool]:
    """Output row to source ids, plus whether lineage is malformed.

    Mirrors hostDatasetReconciliation.lineageSources INCLUDING its duplicate rule:
    a repeated output_row_id voids the entire map, so one duplicate unattributes
    every aggregate row rather than just its own. That is a silent 100% loss on the
    host side; here it is a named error.
    """
    path = lineage_path or DEFAULT_LINEAGE_PATH
    if not os.path.exists(path):
        return {}, False
    sources_by_output_id: dict[str, list[str]] = {}
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
            source_ids = _direct_source_ids(row)
            if not identity or not source_ids:
                continue
            if identity in sources_by_output_id:
                malformed = True
                continue
            sources_by_output_id[identity] = source_ids
    return sources_by_output_id, malformed


def _resolved_source_ids(
    row: dict[str, Any],
    lineage_sources: dict[str, list[str]],
    lineage_malformed: bool,
) -> list[str]:
    """Resolve the exact evidence ids the host will use for this row.

    A bare row_id is NOT provenance. The host resolves it only through
    lineage.jsonl, so an unjoined row_id reconciles against nothing — which is
    exactly how a run emits rows that look identified and grade as unattributed.
    """
    direct = _direct_source_ids(row)
    if direct:
        return direct
    if lineage_malformed:
        return []
    identity = _row_identity(row)
    return lineage_sources.get(identity, []) if identity else []


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
    document_ids, successful_ids = _host_provenance_ids(results_path)
    if not document_ids:
        return
    lineage_sources, lineage_malformed = _lineage_sources(lineage_path)
    resolved = [
        _resolved_source_ids(row, lineage_sources, lineage_malformed) for row in rows
    ]
    missing = sum(1 for source_ids in resolved if not source_ids)
    if lineage_malformed and missing:
        raise ValueError(
            f"{missing} of {len(rows)} row(s) cannot be attributed because "
            f"{lineage_path or DEFAULT_LINEAGE_PATH} is malformed — a duplicate "
            "output_row_id or a non-object line voids the WHOLE lineage map, not "
            "just the offending entry. Emit exactly one lineage object per output "
            "row, then write the rows again."
        )
    if missing:
        raise ValueError(
            f"{missing} of {len(rows)} row(s) lack provenance. This run fetched "
            f"{len(document_ids)} source document(s), so the host blocks every emitted "
            "row it cannot attribute — writing them now defers the identical failure to "
            "validation. Stamp direct rows with source_id: pass source_id= to "
            "scanned_table.extract_pdf or extract_rows, or use extract_pdfs, which "
            "stamps it from the document key. filing_id is a source fact and does not "
            "count as provenance. For aggregate rows, write "
            f"{lineage_path or DEFAULT_LINEAGE_PATH} FIRST with one "
            "{output_row_id, source_ids} object per output row — a row_id that no "
            "lineage entry joins is not provenance."
        )

    # Lake query ids are independently minted and verified by the host from its
    # durable query ledger, which is not exposed in host_results.jsonl. Everything
    # else in a document-derived run must be an exact successful broker result id.
    unknown = sorted(
        {
            source_id
            for source_ids in [*resolved, *lineage_sources.values()]
            for source_id in source_ids
            if source_id not in successful_ids
            and not source_id.startswith("lake-query:")
        }
    )
    if unknown:
        preview = ", ".join(unknown[:8])
        suffix = f" (+{len(unknown) - 8} more)" if len(unknown) > 8 else ""
        raise ValueError(
            f"{len(unknown)} provenance id(s) are not successful host result keys: "
            f"{preview}{suffix}. Preserve source_id from host.fetch receipts through "
            "scanned_table.extract_pdfs; do not replace it with filing_id, a URL, or "
            "another publisher identifier."
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
