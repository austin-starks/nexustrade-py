"""Explicit criterion-to-result contracts. No task semantics are inferred."""
from __future__ import annotations

import math
import json
from typing import Any, Mapping, Sequence

from .report_json import _resolve_pointer

_UNSET = object()


def _coordinate_key(row: Mapping[str, Any]) -> str:
    # JSON numbers have one type in the host; 2020 and 2020.0 are identical.
    normalized = [(key, int(v) if type(v) is float and math.isfinite(v) and v.is_integer() else v) for key, v in sorted(row.items())]
    return json.dumps(normalized, separators=(',', ':'), ensure_ascii=False)


def _validate_coverage(value: Any, minimum_count: int) -> None:
    if not isinstance(value, list) or not value or len(value) > minimum_count:
        raise ValueError('Result coverage needs explicit coordinates within minimumCount')
    dimensions = None
    seen = set()
    types = {}
    for row in value:
        if not isinstance(row, dict) or not row or any(not isinstance(key, str) or not key.strip() for key in row):
            raise ValueError('Result coverage needs nonempty coordinate objects')
        if dimensions is not None and set(row) != dimensions:
            raise ValueError('Coverage coordinates must use the same dimensions')
        dimensions = set(row)
        for key, v in row.items():
            kind = 'number' if type(v) in {int, float} else 'string' if isinstance(v, str) else None
            if not (kind == 'number' and math.isfinite(v) or kind == 'string' and v.strip()) or key in types and types[key] != kind:
                raise ValueError('Coverage values must be consistent nonempty strings or finite numbers')
            types[key] = kind
        identity = _coordinate_key(row)
        if identity in seen:
            raise ValueError('Duplicate required coverage coordinate')
        seen.add(identity)


def validate_output_requirements(requirements: Any, criterion_ids: Sequence[str]) -> None:
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("Declare outputRequirements: criterionId, path and type for actual result leaves, not section containers")
    covered = set()
    seen = set()
    for index, row in enumerate(requirements):
        if not isinstance(row, dict) or set(row) not in ({"criterionId", "path", "type"}, {"criterionId", "resultId", "path", "type"}, {"criterionId", "resultId", "path", "type", "coveragePaths"}):
            raise ValueError("Output requirements need criterionId, path and type, plus resultId for host-declared results")
        if not isinstance(row['criterionId'], str) or row['criterionId'] not in criterion_ids or not isinstance(row['type'], str) or row['type'] not in {'number', 'string', 'boolean'}:
            raise ValueError("Output requirements must name a component criterion and a scalar result type")
        if not isinstance(row['path'], str) or not row['path'].startswith('/'):
            raise ValueError(f"Output requirements need exact RFC6901 pointers; outputRequirements[{index}].path received {row['path']!r}. Start with '/' and name the actual scalar model result (for example '/periods/0/metric').")
        if 'resultId' in row and (not isinstance(row['resultId'], str) or not row['resultId'].strip()):
            raise ValueError("resultId must name an independently required result")
        if 'coveragePaths' in row and (not isinstance(row['coveragePaths'], dict) or not row['coveragePaths'] or any(not isinstance(key, str) or not key.strip() or not isinstance(path, str) or not path.startswith('/') for key, path in row['coveragePaths'].items())):
            raise ValueError('coveragePaths must bind coordinate dimensions to exact model pointers')
        identity = (row['criterionId'], row['path'])
        if identity in seen:
            raise ValueError("Duplicate criterion output requirement")
        seen.add(identity)
        covered.add(row['criterionId'])
    if covered != set(criterion_ids):
        raise ValueError("Every component criterion needs typed result output requirements")


def required_result_binding_findings(bindings: Sequence[Mapping[str, Any]], criteria: Sequence[Mapping[str, Any]], model: Any = _UNSET, *, complete: bool = True) -> list[str]:
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
            if (not isinstance(result, dict) or set(result) - {'id', 'description', 'type', 'minimumCount', 'coverage', 'cardinality', 'coverageRequirement'}
                    or not {'id', 'description', 'type', 'minimumCount'} <= set(result)
                    or not isinstance(result['id'], str) or not result['id'].strip() or result['id'] in known
                    or not isinstance(result['description'], str) or not result['description'].strip()
                    or not isinstance(result['type'], str) or result['type'] not in {'number', 'string', 'boolean'}
                    or type(result['minimumCount']) is not int or not 1 <= result['minimumCount'] <= 2**53 - 1):
                raise ValueError('Host result contract is malformed')
            cardinality = result.get('cardinality')
            if ('cardinality' in result and cardinality not in ('single', 'series')
                    or cardinality == 'single' and result['minimumCount'] != 1
                    or cardinality == 'series' and result['minimumCount'] < 2):
                raise ValueError('A single result needs minimumCount 1; a series needs at least 2 distinct results')
            if 'coverage' in result:
                _validate_coverage(result['coverage'], result['minimumCount'])
            scope = result.get('coverageRequirement')
            if 'coverageRequirement' in result and (not isinstance(scope, dict)
                    or set(scope) != {'dimension', 'minimumDistinct', 'sourceExcerpt'}
                    or not isinstance(scope['dimension'], str) or not scope['dimension'].strip()
                    or type(scope['minimumDistinct']) is not int or not 2 <= scope['minimumDistinct'] <= result['minimumCount']
                    or not isinstance(scope['sourceExcerpt'], str) or not scope['sourceExcerpt'].strip() or len(scope['sourceExcerpt']) > 800
                    or cardinality != 'series'):
                raise ValueError('Counted result coverage needs a dimension, sourceExcerpt and minimumDistinct within minimumCount; do not shrink the requested horizon')
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
            if complete and count < result['minimumCount']:
                findings.append(f"Criterion {criterion['id']}: {result['description']} requires at least {result['minimumCount']} distinct {result['type']} results ({result['id']}); declared {count}.")
            scope = result.get('coverageRequirement')
            if scope:
                observed = set()
                coordinate_type = None
                for row in rows:
                    if row.get('resultId') != result['id']:
                        continue
                    selector = row.get('coveragePaths', {}).get(scope['dimension'])
                    if not isinstance(selector, str) or not selector.startswith('/'):
                        findings.append(f"Criterion {criterion['id']}: {result['id']} needs coveragePaths for {scope['dimension']} into the executed model.")
                        continue
                    if model is _UNSET:
                        continue
                    try:
                        value = _resolve_pointer(model, selector)
                    except (KeyError, IndexError, ValueError, TypeError):
                        value = None
                    if not (isinstance(value, str) and value.strip() or type(value) in {int, float} and math.isfinite(value)):
                        findings.append(f"Criterion {criterion['id']}: {result['id']} needs a real {scope['dimension']} coordinate.")
                        continue
                    identity = _coordinate_key({scope['dimension']: value})
                    value_type = 'string' if isinstance(value, str) else 'number'
                    if coordinate_type is not None and coordinate_type != value_type:
                        findings.append(f"Criterion {criterion['id']}: {result['id']} mixes {scope['dimension']} coordinate types.")
                    coordinate_type = value_type
                    if identity in observed:
                        findings.append(f"Criterion {criterion['id']}: {result['id']} repeats {scope['dimension']} {value}.")
                    observed.add(identity)
                if complete and model is not _UNSET and len(observed) < scope['minimumDistinct']:
                    findings.append(f"Criterion {criterion['id']}: {result['id']} requires {scope['minimumDistinct']} distinct {scope['dimension']} observations; found {len(observed)}.")
            if 'coverage' not in result:
                continue
            dimensions = set(result['coverage'][0])
            expected = {_coordinate_key(coordinate) for coordinate in result['coverage']}
            observed = set()
            for row in rows:
                if row.get('resultId') != result['id']:
                    continue
                selectors = row.get('coveragePaths')
                if not isinstance(selectors, dict) or set(selectors) != dimensions or any(not isinstance(path, str) or not path.startswith('/') for path in selectors.values()):
                    findings.append(f"Criterion {criterion['id']}: {result['id']} needs coveragePaths for {', '.join(sorted(dimensions))} into the executed model.")
                    continue
                if model is _UNSET:
                    continue
                try:
                    identity = _coordinate_key({key: _resolve_pointer(model, selectors[key]) for key in dimensions})
                except (KeyError, IndexError, ValueError, TypeError):
                    findings.append(f"Criterion {criterion['id']}: {result['id']} coveragePaths do not resolve.")
                    continue
                if identity not in expected:
                    findings.append(f"Criterion {criterion['id']}: {result['id']} bound an outside-scope coordinate {identity}.")
                elif identity in observed:
                    findings.append(f"Criterion {criterion['id']}: {result['id']} repeats coordinate {identity}; duplicate rows cannot replace missing coverage.")
                else:
                    observed.add(identity)
            if complete and model is not _UNSET:
                for coordinate in sorted(expected - observed):
                    findings.append(f"Criterion {criterion['id']}: {result['id']} is missing required coverage {coordinate}; execute and bind that requested result.")
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
