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
    for index, row in enumerate(requirements):
        if not isinstance(row, dict) or set(row) not in ({"criterionId", "path", "type"}, {"criterionId", "resultId", "path", "type"}):
            raise ValueError("Output requirements need criterionId, path and type, plus resultId for host-declared results")
        if not isinstance(row['criterionId'], str) or row['criterionId'] not in criterion_ids or not isinstance(row['type'], str) or row['type'] not in {'number', 'string', 'boolean'}:
            raise ValueError("Output requirements must name a component criterion and a scalar result type")
        if not isinstance(row['path'], str) or not row['path'].startswith('/'):
            raise ValueError(f"Output requirements need exact RFC6901 pointers; outputRequirements[{index}].path received {row['path']!r}. Start with '/' and name the actual scalar model result (for example '/periods/0/metric').")
        if 'resultId' in row and (not isinstance(row['resultId'], str) or not row['resultId'].strip()):
            raise ValueError("resultId must name an independently required result")
        identity = (row['criterionId'], row['path'])
        if identity in seen:
            raise ValueError("Duplicate criterion output requirement")
        seen.add(identity)
        covered.add(row['criterionId'])
    if covered != set(criterion_ids):
        raise ValueError("Every component criterion needs typed result output requirements")


def required_result_binding_findings(bindings: Sequence[Mapping[str, Any]], criteria: Sequence[Mapping[str, Any]]) -> list[str]:
    """Use the staged host contract; the producer cannot weaken result types/counts.

    This is construction coverage, not semantic or arithmetic acceptance. A
    plausible number at the wrong pointer still needs independent review.
    """
    findings = []
    for criterion in criteria:
        if 'results' not in criterion:
            continue  # Existing durable plans retain their original contract.
        results = criterion['results']
        if not isinstance(results, list) or not results:
            raise ValueError('Host result contract is malformed')
        known = {}
        for result in results:
            if (not isinstance(result, dict) or set(result) != {'id', 'description', 'type', 'minimumCount'}
                    or not isinstance(result['id'], str) or not result['id'].strip() or result['id'] in known
                    or not isinstance(result['description'], str) or not result['description'].strip()
                    or not isinstance(result['type'], str) or result['type'] not in {'number', 'string', 'boolean'}
                    or type(result['minimumCount']) is not int or not 1 <= result['minimumCount'] <= 2**53 - 1):
                raise ValueError('Host result contract is malformed')
            known[result['id']] = result
        rows = [row for row in bindings if isinstance(row, Mapping) and row.get('criterionId') == criterion['id']]
        paths = set()
        for row in rows:
            result = known.get(row['resultId']) if isinstance(row.get('resultId'), str) else None
            if result is None or row.get('type') != result['type'] or not isinstance(row.get('path'), str) or not row['path'].startswith('/'):
                findings.append(f"Criterion {criterion['id']}: bind the independently required result IDs and types; status/count substitutes cannot redefine the method.")
            elif row['path'] in paths:
                findings.append(f"Criterion {criterion['id']}: duplicate result path {row['path']}; distinct required results need distinct leaves.")
            else:
                paths.add(row['path'])
        for result in results:
            count = len({row['path'] for row in rows if row.get('resultId') == result['id'] and row.get('type') == result['type'] and isinstance(row.get('path'), str) and row['path'].startswith('/')})
            if count < result['minimumCount']:
                findings.append(f"Criterion {criterion['id']}: {result['description']} requires at least {result['minimumCount']} distinct {result['type']} results ({result['id']}); declared {count}.")
    return findings


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
