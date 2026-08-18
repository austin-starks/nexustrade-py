"""Schema-bound, append-only semantic projection over immutable source rows."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal


DEFAULT_BATCH_SIZE = 40
# Evidence-owned projections deliberately use one request per source record.
# Twenty-four concurrent network-bound requests keeps large corpora moving while
# the gateway owns transport retries and this module retains all-or-nothing
# result assembly.
DEFAULT_MAX_WORKERS = 24
DEFAULT_MAX_SPLIT_DEPTH = 2
DEFAULT_MAX_VALIDATION_RETRIES = 2
EVIDENCE_REFS_FIELD = "evidence_refs"
EvidenceRequirement = Literal["always", "truthy", "falsey", "nonempty"]
_EVIDENCE_REQUIREMENTS = frozenset({"always", "truthy", "falsey", "nonempty"})


class SemanticProjectionError(ValueError):
    """The semantic projection response violated the one-to-one boundary."""


class _SemanticProjectionRowErrors(SemanticProjectionError):
    """Validation failures confined to identified rows in an otherwise complete batch."""

    def __init__(
        self,
        valid_by_index: Mapping[int, Mapping[str, Any]],
        errors_by_index: Mapping[int, str],
    ) -> None:
        self.valid_by_index = {
            index: copy.deepcopy(dict(derived))
            for index, derived in valid_by_index.items()
        }
        self.errors_by_index = dict(errors_by_index)
        details = "; ".join(
            f"input_index {index}: {message}"
            for index, message in sorted(self.errors_by_index.items())
        )
        super().__init__(f"semantic projection row validation failed: {details}")


_SYSTEM_PROMPT = """You derive task-specific semantic fields from immutable source records.
Return exactly one result for every input_index and put every interpretation under derived.
Read every relevant field in the same record. Do not assume a universal priority among prose,
codes, labels, identifiers, and metadata. Infer their distinct roles and authority from the
caller's instruction, supplied source definitions, and record context. Resolve apparently
conflicting evidence only when that context gives an evidence-grounded reason; otherwise keep
the conflict unresolved in the caller-provided schema.
Preserve the caller's event and population predicates exactly. A related economic outcome does
not satisfy a different requested event. A compact code does not override an explicit
natural-language description unless a supplied source definition establishes that precedence.
Apply explicit exclusions by ordinary semantic meaning; equivalent source language need not
repeat the caller's exact words.
Do not copy, rewrite, normalize, or omit raw source fields. When evidence conflicts or is
insufficient, express that uncertainty in the caller-provided derived schema rather than
silently choosing a convenient value. Do not infer from neighboring records.
Every caller-provided criterion is phrased so that its satisfied condition maps to true. Before
returning, compare each boolean or true/false/unknown outcome with your own reason and cited
source value. If the reason says the condition is satisfied, do not return false; if it names a
direct contradiction, do not return true. Apply the same interpretation to records with the same
load-bearing evidence; identity, ordering, date, or amount may change an outcome only when the
criterion makes that field relevant."""


_EVIDENCE_REFERENCE_PROMPT = """The caller requires machine-verifiable record-local evidence.
Return evidence_refs as predicate/path pairs. Each path is an RFC 6901 JSON Pointer relative to
the contents of the same record's raw object and must resolve to a concrete scalar source value
that directly supports that predicate. The request envelope's `raw` key is host-owned and is not
part of the pointer: cite `/field`, never `/raw/field`. Do not quote, paraphrase, or cite another
record. The host resolves and attaches cited values after validation; you return only predicate
and path."""


_VALIDATION_REPAIR_PROMPT = """A prior structured response for this same input failed host
validation. Use the supplied validation_feedback only to repair the response contract. Re-read
the immutable record and original instruction; do not relax or reinterpret the task predicate,
invent evidence, or copy a neighboring record. When validation_feedback lists valid non-empty
scalar paths, copy an applicable listed path exactly, including every parent segment; do not
shorten, rename, or reconstruct a near-equivalent path. The host-owned request-envelope key
`raw` is never a pointer segment; replace `/raw/field` with the listed `/field` path."""


_INCLUSION_AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "required_predicate_contradicted": {"type": "boolean"},
        "explicit_exclusion_present": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}

_INCLUSION_AUDIT_INSTRUCTION = """Audit only whether each proposed inclusion has a
concrete blocking defect. Set inclusion_supported=false only when the supplied immutable
evidence directly contradicts an explicit required predicate or directly matches an explicit
exclusion in the caller's task instruction. Absence of additional evidence is not a blocker
in this audit: the first-pass unresolved predicate owns genuinely missing facts. Do not reject
for missing corroboration, the abstract possibility of finer metadata, or source fields that
describe orthogonal dimensions of the same record. Preserve every proposed inclusion without
direct blocking evidence. Do not invent a new population predicate from source metadata that
the caller did not make load-bearing. An opaque abbreviation, compact code, identifier, or
unlabeled category that appears only as metadata and lacks a supplied source definition cannot
establish a contradiction by itself. Interpret a natural-language description as a whole. A
source term may establish only the semantic dimension it actually describes; do not make it
decide an orthogonal event, category, identity, or status merely because the same record also
contains that term. Apply the same source-grounded interpretation to materially identical
load-bearing descriptions across the audited batch.
Audit required predicates as well as exclusions. When the source
explicitly names a different event from the event the caller requires, that is a direct
contradiction even if the events have a related economic outcome. Cite the direct evidence
through the required record-local evidence reference and return a concise reason; never rewrite
the source record or invent a replacement result.
Report the two blocking dimensions independently. Set required_predicate_contradicted=true only
for direct contradictory evidence. Set explicit_exclusion_present=true when the record directly
matches an exclusion in the caller's exact task. Do not add a qualifier to an exclusion or let a
different satisfied predicate override it: if the caller excludes X, direct evidence of X remains
an exclusion even when the same record also has Y, unless the caller explicitly states that
exception. Do not return an inclusion_supported field; the SDK computes that final decision
mechanically as not (required_predicate_contradicted or explicit_exclusion_present)."""


def _strict_schema_node(schema: Mapping[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(schema))
    properties = normalized.get("properties")
    if isinstance(properties, Mapping):
        normalized["properties"] = {
            str(name): _strict_schema_node(value)
            for name, value in properties.items()
            if isinstance(value, Mapping)
        }
        if len(normalized["properties"]) != len(properties):
            raise SemanticProjectionError("every schema property must be an object")
    items = normalized.get("items")
    if isinstance(items, Mapping):
        normalized["items"] = _strict_schema_node(items)
    for keyword in ("anyOf", "oneOf", "allOf"):
        branches = normalized.get(keyword)
        if isinstance(branches, list):
            if not all(isinstance(branch, Mapping) for branch in branches):
                raise SemanticProjectionError(
                    f"every {keyword} branch must be an object"
                )
            normalized[keyword] = [
                _strict_schema_node(branch) for branch in branches
            ]
    schema_type = normalized.get("type")
    object_typed = schema_type == "object" or (
        isinstance(schema_type, list) and "object" in schema_type
    )
    if object_typed:
        strict_properties = normalized.get("properties")
        if not isinstance(strict_properties, Mapping):
            raise SemanticProjectionError("object schemas must declare properties")
        normalized["additionalProperties"] = False
        normalized["required"] = list(strict_properties)
    return normalized


def _strict_object_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _strict_schema_node(schema)
    if normalized.get("type") != "object":
        raise SemanticProjectionError("derived_schema must be a JSON Schema object")
    properties = normalized.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        raise SemanticProjectionError(
            "derived_schema must declare at least one property"
        )
    return normalized


def _response_schema(derived_schema: Mapping[str, Any]) -> dict[str, Any]:
    derived = _strict_object_schema(derived_schema)
    item = {
        "type": "object",
        "properties": {
            "input_index": {"type": "integer", "minimum": 0},
            "derived": derived,
        },
        "required": ["input_index", "derived"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"rows": {"type": "array", "items": item}},
        "required": ["rows"],
        "additionalProperties": False,
    }


def _instruction_text(instruction: str | Mapping[str, Any]) -> str:
    if isinstance(instruction, str):
        normalized = instruction.strip()
    elif isinstance(instruction, Mapping) and instruction:
        normalized = json.dumps(
            dict(instruction), separators=(",", ":"), ensure_ascii=False
        )
    else:
        normalized = ""
    if not normalized:
        raise SemanticProjectionError("instruction must be non-empty")
    return normalized


def _normalize_evidence_requirements(
    requirements: Mapping[str, EvidenceRequirement] | None,
    derived_schema: Mapping[str, Any],
) -> dict[str, EvidenceRequirement]:
    if requirements is None:
        return {}
    if not isinstance(requirements, Mapping):
        raise SemanticProjectionError("evidence_requirements must be a mapping")
    properties = derived_schema.get("properties")
    if not isinstance(properties, Mapping):
        raise SemanticProjectionError("derived_schema must declare properties")
    if EVIDENCE_REFS_FIELD in properties:
        raise SemanticProjectionError(
            f"{EVIDENCE_REFS_FIELD} is reserved by derive_rows"
        )
    normalized: dict[str, EvidenceRequirement] = {}
    for predicate, requirement in requirements.items():
        if not isinstance(predicate, str) or predicate not in properties:
            raise SemanticProjectionError(
                f"evidence predicate {predicate!r} is not a derived_schema property"
            )
        if requirement not in _EVIDENCE_REQUIREMENTS:
            raise SemanticProjectionError(
                f"evidence requirement for {predicate!r} must be one of "
                f"{sorted(_EVIDENCE_REQUIREMENTS)}"
            )
        normalized[predicate] = requirement
    return normalized


def _schema_with_evidence_refs(
    derived_schema: Mapping[str, Any],
    requirements: Mapping[str, EvidenceRequirement],
) -> dict[str, Any]:
    schema = copy.deepcopy(dict(derived_schema))
    if not requirements:
        return schema
    properties = schema["properties"]
    properties[EVIDENCE_REFS_FIELD] = {
        "type": "array",
        "description": (
            "Record-local source evidence. Paths are RFC 6901 JSON Pointers relative "
            "to this input row's raw object."
        ),
        "items": {
            "type": "object",
            "properties": {
                "predicate": {
                    "type": "string",
                    "enum": sorted(requirements),
                },
                "path": {"type": "string", "minLength": 1},
            },
        },
    }
    return schema


def _decode_pointer_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in ("0", "1"):
            raise SemanticProjectionError("evidence path has invalid JSON Pointer escape")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _encode_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _scalar_evidence_paths(value: Any, *, limit: int = 16) -> list[str]:
    """Return bounded non-empty scalar pointers with top-level branch coverage.

    Validation feedback previously walked depth-first and stopped after 16
    paths. A large instruction branch such as ``record_criteria`` could consume
    that entire budget before the traversal reached ``record`` or
    ``document_context``. The repair model was then told that prompt prose was
    valid evidence while every useful row path remained invisible.

    Keep direct scalars first, collect a bounded shallow-first list within each
    remaining top-level branch, then round-robin those branch lists. A global
    collection ceiling would let one unusually large early branch starve every
    later source branch before round-robin selection began.
    """

    def children_of(current: Any, prefix: str) -> list[tuple[str, Any]]:
        if isinstance(current, Mapping):
            return [
                (f"{prefix}/{_encode_pointer_token(str(key))}", child)
                for key, child in current.items()
            ]
        if isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            return [
                (f"{prefix}/{index}", child)
                for index, child in enumerate(current)
            ]
        return []

    def is_container(candidate: Any) -> bool:
        return isinstance(candidate, Mapping) or (
            isinstance(candidate, Sequence)
            and not isinstance(candidate, (str, bytes, bytearray))
        )

    def is_non_empty_scalar(candidate: Any) -> bool:
        return candidate is not None and not is_container(candidate) and not (
            isinstance(candidate, str) and not candidate.strip()
        )

    direct: list[str] = []
    branch_roots: list[tuple[str, Any]] = []
    for child_path, child in children_of(value, ""):
        if is_non_empty_scalar(child):
            direct.append(child_path)
        elif is_container(child):
            branch_roots.append((child_path, child))

    if len(direct) >= limit:
        return direct[:limit]

    by_branch: list[list[str]] = []
    for root_path, root_value in branch_roots:
        branch_paths: list[str] = []
        queue = children_of(root_value, root_path)
        cursor = 0
        while cursor < len(queue) and len(branch_paths) < limit:
            child_path, child = queue[cursor]
            cursor += 1
            if is_non_empty_scalar(child):
                branch_paths.append(child_path)
            elif is_container(child):
                queue.extend(children_of(child, child_path))
        by_branch.append(branch_paths)

    found = list(direct)
    while len(found) < limit:
        added = False
        for paths in by_branch:
            if not paths:
                continue
            found.append(paths.pop(0))
            added = True
            if len(found) >= limit:
                break
        if not added:
            break
    return found


def _resolve_evidence_pointer(row: Mapping[str, Any], path: str) -> Any:
    if not isinstance(path, str) or not path.startswith("/"):
        raise SemanticProjectionError(
            "evidence path must be a non-root RFC 6901 JSON Pointer"
        )
    current: Any = row
    for encoded_token in path[1:].split("/"):
        token = _decode_pointer_token(encoded_token)
        if isinstance(current, Mapping):
            if token not in current:
                raise SemanticProjectionError(
                    f"evidence path {path!r} does not resolve in its raw row"
                )
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            if not token.isdigit():
                raise SemanticProjectionError(
                    f"evidence path {path!r} has a non-numeric array index"
                )
            item_index = int(token)
            if item_index >= len(current):
                raise SemanticProjectionError(
                    f"evidence path {path!r} has an out-of-range array index"
                )
            current = current[item_index]
        else:
            raise SemanticProjectionError(
                f"evidence path {path!r} traverses through a scalar value"
            )
    if current is None or isinstance(current, (Mapping, list, tuple)):
        raise SemanticProjectionError(
            f"evidence path {path!r} must resolve to a concrete scalar value"
        )
    if isinstance(current, str) and not current.strip():
        raise SemanticProjectionError(
            f"evidence path {path!r} resolves to an empty string"
        )
    return current


def _resolve_evidence_pointer_compat(
    row: Mapping[str, Any], path: str
) -> tuple[str, Any]:
    """Resolve record-relative evidence with host-envelope compatibility.

    The model sees a record inside a host-owned ``raw`` field and may therefore
    cite ``/raw/field`` even though the public contract is relative to that
    field's contents. Try the literal pointer first so a caller-owned ``raw``
    property keeps its normal RFC 6901 meaning. Strip the wrapper only when the
    literal pointer is invalid and the record-relative alternative resolves.
    """
    try:
        return path, _resolve_evidence_pointer(row, path)
    except SemanticProjectionError as literal_error:
        if not path.startswith("/raw/"):
            raise
        normalized_path = path[len("/raw") :]
        try:
            return normalized_path, _resolve_evidence_pointer(
                row, normalized_path
            )
        except SemanticProjectionError:
            raise literal_error


def _requires_evidence(value: Any, requirement: EvidenceRequirement) -> bool:
    if requirement == "always":
        return True
    if requirement == "truthy":
        return bool(value)
    if requirement == "falsey":
        return not bool(value)
    if isinstance(value, (str, Mapping, Sequence)) and not isinstance(
        value, (bytes, bytearray)
    ):
        return len(value) > 0
    return bool(value)


def _validate_evidence_refs(
    raw_row: Mapping[str, Any],
    derived: Mapping[str, Any],
    requirements: Mapping[str, EvidenceRequirement],
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(derived))
    if not requirements:
        return normalized
    refs = derived.get(EVIDENCE_REFS_FIELD)
    if not isinstance(refs, list):
        raise SemanticProjectionError(
            f"semantic projection omitted {EVIDENCE_REFS_FIELD}"
        )
    enriched_refs: list[dict[str, Any]] = []
    covered: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        if not isinstance(ref, Mapping):
            raise SemanticProjectionError("semantic evidence reference is not an object")
        predicate = ref.get("predicate")
        path = ref.get("path")
        if not isinstance(predicate, str) or predicate not in requirements:
            raise SemanticProjectionError(
                f"semantic evidence reference has invalid predicate {predicate!r}"
            )
        if not isinstance(path, str):
            raise SemanticProjectionError("semantic evidence reference path is not a string")
        try:
            normalized_path, value = _resolve_evidence_pointer_compat(
                raw_row, path
            )
        except SemanticProjectionError as exc:
            candidates = _scalar_evidence_paths(raw_row)
            suffix = (
                "; valid non-empty scalar paths in this row include "
                + ", ".join(repr(candidate) for candidate in candidates)
                if candidates
                else "; this row contains no non-empty scalar evidence paths"
            )
            raise SemanticProjectionError(f"{exc}{suffix}") from exc
        identity = (predicate, normalized_path)
        if identity in seen:
            raise SemanticProjectionError(
                "semantic evidence reference is duplicated for "
                f"{predicate!r} at {normalized_path!r}"
            )
        seen.add(identity)
        covered.add(predicate)
        enriched_refs.append(
            {
                "predicate": predicate,
                "path": normalized_path,
                "value": copy.deepcopy(value),
            }
        )
    for predicate, requirement in requirements.items():
        if _requires_evidence(derived[predicate], requirement) and predicate not in covered:
            raise SemanticProjectionError(
                f"semantic projection omitted required evidence for {predicate!r}"
            )
    normalized[EVIDENCE_REFS_FIELD] = enriched_refs
    return normalized


def derive_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    instruction: str | Mapping[str, Any],
    derived_schema: Mapping[str, Any],
    evidence_requirements: Mapping[str, EvidenceRequirement] | None = None,
    model: str | None = None,
    system: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_split_depth: int = DEFAULT_MAX_SPLIT_DEPTH,
    max_validation_retries: int = DEFAULT_MAX_VALIDATION_RETRIES,
) -> list[dict[str, Any]]:
    """Derive namespaced semantics without permitting mutation of source observations.

    ``rows`` are serialized verbatim with a host-owned ``input_index``. ``instruction``
    accepts either prose or a structured mapping. The model may return only that index and
    fields allowed by ``derived_schema``. Optional ``evidence_requirements`` names predicates
    whose load-bearing values need a same-row JSON Pointer; the host validates each pointer
    and attaches its immutable source value. Evidence-owned projections isolate records into
    separate model requests so a neighboring record cannot contaminate a load-bearing
    interpretation. Independent requests still run with bounded parallelism. Projections
    without evidence requirements retain bounded multi-record batching. Malformed structured
    responses are split and terminal validation batches are retried a bounded number of times.
    Transport failures remain owned by the gateway retry policy. The result contains one
    ``{"raw": ..., "derived": ...}`` object per input row in original order, and no
    partial batch is returned.
    """
    instruction_text = _instruction_text(instruction)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers must be a positive integer")
    if (
        isinstance(max_split_depth, bool)
        or not isinstance(max_split_depth, int)
        or max_split_depth < 0
    ):
        raise ValueError("max_split_depth must be a non-negative integer")
    if (
        isinstance(max_validation_retries, bool)
        or not isinstance(max_validation_retries, int)
        or max_validation_retries < 0
    ):
        raise ValueError("max_validation_retries must be a non-negative integer")
    raw_rows = [copy.deepcopy(dict(row)) for row in rows]
    if not raw_rows:
        return []
    strict_derived_schema = _strict_object_schema(derived_schema)
    normalized_evidence_requirements = _normalize_evidence_requirements(
        evidence_requirements, strict_derived_schema
    )
    strict_derived_schema = _strict_object_schema(
        _schema_with_evidence_refs(
            strict_derived_schema, normalized_evidence_requirements
        )
    )
    expected_derived_keys = set(strict_derived_schema["properties"])
    schema = _response_schema(strict_derived_schema)

    def project_batch(
        batch: list[dict[str, Any]], validation_feedback: str | None = None
    ) -> list[dict[str, Any]]:
        from nexustrade.host import gateway_chat_json

        payload = {
            "instruction": instruction_text,
            "records": [
                {"input_index": index, "raw": row}
                for index, row in enumerate(batch)
            ],
        }
        if validation_feedback:
            payload["validation_feedback"] = validation_feedback
        result = gateway_chat_json(
            prompt=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            system=(system.strip() + "\n\n" if system and system.strip() else "")
            + _SYSTEM_PROMPT
            + (
                "\n\n" + _EVIDENCE_REFERENCE_PROMPT
                if normalized_evidence_requirements
                else ""
            )
            + ("\n\n" + _VALIDATION_REPAIR_PROMPT if validation_feedback else ""),
            model=model,
            temperature=0,
            json_schema=schema,
            schema_name="semantic_projection",
        )
        projected = result.get("rows") if isinstance(result, Mapping) else None
        if not isinstance(projected, list):
            raise SemanticProjectionError("semantic projection response omitted rows")
        by_index: dict[int, dict[str, Any]] = {}
        row_errors: dict[int, str] = {}
        seen_indices: set[int] = set()
        for item in projected:
            if not isinstance(item, Mapping):
                raise SemanticProjectionError("semantic projection row is not an object")
            index = item.get("input_index")
            derived = item.get("derived")
            if not isinstance(index, int) or isinstance(index, bool):
                raise SemanticProjectionError(
                    "semantic projection input_index is not an integer"
                )
            if index < 0 or index >= len(batch):
                raise SemanticProjectionError(
                    f"semantic projection index {index} is out of range"
                )
            if index in seen_indices:
                raise SemanticProjectionError(
                    f"semantic projection duplicated index {index}"
                )
            seen_indices.add(index)
            if not isinstance(derived, Mapping):
                row_errors[index] = (
                    f"semantic projection derived value for index {index} is not an object"
                )
                continue
            actual_derived_keys = set(derived)
            if actual_derived_keys != expected_derived_keys:
                missing = sorted(expected_derived_keys.difference(actual_derived_keys))
                extra = sorted(actual_derived_keys.difference(expected_derived_keys))
                row_errors[index] = (
                    f"semantic projection derived keys for index {index} do not match "
                    f"the schema (missing={missing}, extra={extra})"
                )
                continue
            try:
                by_index[index] = _validate_evidence_refs(
                    batch[index], derived, normalized_evidence_requirements
                )
            except SemanticProjectionError as exc:
                row_errors[index] = str(exc)
        expected = set(range(len(batch)))
        if seen_indices != expected:
            missing = sorted(expected.difference(seen_indices))
            raise SemanticProjectionError(
                f"semantic projection omitted input index(es): {missing}"
            )
        if row_errors:
            raise _SemanticProjectionRowErrors(by_index, row_errors)
        return [
            {"raw": copy.deepcopy(batch[index]), "derived": by_index[index]}
            for index in range(len(batch))
        ]

    def project_with_split(
        batch: list[dict[str, Any]],
        split_depth: int = 0,
        validation_feedback: str | None = None,
        targeted_retries: int = 0,
    ) -> list[dict[str, Any]]:
        caught_error: SemanticProjectionError | None = None
        try:
            return project_batch(batch, validation_feedback)
        except _SemanticProjectionRowErrors as row_error:
            invalid_indices = sorted(row_error.errors_by_index)
            if (
                row_error.valid_by_index
                and len(invalid_indices) < len(batch)
                and targeted_retries < max_validation_retries
            ):
                invalid_batch = [batch[index] for index in invalid_indices]
                retry_feedback = "\n".join(
                    f"retry input_index {retry_index} previously failed validation: "
                    f"{row_error.errors_by_index[original_index]}"
                    for retry_index, original_index in enumerate(invalid_indices)
                )
                repaired = project_with_split(
                    invalid_batch,
                    split_depth,
                    retry_feedback,
                    targeted_retries + 1,
                )
                repaired_by_index = {
                    original_index: repaired[retry_index]
                    for retry_index, original_index in enumerate(invalid_indices)
                }
                return [
                    repaired_by_index[index]
                    if index in repaired_by_index
                    else {
                        "raw": copy.deepcopy(batch[index]),
                        "derived": copy.deepcopy(row_error.valid_by_index[index]),
                    }
                    for index in range(len(batch))
                ]
            caught_error = row_error
        except SemanticProjectionError as exc:
            caught_error = exc
        if caught_error is None:
            raise SemanticProjectionError("semantic projection failed without an error")
        if len(batch) > 1 and split_depth < max_split_depth:
            midpoint = len(batch) // 2
            return project_with_split(
                batch[:midpoint], split_depth + 1, targeted_retries=targeted_retries
            ) + project_with_split(
                batch[midpoint:], split_depth + 1, targeted_retries=targeted_retries
            )
        latest_error = caught_error
        for _ in range(max_validation_retries):
            try:
                return project_batch(batch, str(latest_error))
            except SemanticProjectionError as retry_error:
                latest_error = retry_error
        raise latest_error

    request_batch_size = 1 if normalized_evidence_requirements else batch_size
    batches = [
        (start, raw_rows[start : start + request_batch_size])
        for start in range(0, len(raw_rows), request_batch_size)
    ]
    projected_batches: dict[int, list[dict[str, Any]]] = {}
    workers = min(max_workers, len(batches))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(project_with_split, batch): (index, start, len(batch))
            for index, (start, batch) in enumerate(batches)
        }
        for future in as_completed(futures):
            index, start, length = futures[future]
            try:
                projected_batches[index] = future.result()
            except SemanticProjectionError as exc:
                end = start + length - 1
                raise SemanticProjectionError(
                    "semantic projection failed for source row range "
                    f"{start}-{end}: {exc}"
                ) from exc
    projected_rows = [
        row
        for index in range(len(batches))
        for row in projected_batches[index]
    ]
    if [dict(row) for row in rows] != raw_rows:
        raise SemanticProjectionError("source rows changed during semantic projection")
    return projected_rows


def audit_inclusions(
    rows: Sequence[Mapping[str, Any]],
    *,
    instruction: str | Mapping[str, Any],
    model: str | None = None,
    system: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_split_depth: int = DEFAULT_MAX_SPLIT_DEPTH,
    max_validation_retries: int = DEFAULT_MAX_VALIDATION_RETRIES,
) -> list[dict[str, Any]]:
    """Audit proposed semantic inclusions without reinterpreting the whole population.

    Each row should preserve the complete source evidence and the caller's proposed
    predicate values. The first semantic pass remains responsible for missing facts and
    unresolved evidence. This bounded second pass can only block a proposal for a direct
    contradiction or explicit exclusion, preventing a critic from manufacturing a stricter
    proof burden while retaining the same one-to-one, immutable, parallel boundary as
    :func:`derive_rows`.
    """
    audit_instruction = (
        _instruction_text(instruction) + "\n\n" + _INCLUSION_AUDIT_INSTRUCTION
    )
    audited = derive_rows(
        rows,
        instruction=audit_instruction,
        derived_schema=_INCLUSION_AUDIT_SCHEMA,
        evidence_requirements={
            "required_predicate_contradicted": "truthy",
            "explicit_exclusion_present": "truthy",
        },
        model=model,
        system=system,
        batch_size=batch_size,
        max_workers=max_workers,
        max_split_depth=max_split_depth,
        max_validation_retries=max_validation_retries,
    )
    for index, pair in enumerate(audited):
        derived = pair.get("derived")
        if not isinstance(derived, Mapping):
            raise SemanticProjectionError(
                f"inclusion audit row {index} has no derived decision"
            )
        contradiction = derived.get("required_predicate_contradicted")
        exclusion = derived.get("explicit_exclusion_present")
        if not all(isinstance(value, bool) for value in (contradiction, exclusion)):
            raise SemanticProjectionError(
                f"inclusion audit row {index} has non-boolean decision components"
            )
        derived_with_decision = dict(derived)
        derived_with_decision["inclusion_supported"] = not (contradiction or exclusion)
        pair["derived"] = derived_with_decision
    return audited


def verify_semantic_citations(
    *,
    evidence_id: str,
    request: str,
    assertions: Sequence[Mapping[str, Any]],
    source_authority: str | None = None,
) -> dict[str, Any]:
    """Independently verify proposed criterion outcomes against cited evidence.

    Each assertion supplies ``assertionId``, ``completeRecordEvidence``, and
    criterion decisions with ``criterionId``, ``positiveCondition``,
    ``proposedOutcome``, ``proposedReason``, and same-record RFC 6901
    ``citedPaths``. The host resolves those pointers, exposes all scalar values
    from the same record to catch selective citation, runs the stored native-Luna
    verifier, and validates every returned id and evidence reference. The
    verifier reports support, contradiction, or insufficiency; it never rewrites
    the caller's decision.
    """
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValueError("evidence_id must be non-empty")
    if not isinstance(request, str) or not request.strip():
        raise ValueError("request must be non-empty")
    if not isinstance(assertions, Sequence) or isinstance(assertions, (str, bytes)):
        raise ValueError("assertions must be a non-empty sequence")
    normalized_assertions = [
        copy.deepcopy(dict(assertion)) for assertion in assertions
    ]
    if not normalized_assertions:
        raise ValueError("assertions must be a non-empty sequence")
    payload: dict[str, Any] = {
        "evidenceId": evidence_id.strip(),
        "request": request.strip(),
        "assertions": normalized_assertions,
    }
    if source_authority is not None and not isinstance(source_authority, str):
        raise ValueError("source_authority must be a string when supplied")
    if source_authority is not None and source_authority.strip():
        payload["sourceAuthority"] = source_authority.strip()
    from nexustrade.host import gateway_semantic_verify

    return gateway_semantic_verify(payload)
