"""Local retained-source discovery. No retrieval, model calls or accounting choices.

Inventories are navigation, not evidence of request completeness. Original source
bytes and SEC field names remain unchanged; use the returned paths in producers.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping

MANIFEST_PATH = "/work/in/data/research-inputs/manifest.json"
ResponseFamily = Literal["statement", "fact_candidates", "fact_instances", "filed_concepts", "dimensioned_concepts", "business_breakdowns", "filing"]


def _path(value: str) -> Path:
    """Resolve a /work or relative path inside the current workspace, refusing escapes and credentials."""
    root = Path(os.environ.get("NEXUSTRADE_WORK_DIR", "/work")).resolve()
    path = Path(value)
    if path.is_relative_to("/work"):
        path = root / path.relative_to("/work")
    elif not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("Research paths must stay inside the current workspace")
    if path.name in {".jobenv", ".session_env"} or path == root / ".env":
        raise ValueError("Research paths cannot read host-private credential files")
    return path


def _document(path: str) -> dict[str, Any]:
    value = json.loads(_path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object at {path}")
    return value


def _sources() -> list[dict[str, Any]]:
    manifest = _document(MANIFEST_PATH)
    sources = manifest.get("sources")
    if manifest.get("schemaVersion") != 1 or not isinstance(sources, list):
        raise ValueError("Unsupported retained research manifest; no partial inventory")
    if any(not isinstance(s, dict) or not isinstance(s.get("id"), str) for s in sources):
        raise ValueError("Research sources need explicit string identities")
    if len({s['id'] for s in sources}) != len(sources):
        raise ValueError("Duplicate retained source IDs; selection is ambiguous")
    return sources


def _source(source_id: str) -> dict[str, Any]:
    sources = _sources()
    for row in sources:
        if row['id'] == source_id:
            return row
    ids = [s['id'] for s in sources]
    suggestions = difflib.get_close_matches(source_id, ids, n=8, cutoff=0.3)
    raise ValueError(f"Unknown retained source ID {source_id!r}. Actual candidate IDs: {suggestions!r}. "
                     "Use research.catalog(query=...) to inspect identities/URLs; no source was substituted.")


def _page(items: list[Any], offset: int, limit: int) -> dict[str, Any]:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("offset must be nonnegative and limit between 1 and 100")
    end = min(len(items), offset + limit)
    return {"items": items[offset:end], "total": len(items), "offset": offset,
            "nextOffset": end if end < len(items) else None}


def catalog(*, query: str = "", kind: str | None = None, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    """Page exact IDs/URLs, file layouts and response types; never dump raw evidence.

    query filters actual identity/URL/table/response metadata text, without
    inventing aliases or selecting a source for the analyst. Follow nextOffset.
    """
    if kind not in {None, 'fetch', 'sec', 'lake'} or not isinstance(query, str):
        raise ValueError("kind must be fetch, sec or lake; query must be text")
    selected = []
    for s in _sources():
        searchable = {key: s.get(key) for key in ('id', 'url', 'finalUrl', 'tables', 'responseMetadata', 'responseFamily')}
        if (kind is not None and s.get('kind') != kind) or query.casefold() not in json.dumps(searchable).casefold():
            continue
        selected.append({key: s[key] for key in ('id', 'kind', 'url', 'finalUrl', 'contentType', 'responseFamily', 'path', 'tables', 'rowCount') if key in s} |
                        {"fileCount": len(s.get('parts', [])) if s.get('kind') == 'lake' else 1})
    return {**_page(selected, offset, limit), "requestCoverage": "not_assessed", "sourceAbsenceEstablished": False}


def files(source_id: str) -> list[dict[str, Any]]:
    """All complete local files, including every lake part. No singular-path guess."""
    s = _source(source_id)
    if s.get('kind') == 'lake':
        parts = s.get('parts')
        if not isinstance(parts, list) or (s.get('rowCount', 0) > 0 and not parts):
            raise ValueError(f"Source {source_id!r} has no complete lake parts")
        result = [dict(p, contentType='application/vnd.apache.parquet') for p in parts]
    elif s.get('kind') in {'fetch', 'sec'}:
        result = [{key: s[key] for key in ('path', 'bytes', 'sha256', 'contentType') if key in s}]
    else:
        raise ValueError(f"Unsupported source kind for {source_id!r}")
    for item in result:
        if not isinstance(item.get('path'), str):
            raise ValueError(f"Source {source_id!r} has a file without a path")
        _path(item['path'])  # Enforce the same workspace boundary as producers.
    return result


def response(source_id: str, *, expected: ResponseFamily | None = None) -> dict[str, Any]:
    """Read the complete unchanged SEC response, optionally checking its family.

    Concepts describe tags; they are not numeric facts. fact_instances retains
    raw Notes names (value_raw/ddate/uom), statements retain normalized names.
    This does not assert cutoff coverage or choose a context/accounting role.
    """
    s = _source(source_id)
    if s.get('kind') != 'sec':
        raise ValueError(f"{source_id!r} is {s.get('kind')!r}, not a SEC response; use research.files().")
    data = _document(files(source_id)[0]['path'])
    family = _family(data)
    if expected is not None and family != expected:
        collection = 'candidates' if 'candidates' in data else 'rows'
        fields = sorted({key for r in data.get(collection, []) if isinstance(r, dict) for key in r})
        advice = ("Concept inventories contain metadata, not financial values. Inspect retained fact_instances/statement sources "
                  if family in {'filed_concepts', 'dimensioned_concepts'} else "Inspect this response's declared query/field contract ")
        raise ValueError(f"SEC response family mismatch: {source_id!r} is {family!r}, expected {expected!r}; "
                         f"status={data.get('status')!r}, available row fields={fields!r}. "
                         + advice +
                         "or retrieve only the exact uncovered concepts. This is not evidence of source absence.")
    return data


def _family(data: Mapping[str, Any]) -> str:
    # action is the query's declared contract, including empty/truncated results.
    action = data.get('action')
    if action in {'fact_instances', 'filed_concepts', 'dimensioned_concepts', 'business_breakdowns', 'filing'}:
        return str(action)
    if 'candidates' in data and 'reconciliation' in data:
        return 'fact_candidates'
    if 'cadence' in data and 'periods_requested' in data and 'rows' in data:
        return 'statement'
    return 'unknown'


def rows(source_id: str, *, expected: ResponseFamily, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    """Page complete records with exact response selectors; no field normalization."""
    data = response(source_id, expected=expected)
    collection = 'candidates' if expected == 'fact_candidates' else 'rows'
    items = data.get(collection)
    if not isinstance(items, list):
        raise ValueError(f"{source_id!r} has no {collection!r} collection")
    page = _page(items, offset, limit)
    page.update(sourceId=source_id, responseFamily=expected, collection=collection,
                selectors=[[collection, i] for i in range(offset, offset + len(page['items']))],
                fields=sorted({key for r in items if isinstance(r, dict) for key in r}),
                limitations=data.get('limitations', []), truncated=data.get('truncated', False),
                filingPlanTruncated=data.get('filingPlanTruncated', False), sourceAbsenceEstablished=False)
    return page


def coverage(source_id: str, *, collection: str | None = None, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    """Summary, or one paged row set, without reading the full inventory into context."""
    _source(source_id)
    manifest = _document(MANIFEST_PATH)
    document = _document(manifest.get('coveragePath', '/work/in/data/research-inputs/coverage.json'))
    matches = [s for s in document.get('sources', []) if s.get('id') == source_id]
    if len(matches) != 1:
        raise ValueError(f"No unique retained coverage entry for {source_id!r}; do not infer missing disclosures")
    source = matches[0]
    sets = source.get('rowSets', [])
    if collection is None:
        return {key: value for key, value in source.items() if key != 'rowSets'} | {
            'rowSets': [{key: value for key, value in row_set.items() if key != 'rows'} for row_set in sets]}
    selected = [row_set for row_set in sets if row_set['key'] == collection]
    if len(selected) != 1:
        raise ValueError(f"Unknown coverage collection {collection!r}; available={[s['key'] for s in sets]!r}")
    return {**_page(selected[0]['rows'], offset, limit), 'sourceId': source_id,
            'collection': collection, 'sourceAbsenceEstablished': False}


def format(source_id: str) -> dict[str, Any]:
    """Identify local container magic and declared content type, never its suffix.

    A ZIP container is not automatically a spreadsheet; inspect its members with
    the appropriate parser. A content type is recorded metadata, not a parser verdict.
    """
    result = []
    for item in files(source_id):
        with _path(item['path']).open('rb') as f:
            head = f.read(8)
        container = ('pdf' if head.startswith(b'%PDF-') else 'parquet' if head.startswith(b'PAR1')
                     else 'zip' if head.startswith(b'PK\x03\x04') else 'unrecognized')
        result.append({**item, 'container': container, 'magicHex': head.hex()})
    return {'sourceId': source_id, 'files': result}
