"""Schema-bound, append-only semantic projection over immutable source rows."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any


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
) -> list[dict[str, Any]]:
    """Derive namespaced semantics without permitting mutation of source observations.

    ``rows`` are serialized verbatim with a host-owned ``input_index``. The model may
    return only that index and fields allowed by ``derived_schema``. The result contains
    one ``{"raw": ..., "derived": ...}`` object per input row in original order.
    """
    if not isinstance(instruction, str) or not instruction.strip():
        raise SemanticProjectionError("instruction must be non-empty")
    raw_rows = [copy.deepcopy(dict(row)) for row in rows]
    if not raw_rows:
        return []
    strict_derived_schema = _strict_object_schema(derived_schema)
    schema = _response_schema(strict_derived_schema)
    expected_derived_keys = set(strict_derived_schema["properties"])
    payload = {
        "instruction": instruction.strip(),
        "records": [
            {"input_index": index, "raw": row}
            for index, row in enumerate(raw_rows)
        ],
    }

    from nexustrade.host import gateway_chat_json

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
        if index < 0 or index >= len(raw_rows):
            raise SemanticProjectionError(
                f"semantic projection index {index} is out of range"
            )
        if index in by_index:
            raise SemanticProjectionError(f"semantic projection duplicated index {index}")
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
    expected = set(range(len(raw_rows)))
    if set(by_index) != expected:
        missing = sorted(expected.difference(by_index))
        raise SemanticProjectionError(
            f"semantic projection omitted input index(es): {missing}"
        )
    if [dict(row) for row in rows] != raw_rows:
        raise SemanticProjectionError("source rows changed during semantic projection")
    return [
        {"raw": copy.deepcopy(raw_rows[index]), "derived": by_index[index]}
        for index in range(len(raw_rows))
    ]
