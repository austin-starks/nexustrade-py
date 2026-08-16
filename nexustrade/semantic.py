"""Schema-bound, append-only semantic projection over immutable source rows."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any


DEFAULT_BATCH_SIZE = 40
DEFAULT_MAX_WORKERS = 8
DEFAULT_MAX_SPLIT_DEPTH = 2


class SemanticProjectionError(ValueError):
    """The semantic projection response violated the one-to-one boundary."""


_SYSTEM_PROMPT = """You derive task-specific semantic fields from immutable source records.
Return exactly one result for every input_index and put every interpretation under derived.
Read every relevant field in the same record. A short code is one observation, not an
authoritative conclusion when another same-row field describes a conflicting event.
Do not copy, rewrite, normalize, or omit raw source fields. When evidence conflicts or is
insufficient, express that uncertainty in the caller-provided derived schema rather than
silently choosing a convenient value. Do not infer from neighboring records."""


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


def derive_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    instruction: str,
    derived_schema: Mapping[str, Any],
    model: str | None = None,
    system: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_split_depth: int = DEFAULT_MAX_SPLIT_DEPTH,
) -> list[dict[str, Any]]:
    """Derive namespaced semantics without permitting mutation of source observations.

    ``rows`` are serialized verbatim with a host-owned ``input_index``. The model may
    return only that index and fields allowed by ``derived_schema``. Large inputs are
    processed in bounded parallel batches; malformed structured responses are split a
    bounded number of times and transport failures remain owned by the gateway retry
    policy. The result contains one ``{"raw": ..., "derived": ...}`` object per input
    row in original order, and no partial batch is returned.
    """
    if not isinstance(instruction, str) or not instruction.strip():
        raise SemanticProjectionError("instruction must be non-empty")
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
    raw_rows = [copy.deepcopy(dict(row)) for row in rows]
    if not raw_rows:
        return []
    strict_derived_schema = _strict_object_schema(derived_schema)
    expected_derived_keys = set(strict_derived_schema["properties"])
    schema = _response_schema(strict_derived_schema)

    def project_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from nexustrade.host import gateway_chat_json

        payload = {
            "instruction": instruction.strip(),
            "records": [
                {"input_index": index, "raw": row}
                for index, row in enumerate(batch)
            ],
        }
        result = gateway_chat_json(
            prompt=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            system=(system.strip() + "\n\n" if system and system.strip() else "")
            + _SYSTEM_PROMPT,
            model=model,
            temperature=0,
            json_schema=schema,
            schema_name="semantic_projection",
        )
        projected = result.get("rows") if isinstance(result, Mapping) else None
        if not isinstance(projected, list):
            raise SemanticProjectionError("semantic projection response omitted rows")
        by_index: dict[int, dict[str, Any]] = {}
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
            if index in by_index:
                raise SemanticProjectionError(
                    f"semantic projection duplicated index {index}"
                )
            if not isinstance(derived, Mapping):
                raise SemanticProjectionError(
                    f"semantic projection derived value for index {index} is not an object"
                )
            actual_derived_keys = set(derived)
            if actual_derived_keys != expected_derived_keys:
                missing = sorted(expected_derived_keys.difference(actual_derived_keys))
                extra = sorted(actual_derived_keys.difference(expected_derived_keys))
                raise SemanticProjectionError(
                    f"semantic projection derived keys for index {index} do not match "
                    f"the schema (missing={missing}, extra={extra})"
                )
            by_index[index] = dict(derived)
        expected = set(range(len(batch)))
        if set(by_index) != expected:
            missing = sorted(expected.difference(by_index))
            raise SemanticProjectionError(
                f"semantic projection omitted input index(es): {missing}"
            )
        return [
            {"raw": copy.deepcopy(batch[index]), "derived": by_index[index]}
            for index in range(len(batch))
        ]

    def project_with_split(
        batch: list[dict[str, Any]], split_depth: int = 0
    ) -> list[dict[str, Any]]:
        try:
            return project_batch(batch)
        except SemanticProjectionError:
            if len(batch) == 1 or split_depth >= max_split_depth:
                raise
            midpoint = len(batch) // 2
            return project_with_split(
                batch[:midpoint], split_depth + 1
            ) + project_with_split(
                batch[midpoint:], split_depth + 1
            )

    batches = [
        (start, raw_rows[start : start + batch_size])
        for start in range(0, len(raw_rows), batch_size)
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
