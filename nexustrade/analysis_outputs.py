"""Explicit criterion-to-result contracts. No task semantics are inferred."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .report_json import _resolve_pointer


def validate_output_requirements(requirements: Any, criterion_ids: Sequence[str]) -> None:
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("Declare outputRequirements: criterionId, path and type for actual result leaves, not section containers")
    covered = set()
    seen = set()
    for row in requirements:
        if not isinstance(row, dict) or set(row) != {"criterionId", "path", "type"}:
            raise ValueError("Output requirements need exactly criterionId, path and type")
        if not isinstance(row['criterionId'], str) or row['criterionId'] not in criterion_ids or not isinstance(row['type'], str) or row['type'] not in {'number', 'string', 'boolean'}:
            raise ValueError("Output requirements must name a component criterion and a scalar result type")
        if not isinstance(row['path'], str) or not row['path'].startswith('/'):
            raise ValueError("Output requirements need exact RFC6901 pointers")
        identity = (row['criterionId'], row['path'])
        if identity in seen:
            raise ValueError("Duplicate criterion output requirement")
        seen.add(identity)
        covered.add(row['criterionId'])
    if covered != set(criterion_ids):
        raise ValueError("Every component criterion needs typed result output requirements")


def missing_required_outputs(model: Any, requirements: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    missing = []
    for row in requirements:
        try:
            value = _resolve_pointer(model, row['path'])
        except (KeyError, IndexError, ValueError):
            value = None
        valid = (row['type'] == 'number' and type(value) in {int, float} and math.isfinite(value)
                 or row['type'] == 'string' and isinstance(value, str) and bool(value.strip())
                 or row['type'] == 'boolean' and type(value) is bool)
        if not valid:
            missing.append({**row, 'reason': 'missing_or_null' if value is None else 'wrong_type_or_empty'})
    return missing
