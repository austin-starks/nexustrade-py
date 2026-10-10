"""A metered semantic readiness check, separate from report grading."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from uuid import uuid4
from pathlib import Path
from typing import Any

from .host import _gateway_credentials, _touch_host_activity
from .report_evidence import EVIDENCE_FIELD, MAX_CONTEXT_BYTES, staged_research_context
from .report_json import _resolve_pointer, assert_report_envelope, REPORT_INPUTS_MAX_BYTES
from .report_analysis import ANALYSIS_FIELD, construction_checkpoint, record_readiness

MAX_INPUT_BYTES = REPORT_INPUTS_MAX_BYTES
REQUEST_CONTRACT = "report-readiness-typed-construction-v4"
CRITERIA_PATH = "/work/acceptance/acceptance_criteria.json"
MISSING_STATES = frozenset({"not_yet_investigated", "unresolved_evidence", "absent_after_investigation", "analysis_not_performed", "unsupported_assumption", "inconsistent"})


def _read_json(path: Path) -> Any:
    with path.open("rb") as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"Report validator input {path} exceeds {MAX_INPUT_BYTES} bytes; nothing was truncated")
    return json.loads(data)


def _check_result(result: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("status") not in {"ready", "needs_repair"}:
        raise ValueError("Report validator returned no readiness verdict")
    criteria, findings = result.get("criteria"), result.get("findings")
    if not isinstance(criteria, list) or not isinstance(findings, list):
        raise ValueError("Report validator omitted criteria or findings")
    expected = {row["id"] for row in payload["criteria"]}
    ids = [row.get("id") for row in criteria if isinstance(row, dict)]
    if len(ids) != len(criteria) or len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("Report validator did not cover the exact staged criteria")
    gaps = {row["id"] for row in criteria if row.get("status") == "gap"}
    for row in criteria:
        if row.get("status") not in {"met", "gap", "justified_limitation"} or not row.get("reason"):
            raise ValueError("Malformed criterion verdict")
    blocking = set()
    finding_ids = set()
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("criterionId") not in expected:
            raise ValueError("Report validator returned an unknown finding criterion")
        if finding.get("severity") not in {"blocking", "advisory"} or finding.get("missingState") not in MISSING_STATES:
            raise ValueError("Malformed validator finding classification")
        for name in ("id", "problem", "nextAction"):
            if not isinstance(finding.get(name), str) or not finding[name].strip():
                raise ValueError(f"Validator finding omitted {name}")
        if finding["id"] in finding_ids:
            raise ValueError("Duplicate validator finding")
        finding_ids.add(finding["id"])
        pointer = finding.get("inputPath")
        if not isinstance(pointer, str):
            raise ValueError("Validator finding omitted inputPath")
        _resolve_pointer(payload["inputs"], pointer)
        if finding.get("researchQuestion") is not None and not isinstance(finding["researchQuestion"], str):
            raise ValueError("Malformed researchQuestion")
        if finding["severity"] == "blocking":
            blocking.add(finding["criterionId"])
    if gaps != blocking or (result["status"] == "ready") != (not blocking):
        raise ValueError("Validator readiness contradicts its repair findings")
    if not isinstance(result.get("summary"), str) or not result["summary"].strip():
        raise ValueError("Validator omitted summary")
    return result


def validate(*, inputs_path: str, timeout_sec: int = 180) -> dict[str, Any]:
    """Check unfinished required analysis before report authoring.

    The host supplies the original request and the named cloud prompt/model.
    `needs_repair` returns concrete parent tasks and research questions, not a
    grade. Investigators retrieve evidence; the parent repairs its producer and
    assumptions, saves new inputs, and calls again. This helper never rewrites
    the model or executes model-authored SQL. Unchanged inputs reuse the receipt.
    Complete bounded child research selections, source locators and limitations
    are captured separately under _report_readiness_evidence in the request copy.
    Empty or non-inlined content is a transport state, not issuer non-disclosure.
    After an ambiguous transport failure, the same call uses replay-only recovery
    rather than buying another response. All paid calls use the owning run cap.
    """
    work = Path(os.environ.get("NEXUSTRADE_WORK_DIR", "/work"))
    physical = Path(inputs_path)
    if not physical.is_absolute():
        physical = work / physical
    elif work != Path("/work") and physical.is_relative_to("/work"):
        physical = work / physical.relative_to("/work")
    if not physical.resolve().is_relative_to(work.resolve()):
        raise ValueError("inputs_path must identify a JSON artifact inside the current workspace")
    inputs = _read_json(physical)
    # Readiness is a host attestation of these inputs, never part of its own identity.
    if isinstance(inputs, dict) and isinstance(inputs.get(ANALYSIS_FIELD), dict):
        inputs[ANALYSIS_FIELD].pop('readinessReceipt', None)
    criteria_document = _read_json(work / Path(CRITERIA_PATH).relative_to("/work"))
    criteria = [{"id": row["id"], "text": row["text"]}
                for row in criteria_document.get("acceptanceCriteria", []) if row.get("kind") == "required"]
    if not isinstance(inputs, dict) or not criteria:
        raise ValueError("Report validation requires object inputs and staged required method criteria")
    if EVIDENCE_FIELD in inputs:
        raise ValueError(f"{EVIDENCE_FIELD} is reserved for SDK-captured research evidence")
    assert_report_envelope(inputs)
    checkpoint = construction_checkpoint(inputs)
    if checkpoint is not None:
        return checkpoint
    original_payload = {"inputs": {**inputs, EVIDENCE_FIELD: {}}, "criteria": criteria}
    original_bytes = len(json.dumps(original_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    # Replace the empty object using the exact remaining envelope byte budget.
    evidence = staged_research_context(work, max_bytes=min(MAX_CONTEXT_BYTES, MAX_INPUT_BYTES - original_bytes + 2))
    # Stable retained evidence precedes the changing model, permitting provider
    # prefix caching across repairs without omitting either from cache identity.
    payload = {"inputs": {EVIDENCE_FIELD: evidence, **inputs}, "criteria": criteria}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError("Combined validator input exceeds the byte limit; nothing was truncated")
    base, api_key = _gateway_credentials()
    key = hashlib.sha256(REQUEST_CONTRACT.encode() + base.encode() + api_key.encode() + encoded).hexdigest()
    directory = work / ".nexustrade" / "report-readiness"
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / f"{key}.json"
    marker = receipt.with_suffix(".pending")
    if receipt.exists():
        existing = json.loads(receipt.read_text())
        if "result" in existing:
            result = _check_result(existing["result"], payload)
            record_readiness(receipt, result, inputs)
            _save_acceptance(physical, inputs, result)
            return result
    try:
        with marker.open("x"):
            pass
        pending = False
    except FileExistsError:
        pending = True
    _touch_host_activity()
    deadline = time.monotonic() + timeout_sec
    def pending_result() -> dict[str, Any]:
        return {'status':'validation_pending', 'origin':'transport_recovery', 'requestKey':key,
                'acceptance':False, 'additionalPaidCalls':0,
                'nextAction':'Call report.validate with the same inputs to recover the existing request through replay only. Do not buy a replacement review or treat this transport state as an analytical verdict.'}
    def read_response(replay: bool) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Validator response deadline expired; recover this same pending request')
        request = urllib.request.Request(f"{base}/report/validate" + ("/replay" if replay else ""), data=encoded,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=remaining) as response:
            return _check_result(json.loads(response.read().decode("utf-8")), payload)
    try:
        try:
            result = read_response(pending)
        except urllib.error.HTTPError:
            raise
        except (http.client.IncompleteRead, TimeoutError, ConnectionError, urllib.error.URLError):
            if pending:
                return pending_result()
            pending = True
            try:
                result = read_response(True)  # One bounded cache recovery, same payload, no paid retry.
            except urllib.error.HTTPError:
                raise
            except (http.client.IncompleteRead, TimeoutError, ConnectionError, urllib.error.URLError):
                return pending_result()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            kind = json.loads(detail).get('error', {}).get('type')
        except (json.JSONDecodeError, AttributeError):
            kind = None
        if pending and error.code == 409 and kind == 'logical_replay_unavailable':
            return pending_result()
        raise RuntimeError(f"Report validator HTTP {error.code}: {detail}; no ready verdict and no automatic paid retry") from error
    finally:
        _touch_host_activity()
    temporary = receipt.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps({"state": "completed", "inputKey": key, "result": result}) + "\n")
    temporary.replace(receipt)
    marker.unlink(missing_ok=True)
    record_readiness(receipt, result, inputs)
    _save_acceptance(physical, inputs, result)
    return result


def _save_acceptance(path: Path, inputs: dict[str, Any], result: dict[str, Any]) -> None:
    if isinstance(inputs.get(ANALYSIS_FIELD), dict) and result.get('hostReceipt'):
        current = _read_json(path)
        if isinstance(current, dict) and isinstance(current.get(ANALYSIS_FIELD), dict):
            current[ANALYSIS_FIELD].pop('readinessReceipt', None)
        if current != inputs:
            raise ValueError('Report inputs changed during validation; keep the changed artifact and validate its current handoff')
        # Update only SDK-owned transport state; do not patch analytical outputs.
        inputs[ANALYSIS_FIELD]['readinessReceipt'] = result['hostReceipt']
        temporary = path.with_suffix(f'.{uuid4().hex}.tmp')
        temporary.write_text(json.dumps(inputs, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
        temporary.replace(path)
