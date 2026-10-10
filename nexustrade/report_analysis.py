"""Model-authored construction plan and one reproducible producer.

No accounting definitions, financial assumptions, source facts or formula choices
are inferred here. The executor declares dependencies; current byte receipts prove
execution/serialization, not economic correctness. Semantic review remains separate.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
import sys
from uuid import uuid4
from pathlib import Path
from typing import Any, Mapping, Sequence
from collections import deque

from .report_json import _resolve_pointer, assert_report_envelope, REPORT_INPUTS_MAX_BYTES
from .analysis_outputs import validate_output_requirements, missing_required_outputs, required_result_binding_findings

PLAN_PATH = "acceptance/analysis_components.json"
EXECUTION_PATH = ".nexustrade/analysis/execution.json"
ANALYSIS_FIELD = "analysisWork"
_COMPONENT_KINDS = ("calculation", "research", "delivery")
_DECISION_KINDS = ("reported_fact", "accounting_definition", "future_policy", "implementation")
_DECISION_STATES = ("resolved", "unresolved")


def _root() -> Path:
    return Path(os.environ.get("NEXUSTRADE_WORK_DIR", "/work")).resolve()


def _path(value: str) -> Path:
    path = Path(value)
    root = _root()
    if path.is_relative_to("/work"):
        path = root / path.relative_to("/work")
    elif not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("Analysis artifacts must stay inside the current workspace")
    if path.name in {".jobenv", ".session_env"} or path == root / ".env":
        raise ValueError("Analysis artifacts cannot read host-private credential files")
    return path


def _logical(path: Path) -> str:
    return "/work/" + path.resolve().relative_to(_root()).as_posix()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def _report_digest(inputs: Mapping[str, Any]) -> str:
    # Completion state and receipt are transport metadata; report edits can be
    # legitimate repairs even when the underlying numerical model is unchanged.
    value = {key: child for key, child in inputs.items() if key != ANALYSIS_FIELD}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _required() -> set[str]:
    return {row['id'] for row in _required_criteria()}


def _required_criteria() -> list[dict[str, Any]]:
    document = json.loads((_root() / "acceptance/acceptance_criteria.json").read_text())
    return [row for row in document['acceptanceCriteria'] if row.get('kind') == 'required']


def analysis_requirements() -> list[dict[str, Any]]:
    """Read the host-owned method results before declaring a producer's bindings.

    Each criterion includes its id/text and, for newly interpreted methods,
    results with id, description, type and minimumCount. This reads the current
    workspace only; it makes no model call or financial decision.
    """
    return _required_criteria()


def _ordered_components(components: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_id = {row["id"]: row for row in components}
    pending = {row["id"]: len(row["dependsOn"]) for row in components}
    dependents: dict[str, list[str]] = {row["id"]: [] for row in components}
    for row in components:
        for dependency in row["dependsOn"]:
            dependents[dependency].append(row["id"])
    ready = deque(key for key, count in pending.items() if count == 0)
    ordered = []
    while ready:
        key = ready.popleft()
        ordered.append(by_id[key])
        for dependent in dependents[key]:
            pending[dependent] -= 1
            if pending[dependent] == 0:
                ready.append(dependent)
    if len(ordered) != len(components):
        raise ValueError("Analysis component dependency cycle")
    return ordered


def _validate(plan: Mapping[str, Any]) -> None:
    required = _required()
    components = plan.get("components")
    if plan.get("version") not in {1, 2} or not isinstance(components, list) or not components:
        raise ValueError("Analysis plan needs version 2 and nonempty components")
    ids = [row.get("id") for row in components if isinstance(row, dict)]
    if len(ids) != len(components) or any(not isinstance(x, str) or not x.strip() for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Analysis component IDs must be unique nonempty strings")
    declared_ids = set(ids)
    covered: set[str] = set()
    for component_index, row in enumerate(components):
        location = f"components[{component_index}] ({row['id']!r})"
        if row.get("kind") not in _COMPONENT_KINDS:
            raise ValueError(f"Component kind must be calculation, research or delivery; {location}.kind received {row.get('kind')!r}, allowed={_COMPONENT_KINDS!r}")
        criteria = row.get("criterionIds")
        if not isinstance(criteria, list) or not criteria or not set(criteria).issubset(required):
            raise ValueError(f"Components must name actual required criterion IDs; {location}.criterionIds received {criteria!r}, allowed={sorted(required)!r}. Read report.analysis_requirements().")
        covered.update(criteria)
        if plan['version'] == 2:
            try:
                validate_output_requirements(row.get('outputRequirements'), criteria)
            except ValueError as error:
                raise ValueError(f"{location}: {error}") from error
        dependencies = row.get("dependsOn")
        if not isinstance(dependencies, list) or len(set(dependencies)) != len(dependencies) or not set(dependencies).issubset(declared_ids) or row["id"] in dependencies:
            raise ValueError("Component dependencies must name other declared components")
        outputs = row.get("outputPaths")
        if not isinstance(outputs, list) or not outputs or any(not isinstance(x, str) or not x.startswith("/") for x in outputs):
            raise ValueError("Each component needs explicit output pointers into the canonical model")
        decisions = row.get("decisions", [])
        for decision_index, decision in enumerate(decisions):
            decision_location = f"{location}.decisions[{decision_index}]"
            if not isinstance(decision, dict):
                raise ValueError(f"Decisions must distinguish facts, definitions, future policies and implementation; {decision_location} must be an object with kind, state, explanation and evidencePaths")
            if decision.get("kind") not in _DECISION_KINDS:
                raise ValueError(f"Decisions must distinguish facts, definitions, future policies and implementation; {decision_location}.kind received {decision.get('kind')!r}, allowed={_DECISION_KINDS!r}")
            if decision.get("state") not in _DECISION_STATES:
                raise ValueError(f"Decisions need explicit state, explanation and inspected evidence paths; {decision_location}.state received {decision.get('state')!r}, allowed={_DECISION_STATES!r}")
            if not decision.get("explanation") or not isinstance(decision.get("evidencePaths"), list):
                raise ValueError(f"Decisions need explicit state, explanation and inspected evidence paths; {decision_location} needs nonempty explanation and an evidencePaths list")
            for path in decision["evidencePaths"]:
                if _path(path) == _path(plan["modelPath"]):
                    raise ValueError("Decision evidence must be frozen inputs, separate from the model output")
    if covered != required:
        raise ValueError(f"Analysis plan omitted required criteria: {sorted(required - covered)}")
    binding_findings = required_result_binding_findings(
        [binding for row in components for binding in row.get('outputRequirements', [])], _required_criteria())
    if binding_findings:
        raise ValueError('Required result construction incomplete: ' + ' '.join(binding_findings))
    _ordered_components(components)
    producer, model = _path(plan["producerPath"]), _path(plan["modelPath"])
    if producer == model or producer.suffix != ".py" or model.suffix != ".json":
        raise ValueError("Declare one Python producer and a separate canonical JSON model")
    if model in {_root() / PLAN_PATH, _root() / EXECUTION_PATH, _root() / "out/report_inputs.json"}:
        raise ValueError("The canonical model must be separate from report inputs and SDK control artifacts")
    if not isinstance(plan.get("producerArgs"), list) or any(not isinstance(x, str) for x in plan["producerArgs"]):
        raise ValueError("producerArgs must contain literal argument strings")
    paths = plan.get("sourcePaths")
    if not isinstance(paths, list) or any(_path(x) == model for x in paths):
        raise ValueError("sourcePaths must identify frozen inputs, separate from output")


def plan_analysis(*, producer_path: str, model_path: str, source_paths: Sequence[str], components: Sequence[Mapping[str, Any]], producer_args: Sequence[str] = ()) -> str:
    """Declare the current brief's work and direct dependencies before building.

    Components include id, kind, criterionIds, dependsOn, outputPaths,
    outputRequirements ({criterionId,resultId,path,type}: number/string/boolean leaves
    for every criterion and requested period/case), and optional
    decisions. Each decision has kind (reported_fact/accounting_definition/
    future_policy/implementation), state (resolved/unresolved), explanation and
    evidencePaths. Component kind is calculation/research/delivery.
    Historical periods depend on their own aligned inputs/definitions, not on
    unrelated future distribution choices. No plan is inferred from financial keys.
    Read the staged criteria's results first. resultId and type must match that
    independent contract, and minimumCount needs distinct scalar pointers. The
    executor cannot substitute a status string or count for numerical results.
    Legacy criteria without results retain the three-field declaration format.
    All required criteria must be represented, including research/delivery work.
    """
    declarations = json.loads(json.dumps(list(components), allow_nan=False))
    for row in declarations:
        for decision in row.get("decisions", []):
            decision["evidencePaths"] = [_logical(_path(x)) for x in decision.get("evidencePaths", [])]
    plan = {"version": 2, "producerPath": _logical(_path(producer_path)), "modelPath": _logical(_path(model_path)),
            "sourcePaths": [_logical(_path(x)) for x in source_paths], "components": declarations, "producerArgs": list(producer_args)}
    _validate(plan)
    path = _root() / PLAN_PATH
    _save(path, plan)
    return str(path)


def _plan() -> dict[str, Any]:
    plan = json.loads((_root() / PLAN_PATH).read_text())
    _validate(plan)
    return plan


def _inputs(plan: Mapping[str, Any]) -> dict[str, str]:
    paths = [plan["producerPath"], *plan["sourcePaths"],
             *(path for row in plan["components"] for decision in row.get("decisions", []) for path in decision["evidencePaths"])]
    return {path: _digest(_path(path)) for path in paths}


def execute_analysis(*, timeout_sec: int = 300) -> dict[str, Any]:
    """Execute the declared producer once and retain exact current output identity.

    The producer reads frozen inputs and writes modelPath. It should construct all
    independent components even if another component has an unresolved primitive.
    One subprocess, no model calls, no retry loop, no automatic assumption choices.
    Failed execution never leaves a successful execution receipt for these bytes.
    """
    plan = _plan()
    plan_hash = _digest(_root() / PLAN_PATH)
    before = _inputs(plan)
    if not isinstance(timeout_sec, int) or timeout_sec <= 0:
        raise ValueError("Analysis execution requires a positive timeout")
    args = plan["producerArgs"]
    receipt = _root() / EXECUTION_PATH
    receipt.unlink(missing_ok=True)
    model = _path(plan["modelPath"])
    report_path = _root() / "out/report_inputs.json"
    backups: dict[Path, Path | None] = {}
    for output in {model, report_path}:
        backup = _root() / ".nexustrade/analysis/checkpoints" / f"{uuid4().hex}.json" if output.exists() else None
        if backup is not None:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output, backup)
        backups[output] = backup
    model.unlink(missing_ok=True)
    try:
        subprocess.run([sys.executable, str(_path(plan["producerPath"])), *args], cwd=_root(), check=True, timeout=timeout_sec)
        if not model.exists():
            raise ValueError("Producer exited without writing a new canonical model")
        if _digest(_root() / PLAN_PATH) != plan_hash or _inputs(plan) != before:
            raise ValueError("Producer changed its declared plan/source inputs during execution")
        if model.stat().st_size > REPORT_INPUTS_MAX_BYTES:
            raise ValueError(f"report_capacity_exceeded: Canonical model exceeds {REPORT_INPUTS_MAX_BYTES} bytes; keep complete raw evidence separately with exact references")
        parsed = json.loads(model.read_text())
        assert_report_envelope(parsed)
        if not isinstance(parsed, dict):
            raise ValueError("Producer must write the canonical model object")
    except BaseException:
        for output, backup in backups.items():
            output.unlink(missing_ok=True)
            if backup is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                backup.replace(output)
        raise
    finally:
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)
    _save(receipt, {"version": 1, "planSha256": plan_hash, "inputSha256": before, "modelSha256": _digest(model),
                    "command": [_logical(_path(plan["producerPath"])), *args], "exitCode": 0})
    # The producer may export the report from the SAME model before this execution
    # receipt exists. Refresh only the SDK-owned construction field after success;
    # exact model equality prevents stamping an old report with new completion.
    report_export_required = True
    if report_path.exists():
        try:
            exported = json.loads(report_path.read_text())
        except json.JSONDecodeError:
            exported = None
        if isinstance(exported, dict) and json.dumps(exported.get("calculationModel"), sort_keys=True) == json.dumps(parsed, sort_keys=True):
            exported.pop(ANALYSIS_FIELD, None)
            exported = attach_analysis_work(exported)
            assert_report_envelope(exported)
            _save(report_path, exported)
            report_export_required = False
    return {**analysis_status(), "reportExportRequired": report_export_required}


def analysis_status(*, include_review: bool = True) -> dict[str, Any]:
    """Return current construction state and bounded next work; never a grade.

    Status is derived from the producer's current execution, output pointers and
    explicit dependencies. Patch scripts cannot overwrite a completion narrative.
    Null output is unfinished; a real zero is present. Acceptance requires current
    semantic readiness evidence, not merely an exit code or populated model.
    """
    plan = _plan()
    model_path = _path(plan["modelPath"])
    model = json.loads(model_path.read_text()) if model_path.exists() else {}
    receipt_path = _root() / EXECUTION_PATH
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    try:
        input_hashes = _inputs(plan)
    except OSError:
        input_hashes = None
    current = bool(receipt.get("exitCode") == 0 and receipt.get("planSha256") == _digest(_root() / PLAN_PATH)
                   and receipt.get("inputSha256") == input_hashes and model_path.exists()
                   and receipt.get("modelSha256") == _digest(model_path))
    components: list[dict[str, Any]] = []
    for row in plan["components"]:
        missing = []
        missing_results = missing_required_outputs(model, row.get('outputRequirements', []))
        if plan['version'] == 1:
            missing.append('Typed criterion outputs not declared; migrate the section-only plan')
        missing.extend(result['path'] for result in missing_results)
        for pointer in row["outputPaths"]:
            try:
                if _resolve_pointer(model, pointer) is None:
                    missing.append(pointer)
            except (KeyError, IndexError, ValueError):
                missing.append(pointer)
        unresolved = [x for x in row.get("decisions", []) if x["state"] == "unresolved"]
        components.append({**row, "status": "computed" if current and not missing and not unresolved else "not_started",
                           "missingOutputs": list(dict.fromkeys(missing)), "unresolvedPrimitives": unresolved,
                           "missingRequiredOutputs": missing_results})
    by_id = {row["id"]: row for row in components}
    for row in _ordered_components(components):
        if row["status"] == "computed" and any(by_id[x]["status"] != "computed" for x in row["dependsOn"]):
            row["status"] = "not_started"
    construction_available = {row['id'] for row in components if row['status'] == 'computed'}
    review_path = _root() / ".nexustrade/analysis/readiness.json"
    review = json.loads(review_path.read_text()) if include_review and review_path.exists() else {}
    review_current = current and review.get("execution") == receipt
    reviewed = None
    if review:
        reviewed = json.loads(_path(review["receiptPath"]).read_text())["result"]
        verdicts = {row["id"]: row["status"] for row in reviewed["criteria"]}
        for row in components:
            if row["status"] == "computed":
                if any(verdicts.get(x) == "gap" for x in row["criterionIds"]):
                    row["status"] = "review_rejected"
                elif review_current:
                    row["status"] = "accepted"
    by_id = {row["id"]: row for row in components}
    for row in _ordered_components(components):
        if row["status"] in {"computed", "accepted"} and any(by_id[x]["status"] == "review_rejected" for x in row["dependsOn"]):
            row["status"] = "review_rejected"
    tasks = []
    for row in components:
        if row["status"] in {"computed", "accepted"}:
            continue
        # A semantic rejection does not make executed inputs disappear. Keep
        # review rejection for acceptance, but allow independent construction
        # repairs while another research judgment is still being reviewed.
        waiting = [x for x in row["dependsOn"] if x not in construction_available]
        findings = [finding for finding in (reviewed or {}).get('findings', []) if finding['criterionId'] in row['criterionIds'] and finding['severity'] == 'blocking']
        tasks.append({"componentId": row["id"], "criterionIds": row["criterionIds"], "operation": "declare_outputs" if plan['version'] == 1 else ("repair_producer" if row["status"] == "review_rejected" else ("resolve_primitive" if row["unresolvedPrimitives"] else "execute_producer")),
                      "waitingOn": waiting, "readyToWork": not waiting, "missingOutputs": row["missingOutputs"], "unresolvedPrimitives": row["unresolvedPrimitives"],
                      "missingRequiredOutputs": row['missingRequiredOutputs'], "findings": findings,
                      "requiresSemanticReassessment": bool(findings)})
    return {"version": plan['version'], "producerPath": plan["producerPath"], "modelPath": plan["modelPath"],
            "executionCurrent": current, "execution": receipt, "components": components, "nextTasks": tasks}


def attach_analysis_work(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if ANALYSIS_FIELD in inputs:
        raise ValueError("analysisWork is derived from the current producer; do not hand-write completion statuses")
    result = dict(inputs)
    if (_root() / PLAN_PATH).exists():
        plan = _plan()
        model_path = _path(plan["modelPath"])
        if model_path.exists() and inputs.get("calculationModel") is not None:
            supplied = json.dumps(inputs["calculationModel"], sort_keys=True, allow_nan=False)
            saved = json.dumps(json.loads(model_path.read_text()), sort_keys=True, allow_nan=False)
            if supplied != saved:
                raise ValueError("Report calculationModel differs from the authoritative producer's canonical model")
        result[ANALYSIS_FIELD] = analysis_status(include_review=False)
        review_path = _root() / '.nexustrade/analysis/readiness.json'
        if review_path.exists():
            review = json.loads(review_path.read_text())
            if review.get('execution') == result[ANALYSIS_FIELD]['execution']:
                reviewed = json.loads(_path(review['receiptPath']).read_text())['result']
                if reviewed.get('hostReceipt'):
                    result[ANALYSIS_FIELD]['readinessReceipt'] = reviewed['hostReceipt']
    elif inputs.get("calculationModel") is not None and (_root() / "acceptance/acceptance_criteria.json").exists():
        raise ValueError("Declare report.plan_analysis for the staged criteria and execute its authoritative producer before export")
    return result


def construction_checkpoint(inputs: Mapping[str, Any]) -> dict[str, Any] | None:
    if not (_root() / PLAN_PATH).exists():
        if inputs.get("calculationModel") is None:
            return None
        return {"status": "needs_construction", "origin": "local_execution_preflight", "paidCalls": 0,
                "summary": "Declare the required components and their direct dependencies, then execute the authoritative producer before broad semantic review",
                "components": [], "nextTasks": [{"operation": "plan_analysis", "criterionIds": sorted(_required()), "readyToWork": True, "waitingOn": []}]}
    current = analysis_status(include_review=False)
    supplied = {key: value for key, value in inputs.get(ANALYSIS_FIELD, {}).items() if key != 'readinessReceipt'}
    if supplied != current:
        raise ValueError("Saved analysisWork is stale; regenerate report inputs from the current authoritative producer")
    if not current["nextTasks"]:
        reviewed = analysis_status()
        if reviewed['nextTasks']:
            previous_review = json.loads((_root() / '.nexustrade/analysis/readiness.json').read_text())
            prior = previous_review['execution']
            inputs_without_producer = lambda execution: {path: digest for path, digest in execution.get('inputSha256', {}).items() if path != current['producerPath']}
            if (prior.get('modelSha256') == current['execution'].get('modelSha256') and
                    inputs_without_producer(prior) == inputs_without_producer(current['execution']) and
                    previous_review.get('reportSha256') == _report_digest(inputs)):
                current = reviewed
            else:
                return None  # Changed candidate may be reassessed; open findings are not closed.
        else:
            return None
    return {"status": "needs_construction", "origin": "local_execution_preflight", "paidCalls": 0,
            "summary": "Required components are unfinished; construct independent ready components before broad semantic review",
            "components": current["components"], "nextTasks": current["nextTasks"]}


def record_readiness(receipt_path: Path, result: Mapping[str, Any], inputs: Mapping[str, Any]) -> None:
    if not (_root() / PLAN_PATH).exists():
        return
    current = analysis_status(include_review=False)
    supplied = {key: value for key, value in inputs.get(ANALYSIS_FIELD, {}).items() if key != 'readinessReceipt'}
    if supplied != current:
        raise ValueError("Analysis changed while semantic review was running; that review cannot accept the new outputs")
    _save(_root() / ".nexustrade/analysis/readiness.json", {"execution": current["execution"], "receiptPath": _logical(receipt_path), "reportSha256":_report_digest(inputs)})
