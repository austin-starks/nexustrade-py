"""A metered semantic readiness check, separate from report grading."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from uuid import uuid4
from pathlib import Path
from typing import Any

from .host import _gateway_credentials, _touch_host_activity

MAX_INPUT_BYTES = 4 * 1024 * 1024
REQUEST_CONTRACT = "report-readiness-native-schema-exact-model-v1"
CRITERIA_PATH = "/work/acceptance/acceptance_criteria.json"
MISSING_STATES = frozenset({"not_yet_investigated", "unresolved_evidence", "absent_after_investigation", "analysis_not_performed", "unsupported_assumption", "inconsistent"})


def _read_json(path: Path) -> Any:
    with path.open("rb") as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"Report validator input {path} exceeds {MAX_INPUT_BYTES} bytes; nothing was truncated")
    return json.loads(data)


def _resolve_pointer(root: Any, pointer: str) -> Any:
    if pointer == "":
        return root
    if not pointer.startswith("/"):
        raise ValueError("Validator inputPath must be an RFC 6901 pointer")
    value = root
    for component in pointer[1:].split("/"):
        # Validate the machine pointer format; no semantic keyword matching.
        index = 0
        while index < len(component):
            if component[index] == "~":
                if index + 1 >= len(component) or component[index + 1] not in "01":
                    raise ValueError("Invalid validator pointer escape")
                index += 1
            index += 1
        key = component.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isascii() or not key.isdecimal() or str(int(key)) != key:
                raise ValueError("Invalid validator array pointer")
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError("Validator pointer does not resolve")
    return value


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
    criteria_document = _read_json(work / Path(CRITERIA_PATH).relative_to("/work"))
    criteria = [{"id": row["id"], "text": row["text"]}
                for row in criteria_document.get("acceptanceCriteria", []) if row.get("kind") == "required"]
    if not isinstance(inputs, dict) or not criteria:
        raise ValueError("Report validation requires object inputs and staged required method criteria")
    payload = {"inputs": inputs, "criteria": criteria}
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
            return _check_result(existing["result"], payload)
    try:
        with marker.open("x"):
            pass
        pending = False
    except FileExistsError:
        pending = True
    _touch_host_activity()
    request = urllib.request.Request(
        f"{base}/report/validate" + ("/replay" if pending else ""),
        data=encoded,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            result = _check_result(json.loads(response.read().decode("utf-8")), payload)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Report validator HTTP {error.code}: {detail}; no ready verdict and no automatic paid retry") from error
    finally:
        _touch_host_activity()
    temporary = receipt.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps({"state": "completed", "inputKey": key, "result": result}) + "\n")
    temporary.replace(receipt)
    marker.unlink(missing_ok=True)
    return result
