"""Mistral OCR and schema-bound extraction for scanned PDFs."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MISTRAL_OCR_MODEL = "mistral-ocr-latest"
_MISTRAL_POOR_CONFIDENCE = 0.70
_MISTRAL_FAIR_CONFIDENCE = 0.85

_LOW_CONFIDENCE_GRADES = frozenset({"POOR", "FAIR"})


class OcrBudgetExhausted(RuntimeError):
    """Raised when the sandbox OCR gateway reports the job page budget is spent (HTTP 429)."""


def _resolve_mistral_model() -> str:
    configured = os.environ.get("SCAN_TABLE_MISTRAL_MODEL", "").strip()
    return configured or DEFAULT_MISTRAL_OCR_MODEL


def _tessdata_path() -> str:
    return os.environ.get("TESSDATA_PREFIX", "/opt/tessdata")


def header_to_snake_case(header: str) -> str:
    """Normalize a table header to a stable snake_case key."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", header.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "column"


class TargetSchemaError(ValueError):
    """Raised when target_schema is a shape we cannot interpret.

    Loud on purpose. Every model in the 2026-07 bake-off passed
    `{field: "description"}`; the old code iterated that description STRING
    character by character as if it were an alias list, producing a 20-entry map
    of single letters where later fields silently overwrote earlier ones. Every
    model got mangled output, none got an error, and all four abandoned
    extract_pdf and hand-rolled a regex parser instead.
    """


def _fields_from_sequence(seq: Any) -> dict[str, list[str]]:
    """Field specs from a list: ["ticker"] or [{"name": "ticker", ...}]."""
    out: dict[str, list[str]] = {}
    for item in seq:
        if isinstance(item, str):
            out[header_to_snake_case(item)] = []
            continue
        if isinstance(item, dict):
            name = item.get("name") or item.get("field") or item.get("column")
            if not name:
                raise TargetSchemaError(
                    f"field spec {item!r} has no 'name'/'field'/'column' key"
                )
            raw_aliases = item.get("aliases") or item.get("synonyms") or []
            out[header_to_snake_case(str(name))] = [str(a) for a in raw_aliases]
            continue
        raise TargetSchemaError(f"unsupported field spec: {item!r}")
    return out


def normalize_target_schema(target_schema: Any) -> dict[str, list[str]]:
    """Canonicalize target_schema to {field: [aliases]}.

    Accepts every shape callers actually pass:
      ["ticker", "amount"]                        - names only
      {"ticker": "Ticker symbol, else blank"}     - name -> DESCRIPTION (not aliases)
      {"ticker": ["symbol", "sym"]}               - name -> alias list
      {"ticker": {"aliases": ["symbol"]}}         - name -> spec object
      {"rows": [{"name": "ticker", ...}]}         - JSON-schema style wrapper
    Anything else raises rather than silently mangling the mapping.
    """
    if not target_schema:
        return {}
    if isinstance(target_schema, (list, tuple)):
        return _fields_from_sequence(target_schema)
    if not hasattr(target_schema, "items"):
        raise TargetSchemaError(
            f"target_schema must be a list or dict, got {type(target_schema).__name__}"
        )

    out: dict[str, list[str]] = {}
    for field, spec in target_schema.items():
        # Wrapper key holding real field specs, e.g. {"rows": [{"name": ...}]}.
        if (
            isinstance(spec, (list, tuple))
            and spec
            and all(isinstance(x, dict) for x in spec)
        ):
            out.update(_fields_from_sequence(spec))
            continue
        canonical = header_to_snake_case(str(field))
        if spec is None or isinstance(spec, str):
            # A human description of the column, NOT a list of aliases.
            out[canonical] = []
        elif isinstance(spec, (list, tuple)):
            out[canonical] = [str(a) for a in spec]
        elif isinstance(spec, dict):
            raw_aliases = spec.get("aliases") or spec.get("synonyms") or []
            out[canonical] = [str(a) for a in raw_aliases]
        else:
            raise TargetSchemaError(
                f"field {field!r} has unsupported spec {spec!r}; use a description "
                "string, a list of aliases, or {'aliases': [...]}"
            )
    return out


def build_schema_alias_map(target_schema: Any) -> dict[str, str]:
    """Map each snake_cased header/alias onto its canonical target field."""
    aliases: dict[str, str] = {}
    for field, synonyms in normalize_target_schema(target_schema).items():
        aliases[field] = field
        for synonym in synonyms:
            aliases[header_to_snake_case(str(synonym))] = field
    return aliases


def schema_fields(target_schema: Any) -> list[str]:
    """Canonical field names declared by a target schema."""
    return list(normalize_target_schema(target_schema).keys())


def _domain_from_spec(spec: Any) -> frozenset[str] | None:
    if not isinstance(spec, dict):
        return None
    raw = spec.get("values") or spec.get("enum") or spec.get("domain")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    return frozenset(str(v).strip().casefold() for v in raw)


def schema_value_domains(target_schema: Any) -> dict[str, frozenset[str]]:
    """Closed value sets a caller declared per field, e.g.

        {"transaction_type": {"values": ["P", "S", "E"]}}

    Optional and opt-in: a field with no declared domain is never checked. This is
    a CALLER-DECLARED contract, not a guess about what a column should hold — the
    extractor has no way to know that "02/12/2024" is wrong in a column unless the
    caller says which values are legal.

    Matching is case- and whitespace-insensitive because OCR case is unstable
    (House PTRs render "S" and encode "s").
    """
    if not target_schema:
        return {}
    specs: list[tuple[Any, Any]] = []
    if isinstance(target_schema, (list, tuple)):
        specs = [
            (item.get("name") or item.get("field") or item.get("column"), item)
            for item in target_schema
            if isinstance(item, dict)
        ]
    elif hasattr(target_schema, "items"):
        for field, spec in target_schema.items():
            if (
                isinstance(spec, (list, tuple))
                and spec
                and all(isinstance(x, dict) for x in spec)
            ):
                specs.extend(
                    (x.get("name") or x.get("field") or x.get("column"), x)
                    for x in spec
                )
            else:
                specs.append((field, spec))
    out: dict[str, frozenset[str]] = {}
    for name, spec in specs:
        if not name:
            continue
        domain = _domain_from_spec(spec)
        if domain:
            out[header_to_snake_case(str(name))] = domain
    return out


def flag_domain_violations(
    rows: list[dict[str, Any]],
    domains: dict[str, frozenset[str]],
) -> int:
    """Mark rows whose extracted value falls outside a declared domain.

    A structuring model can put an adjacent column's value into a field — a
    transaction date landed in a P/S/E `transaction_type` column on one filing of
    62, and downstream `type == "P"` classification silently reclassified two real
    purchases as non-purchases. They were not counted as extracted, not counted as
    rejected, and not counted as unresolved: the whole filing left no trace.

    A domain violation is REVIEW, not deletion — the value is preserved and the
    row is flagged, so `rowsNeedingReview` carries it to the grader instead of the
    row disappearing. Empty/None is not a violation: declared fields legitimately
    default to None when a page has no such column.
    """
    if not domains:
        return 0
    flagged = 0
    for row in rows:
        offenders = [
            field
            for field, domain in domains.items()
            if (value := row.get(field)) is not None
            and str(value).strip()
            and str(value).strip().casefold() not in domain
        ]
        if not offenders:
            continue
        row["_needs_review"] = True
        row.setdefault("_reason", f"value_outside_declared_domain:{offenders[0]}")
        flagged += 1
    return flagged


def apply_row_schema(
    row: dict[str, Any],
    alias_map: dict[str, str],
    fields: list[str],
) -> dict[str, Any]:
    """Snake_case keys, fold aliases onto canonical fields, never drop unmapped columns.

    Applied to every extracted row so output shape stays stable. Metadata keys (leading
    underscore) pass through.
    """
    out: dict[str, Any] = {}
    for key, value in row.items():
        if str(key).startswith("_"):
            out[key] = value
            continue
        normalized = header_to_snake_case(str(key))
        out[alias_map.get(normalized, normalized)] = value
    for field in fields:
        out.setdefault(field, None)
    return out


def inject_extra_fields(
    row: dict[str, Any],
    extra_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    """Stamp caller-supplied constants (doc id, source url, filing date) onto a row.

    Extracted values win: document-level metadata never overwrites what is on the page.
    """
    if not extra_fields:
        return row
    for key, value in extra_fields.items():
        if value is not None:
            row.setdefault(header_to_snake_case(str(key)), value)
    return row


def text_layer_is_suspect(sample: str) -> tuple[bool, int, int]:
    """Detect a present-but-unusable PDF text layer. Returns (suspect, bad_chars, scrambled).

    A PDF can carry a full text layer that decodes to garbage: House PTRs render "AAPL"
    while encoding "aaPl", and broken ToUnicode maps decode to U+FFFD/U+FFFE or the
    private-use area. 62 of the 64 Pelosi PTRs trip this, so `has_text_layer` alone is a
    trap — it is what led every bake-off model to regex a corrupt layer.
    """
    bad_chars = sum(
        1 for ch in sample if ch in "\ufffd\ufffe\uffff" or "\ue000" <= ch <= "\uf8ff"
    )
    scrambled = len(re.findall(r"\b[a-z]+[A-Z]", sample))
    return (bad_chars >= 3 or scrambled >= 3, bad_chars, scrambled)


def page_needs_review(grade: str | None, row_count: int) -> bool:
    """Return True when Mistral output for a page should be flagged for human review."""
    if row_count <= 0:
        return True
    return grade in _LOW_CONFIDENCE_GRADES


def _review_reason(
    *,
    page_index: int,
    grade: str | None,
    row_count: int,
    failed_pages: set[int],
) -> str | None:
    if page_index in failed_pages and row_count <= 0:
        return "mistral_zero_rows"
    if page_index in failed_pages:
        return "mistral_table_parse_failed"
    if row_count <= 0:
        return "zero_rows"
    if grade in _LOW_CONFIDENCE_GRADES:
        return "low_confidence"
    return None


def _mistral_grade_from_confidence(confidence: dict[str, Any] | None) -> str | None:
    """Map Mistral page confidence scores onto escalation grades (POOR/FAIR/GOOD)."""
    if not confidence:
        return None
    # Use the AVERAGE, not the minimum. `minimum_page_confidence_score` is the min over every
    # word on the page, so a single bad glyph (bullet, checkbox, scan artifact) drags it to
    # ~0.25 on pages whose average is 0.98. Grading on the minimum marked every real PTR page
    # POOR, escalated all of them to vision, and silently discarded Mistral's own rows.
    # We now flag those pages `_needs_review` instead of calling a second parser.
    score = confidence.get("average_page_confidence_score")
    if not isinstance(score, (int, float)):
        score = confidence.get("minimum_page_confidence_score")
    if not isinstance(score, (int, float)):
        return None
    if score < _MISTRAL_POOR_CONFIDENCE:
        return "POOR"
    if score < _MISTRAL_FAIR_CONFIDENCE:
        return "FAIR"
    return "GOOD"


def _parse_markdown_table(text: str) -> list[dict[str, Any]]:
    """Parse a GitHub-flavored markdown pipe table into row dicts."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].startswith("|"):
        return []
    headers = [
        header_to_snake_case(cell.strip())
        for cell in lines[0].strip("|").split("|")
    ]
    data_start = 2 if re.match(r"^\|?[\s\-:|]+\|?$", lines[1]) else 1
    rows: list[dict[str, Any]] = []
    for line in lines[data_start:]:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        row: dict[str, Any] = {}
        empty = True
        for key, cell in zip(headers, cells, strict=False):
            text_val = cell.strip()
            if text_val:
                empty = False
                row[key] = text_val
            else:
                row[key] = None
        if not empty:
            rows.append(row)
    return rows


def _extract_markdown_tables_from_text(text: str) -> list[dict[str, Any]]:
    """Find and parse every markdown pipe table embedded in page markdown.

    Only reachable when a page carries an inline pipe table. Mistral does NOT do
    that for these documents — the page markdown holds a REFERENCE
    (`[tbl-0.md](tbl-0.md)`) and zero pipe rows, with the real table under
    `page["tables"]`. Kept for sources that do inline their tables; it is not a
    fallback for the Mistral path.
    """
    rows: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        if "|" not in block:
            continue
        parsed = _parse_markdown_table(block)
        if parsed:
            rows.extend(parsed)
    return rows


def _parse_html_table_rows(html: str) -> list[dict[str, Any]]:
    """Parse HTML table markup into row dicts (pandas when available)."""
    try:
        import pandas as pd
    except ImportError:
        return []
    try:
        frames = pd.read_html(io.StringIO(html))
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for frame in frames:
        rows.extend(
            dataframe_to_rows(frame, page_index=0, page_grade=None, source="mistral")
        )
    return rows


def _table_text_to_rows(table_text: str, *, format_hint: str) -> list[dict[str, Any]]:
    stripped = table_text.strip()
    if not stripped:
        return []
    if format_hint == "html" or stripped.startswith("<"):
        return _parse_html_table_rows(stripped)
    return _parse_markdown_table(stripped)


def _mistral_page_rows(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract table rows from one Mistral OCR page payload."""
    rows: list[dict[str, Any]] = []
    tables = page.get("tables")
    if isinstance(tables, list):
        for table in tables:
            if not isinstance(table, dict):
                continue
            content = (
                table.get("content")
                or table.get("markdown")
                or table.get("html")
                or ""
            )
            if not isinstance(content, str) or not content.strip():
                continue
            fmt = str(table.get("format") or "markdown")
            parsed = _table_text_to_rows(content, format_hint=fmt)
            for row in parsed:
                row.pop("_extract_source", None)
                row.pop("_page_index", None)
                row.pop("_page_grade", None)
            rows.extend(parsed)
    if rows:
        return rows
    markdown = page.get("markdown")
    if isinstance(markdown, str) and markdown.strip():
        return _extract_markdown_tables_from_text(markdown)
    return []


def build_ocr_request_body(
    *,
    document_url: str,
    page_count: int,
    total_pages: int | None = None,
    table_format: str | None = "markdown",
) -> dict[str, Any]:
    """Build the Mistral OCR request body.

    Restricts OCR to the pages we keep: Mistral bills per page PROCESSED, so sending a whole
    document and then discarding the tail paid for pages we threw away. `pages` is 0-based.
    """
    body: dict[str, Any] = {
        "model": _resolve_mistral_model(),
        "document": {"type": "document_url", "document_url": document_url},
        "_sandbox_expected_pages": page_count,
    }
    if total_pages is not None and 0 < page_count < total_pages:
        body["pages"] = list(range(page_count))
    if table_format is not None:
        body["table_format"] = table_format
        body["confidence_scores_granularity"] = "page"
    return body


def _mistral_ocr_document(
    pdf_bytes: bytes,
    *,
    page_count: int,
    total_pages: int | None = None,
    table_format: str | None = "markdown",
) -> dict[str, Any]:
    """Run Mistral OCR on a PDF via the sandbox gateway (/ocr)."""
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not api_key:
        raise RuntimeError("_mistral_ocr_document requires OPENAI_BASE_URL and OPENAI_API_KEY")

    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    body = build_ocr_request_body(
        document_url=f"data:application/pdf;base64,{b64}",
        page_count=page_count,
        total_pages=total_pages,
        table_format=table_format,
    )
    req = urllib.request.Request(
        f"{base}/ocr",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 429:
            raise OcrBudgetExhausted(
                f"OCR page budget exhausted at the gateway: {detail}"
            ) from exc
        raise RuntimeError(f"mistral OCR HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("mistral OCR response was not a JSON object")
    return payload


def _mistral_rows_by_page(
    pdf_bytes: bytes,
    *,
    page_count: int,
    total_pages: int | None = None,
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, str | None], set[int]]:
    """Run Mistral OCR and return per-page table rows + confidence grades."""
    payload = _mistral_ocr_document(
        pdf_bytes, page_count=page_count, total_pages=total_pages
    )
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise RuntimeError("mistral OCR response missing pages")

    by_page: dict[int, list[dict[str, Any]]] = {i: [] for i in range(page_count)}
    grades: dict[int, str | None] = dict.fromkeys(range(page_count), None)
    failed_pages: set[int] = set()

    for page in pages:
        if not isinstance(page, dict):
            continue
        page_index = page.get("index")
        if not isinstance(page_index, int) or page_index < 0 or page_index >= page_count:
            continue
        confidence = page.get("confidence_scores")
        grade = _mistral_grade_from_confidence(
            confidence if isinstance(confidence, dict) else None
        )
        grades[page_index] = grade
        try:
            page_rows = _mistral_page_rows(page)
        except Exception:
            failed_pages.add(page_index)
            continue
        if not page_rows:
            failed_pages.add(page_index)
        for row in page_rows:
            row["_extract_source"] = "mistral"
            row["_page_index"] = page_index
            if grade is not None:
                row["_page_grade"] = grade
            by_page[page_index].append(row)
    return by_page, grades, failed_pages


def dataframe_to_rows(
    df: Any,
    *,
    page_index: int,
    page_grade: str | None,
    source: str = "mistral",
) -> list[dict[str, Any]]:
    """Convert a table dataframe into row dicts with extraction metadata."""
    rows: list[dict[str, Any]] = []
    columns = [header_to_snake_case(str(col)) for col in df.columns]
    for record in df.to_dict(orient="records"):
        row: dict[str, Any] = {}
        empty = True
        for key, col in zip(columns, df.columns, strict=True):
            value = record[col]
            text = "" if value is None else str(value).strip()
            if not text or text.lower() == "nan":
                # Keep the key (as None) so row shape stays stable across pages and engines.
                row[key] = None
                continue
            row[key] = text
            empty = False
        if empty:
            continue
        row["_extract_source"] = source
        row["_page_index"] = page_index
        if page_grade is not None:
            row["_page_grade"] = page_grade
        rows.append(row)
    return rows


def ocr_png_bytes(png_bytes: bytes) -> str:
    """Run liteparse OCR on PNG bytes (best-effort; returns empty string on failure)."""
    try:
        from liteparse import LiteParse

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(png_bytes)
            path = handle.name
        try:
            parser = LiteParse(quiet=True, tessdata_path=_tessdata_path())
            result = parser.parse(path, ocr_enabled=True, output_format="text")
            text = getattr(result, "text", None)
            return text if isinstance(text, str) else str(result)
        finally:
            os.unlink(path)
    except Exception:
        return ""


def _png_from_pdf_page(pdf_bytes: bytes, page_index: int, scale: float = 2.0) -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    if page_index < 0 or page_index >= len(doc):
        raise IndexError(f"page_index {page_index} out of range (pages={len(doc)})")
    page = doc[page_index]
    bitmap = page.render(scale=scale)
    pil = bitmap.to_pil()
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()


def _unresolved_page_marker(
    page_index: int,
    grade: str | None,
    reason: str,
) -> dict[str, Any]:
    """Sentinel row so a page we could not fully extract is never silently absent.

    Metadata-only (every key underscore-prefixed) so callers can filter it out, but a page
    that errored can always be counted rather than quietly vanishing.
    """
    return {
        "_extract_source": "unresolved",
        "_page_index": page_index,
        "_page_grade": grade,
        "_needs_review": True,
        "_reason": reason,
    }


def _assemble_mistral_extract_rows(
    *,
    limit: int,
    alias_map: dict[str, str],
    fields: list[str],
    extra_fields: dict[str, Any] | None,
    by_page: dict[int, list[dict[str, Any]]],
    grades: dict[int, str | None],
    failed_pages: set[int],
) -> list[dict[str, Any]]:
    """Normalize Mistral rows and flag thin pages — no secondary LLM parser."""
    all_rows: list[dict[str, Any]] = []
    for page_index in range(limit):
        page_rows = [
            apply_row_schema(row, alias_map, fields)
            for row in by_page.get(page_index, [])
        ]
        grade = grades.get(page_index)
        reason = _review_reason(
            page_index=page_index,
            grade=grade,
            row_count=len(page_rows),
            failed_pages=failed_pages,
        )
        if reason is not None:
            if page_rows:
                for row in page_rows:
                    row["_needs_review"] = True
                    row["_reason"] = reason
            else:
                all_rows.append(_unresolved_page_marker(page_index, grade, reason))
                continue
        for row in page_rows:
            inject_extra_fields(row, extra_fields)
        all_rows.extend(page_rows)
    return all_rows


def extract_pdf(
    pdf_bytes: bytes,
    extra_fields: dict[str, Any] | None = None,
    max_pages: int | None = None,
    target_schema: Any = None,
    source_id: str | None = None,
) -> list[dict[str, Any]]:
    """Extract rows from every page (or up to max_pages) of a PDF via Mistral OCR.

    Mistral parses table structure; thin pages (zero rows, parse failure, low confidence)
    are flagged `_needs_review` or emit an `_extract_source=unresolved` sentinel — never
    a second vision-model pass on the same page.

    Raises RuntimeError when the OCR backend itself is unreachable. Per-PAGE trouble is
    reported in the rows; a whole-DOCUMENT failure is raised, so an outage can never be
    mistaken for a document that simply scanned poorly.

    `target_schema` (dict of field -> aliases, or a sequence of field names) normalizes
    column keys. Unmapped columns are preserved; declared fields default to None.

    `source_id` (the host fetch key / filing id) and a host-owned zero-based
    `_source_row_index` are stamped onto every returned row, exactly as
    `extract_rows` does and as `extract_pdfs` does from the document key.
    The host reconciles emitted rows against its own fetch ledger by this field before
    grading, and blocks a run whose document-derived rows cannot be attributed. This
    function is the serial fallback when the batch path fails; it used to be the one
    member of the family with no way to stamp provenance, so falling back to it forced
    callers to hand-roll an id — positional indices like f"{sid}:{i}", which reconcile
    against nothing. Pass the same key you fetched the document under.

    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page_count = len(doc)
    finally:
        doc.close()
    limit = page_count if max_pages is None else min(page_count, max_pages)

    alias_map = build_schema_alias_map(target_schema)
    fields = schema_fields(target_schema)
    try:
        by_page, grades, failed_pages = _mistral_rows_by_page(
            pdf_bytes, page_count=limit, total_pages=page_count
        )
    except Exception as exc:
        # A whole-document OCR failure is infrastructure, not an unreadable page.
        # This used to return `unresolved` markers for every page, which the caller
        # is told to filter out — so a dead gateway looked exactly like a PDF that
        # OCR'd badly. A staged-but-undeployed MISTRAL_API_KEY 503'd every call for
        # 20 hours and read as "0 rows extracted": every model silently fell back to
        # regex over the raw text layer and lost the rows only OCR recovers, and the
        # run reported 0 rejected because nothing ever raised. Fail loudly instead.
        raise RuntimeError(
            f"extract_pdf: OCR backend unavailable ({exc}). This is an "
            "INFRASTRUCTURE failure, not a problem with this PDF — every page "
            "would fail the same way. Do NOT silently fall back to raw text "
            "extraction: report the failure so it can be fixed, and say in your "
            "summary that OCR was unavailable."
        ) from exc
    rows = _assemble_mistral_extract_rows(
        limit=limit,
        alias_map=alias_map,
        fields=fields,
        extra_fields=extra_fields,
        by_page=by_page,
        grades=grades,
        failed_pages=failed_pages,
    )
    # Opt-in per-field value domains. No-op unless the caller declared one; the
    # `rows_schema` path needs nothing here because JSON Schema `enum` already
    # constrains the model directly.
    flag_domain_violations(rows, schema_value_domains(target_schema))
    if source_id:
        for row_index, row in enumerate(rows):
            row["source_id"] = source_id
            row["_source_row_index"] = row_index
    return rows


def ocr_pdf_text(pdf_bytes: bytes, max_pages: int | None = None) -> str:
    """Rasterize and OCR every page without making a vision-model call."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    page_count = len(doc)
    limit = page_count if max_pages is None else min(page_count, max_pages)
    pages: list[str] = []
    for page_index in range(limit):
        text = ocr_png_bytes(_png_from_pdf_page(pdf_bytes, page_index))
        if text.strip():
            pages.append(f"--- PAGE {page_index + 1} ---\n{text.strip()}")
    return "\n\n".join(pages)


def probe_pdf(
    pdf_bytes: bytes,
    sample_pages: int = 3,
    sample_chars: int = 600,
) -> dict[str, Any]:
    """Cheap triage of a PDF — no OCR, no vision, no model weights.

    Lets a caller branch on facts instead of guessing: how big is it, is there a usable
    text layer, does it look scanned, and what does that text layer actually say. The
    `text_sample` matters because a PDF can be born-digital yet still carry a corrupt
    text layer (House PTRs render "AAPL" but encode "aaPl"), which is only visible by
    looking at it.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    pages = len(doc)
    limit = max(0, min(pages, sample_pages))
    chunks: list[str] = []
    for page_index in range(limit):
        try:
            textpage = doc[page_index].get_textpage()
            chunks.append(textpage.get_text_range() or "")
        except Exception:
            chunks.append("")
    sampled = "\n".join(chunks).strip()
    chars = len(sampled)
    per_page = (chars / limit) if limit else 0.0

    # A PDF can carry a full text layer that is still unusable: House PTRs render "AAPL"
    # but encode "aaPl", and some fonts have a broken ToUnicode map that decodes to U+FFFD.
    # Without this flag a caller sees has_text_layer=True and confidently parses garbage.
    suspect, replacement_chars, scrambled_tokens = text_layer_is_suspect(sampled)

    return {
        "pages": pages,
        "bytes": len(pdf_bytes),
        "sampled_pages": limit,
        "text_chars_sampled": chars,
        "chars_per_page": round(per_page, 1),
        "has_text_layer": chars > 0,
        "likely_scanned": per_page < 200,
        "text_layer_suspect": suspect,
        "replacement_chars": replacement_chars,
        "scrambled_tokens": scrambled_tokens,
        "text_sample": sampled[:sample_chars],
    }


def _apparent_markdown_table_rows(markdown: str) -> int:
    """Count visible table rows without treating ordinary prose as evidence."""
    pipe_rows = len(_extract_markdown_tables_from_text(markdown))
    html_rows = len(re.findall(r"<tr\b", markdown, flags=re.IGNORECASE))
    return pipe_rows + html_rows


def _mistral_page_markdown(page: dict[str, Any]) -> str:
    """Materialize page prose and separately-returned tables in one document."""
    markdown = page.get("markdown")
    parts = [markdown.strip()] if isinstance(markdown, str) and markdown.strip() else []
    tables = page.get("tables")
    if isinstance(tables, list):
        for table in tables:
            if not isinstance(table, dict):
                continue
            content = (
                table.get("content") or table.get("markdown") or table.get("html")
            )
            if not isinstance(content, str) or not content.strip():
                continue
            table_text = content.strip()
            if any(table_text in part for part in parts):
                continue
            parts.append(table_text)
    return "\n\n".join(parts)


def _mistral_document_markdown_with_audit(
    pdf_bytes: bytes, *, page_count: int
) -> tuple[str, list[dict[str, Any]]]:
    """Whole document markdown plus page confidence/coverage receipts."""
    # table_format=markdown is the already-proven confidence-enabled Mistral
    # request shape used by extract_pdf. The old prose path passed None and
    # discarded confidence_scores, making the structurer blind to OCR quality.
    payload = _mistral_ocr_document(
        pdf_bytes, page_count=page_count, table_format="markdown"
    )
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise RuntimeError("mistral OCR response missing pages")
    parts: list[str] = []
    audit: list[dict[str, Any]] = []
    pages_by_index: dict[int, dict[str, Any]] = {}
    duplicate_indexes: set[int] = set()
    invalid_pages = 0
    for page in pages:
        if not isinstance(page, dict):
            invalid_pages += 1
            continue
        page_index = page.get("index")
        if (
            not isinstance(page_index, int)
            or isinstance(page_index, bool)
            or page_index < 0
            or page_index >= page_count
        ):
            invalid_pages += 1
            continue
        if page_index in pages_by_index:
            duplicate_indexes.add(page_index)
            continue
        pages_by_index[page_index] = page

    for page_index in range(page_count):
        page = pages_by_index.get(page_index)
        if page is None:
            audit.append(
                {
                    "page_index": page_index,
                    "confidence_grade": None,
                    "average_confidence": None,
                    "minimum_confidence": None,
                    "markdown_chars": 0,
                    "apparent_table_rows": 0,
                    "needs_review": True,
                    "reason": "missing_ocr_page",
                }
            )
            continue
        text = _mistral_page_markdown(page)
        confidence = page.get("confidence_scores")
        confidence_dict = confidence if isinstance(confidence, dict) else None
        grade = _mistral_grade_from_confidence(confidence_dict)
        apparent_rows = _apparent_markdown_table_rows(text)
        duplicate = page_index in duplicate_indexes
        needs_review = not text or grade in _LOW_CONFIDENCE_GRADES or duplicate
        reason = (
            "empty_ocr_markdown"
            if not text
            else "low_confidence"
            if grade in _LOW_CONFIDENCE_GRADES
            else "duplicate_ocr_page"
            if duplicate
            else None
        )
        audit.append(
            {
                "page_index": page_index,
                "confidence_grade": grade,
                "average_confidence": (
                    confidence_dict.get("average_page_confidence_score")
                    if confidence_dict
                    else None
                ),
                "minimum_confidence": (
                    confidence_dict.get("minimum_page_confidence_score")
                    if confidence_dict
                    else None
                ),
                "markdown_chars": len(text),
                "apparent_table_rows": apparent_rows,
                "needs_review": needs_review,
                "reason": reason,
            }
        )
        if not text:
            continue
        parts.append(f"--- PAGE {page_index + 1} ---\n{text}")
    if invalid_pages > 0:
        audit.append(
            {
                "page_index": None,
                "confidence_grade": None,
                "average_confidence": None,
                "minimum_confidence": None,
                "markdown_chars": 0,
                "apparent_table_rows": 0,
                "needs_review": True,
                "reason": "invalid_ocr_page_index",
                "provider_page_count": invalid_pages,
            }
        )
    return "\n\n".join(parts), audit


def _mistral_document_markdown(pdf_bytes: bytes, *, page_count: int) -> str:
    """Compatibility wrapper returning only whole-document markdown."""
    markdown, _ = _mistral_document_markdown_with_audit(
        pdf_bytes, page_count=page_count
    )
    return markdown



DOCUMENT_EXTRACTION_PROTOCOL_VERSION = "document-extractions/v1"


def _gateway_json(
    path: str,
    payload: dict[str, Any],
    *,
    timeout_sec: int = 300,
) -> dict[str, Any] | None:
    """Internal durable-extraction protocol; None only means no gateway exists."""
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not api_key:
        return None
    request = urllib.request.Request(
        f"{base}/{path.lstrip('/')}",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                parsed = json.loads(response.read().decode("utf-8"))
            if not isinstance(parsed, dict):
                raise RuntimeError(f"document extraction gateway returned {type(parsed)}")
            return parsed
        except Exception as exc:  # noqa: BLE001 - bounded transport retry
            last = exc
            if attempt < 2:
                time.sleep(0.25 * (2**attempt))
    raise RuntimeError(f"document extraction gateway failed: {last}") from last


def _document_request_key(
    key: str,
    pdf_bytes: bytes,
    *,
    markdown: bool,
    max_pages: int | None,
    target_schema: Any,
    rows_schema: dict[str, Any] | None,
    rows_model: str | None,
    rows_retries: int,
    extra_fields: dict[str, Any] | None,
) -> str:
    descriptor = {
        "protocol": DOCUMENT_EXTRACTION_PROTOCOL_VERSION,
        "document_id": key,
        "document_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "markdown": markdown,
        "max_pages": max_pages,
        "target_schema": normalize_target_schema(target_schema),
        "rows_schema": rows_schema,
        "rows_model": rows_model or os.environ.get("EXTRACT_ROWS_MODEL", "").strip()
        or DEFAULT_EXTRACT_ROWS_MODEL,
        "rows_retries": rows_retries,
        "extra_fields": extra_fields,
    }
    encoded = json.dumps(
        descriptor,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_batch_key(request_keys: dict[str, str]) -> str:
    encoded = json.dumps(
        sorted(request_keys.items()),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_cache_lookup(request_key: str) -> dict[str, Any] | None:
    response = _gateway_json(
        "document-extractions/lookup", {"requestKey": request_key}
    )
    if response is None or response.get("hit") is not True:
        return None
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("document extraction cache hit has no payload object")
    return payload


def _document_cache_record(
    *,
    batch_key: str,
    request_key: str,
    document_id: str,
    payload: dict[str, Any],
    total: int,
) -> None:
    response = _gateway_json(
        "document-extractions/record",
        {
            "batchKey": batch_key,
            "requestKey": request_key,
            "documentId": document_id,
            "payload": payload,
            "total": total,
        },
    )
    if response is not None and response.get("ok") is not True:
        raise RuntimeError("document extraction result was not durably committed")


def _document_batch_progress(
    *,
    batch_key: str,
    total: int,
    completed: int,
    failed: int,
    cache_hits: int,
    done: bool,
) -> None:
    try:
        _gateway_json(
            "document-extractions/progress",
            {
                "batchKey": batch_key,
                "total": total,
                "completed": completed,
                "failed": failed,
                "cacheHits": cache_hits,
                "done": done,
            },
        )
        # Reuse the existing user-facing live note. It is advisory and
        # intentionally separate from the durable result receipt above.
        _gateway_json(
            "progress",
            {
                "note": (
                    f"Document extraction: {completed + failed}/{total} processed; "
                    f"{completed} complete, {failed} failed, {cache_hits} replayed"
                )
            },
        )
    except Exception as exc:  # noqa: BLE001 - visibility cannot invalidate data
        print(f"document extraction progress update failed: {exc}")


def extract_pdfs(
    documents: "Mapping[str, bytes] | Sequence[tuple[str, bytes]]",
    *,
    max_workers: int | None = None,
    max_attempts: int = 3,
    retry_backoff_s: float = 0.5,
    markdown: bool = False,
    extra_fields_by_key: "Mapping[str, dict[str, Any]] | None" = None,
    max_pages: int | None = None,
    target_schema: Any = None,
    rows_schema: dict[str, Any] | None = None,
    rows_model: str | None = None,
    rows_retries: int = 1,
) -> dict[str, dict[str, Any]]:
    """OCR many PDFs concurrently. One gateway call per DOCUMENT, fanned out.

    Each document is already a single gateway round trip (every page rides in one
    request), but documents were processed in a serial for-loop, so a 65-filing
    corpus paid 65 sequential network waits. The calls are independent and
    network-bound, so they are all eligible concurrently by default.

    Returns {key: {"rows"|"markdown": ..., "error": str | None}} — one entry per
    input, ALWAYS. A document that fails is reported rather than dropped, so a
    partial batch is visible instead of looking like a smaller-but-clean result.

    Transient failures are retried per document (max_attempts, exponential
    backoff) so a single network blip does not silently cost a whole file.

    Budget exhaustion (HTTP 429) is backpressure, not a crash and never retried:
    scheduling stops, already-running work finishes, and every unstarted document
    comes back with an explicit `error` so the shortfall is countable.

    Pass `rows_schema` to structure each document with a cheap schema-bound model
    (the `extract_rows` path) INSIDE the same fan-out, so a 65-filing corpus pays
    one concurrent batch rather than 65 sequential OCR-then-structure round trips.
    It describes ONE ROW — `{"ticker": "string", "amount": "number"}` or a JSON
    Schema object — and is validated once, before the batch starts, so a schema
    the provider would reject costs nothing instead of one 400 per document.
    Entries then carry both `rows` and `markdown`, and every row is stamped with
    `source_id` = the document key, which is what the host reconciles against.
    Do NOT confuse this with `target_schema`, which is Mistral's own column
    mapping and misaligns headers on real documents.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from nexustrade.host import GatewayChatError

    # Validate once for the whole batch rather than once per document. This is
    # a programming error in the caller's schema, not a per-document outcome, so
    # it raises instead of returning 62 identical `error` entries after paying
    # for 62 OCRs.
    if rows_schema is not None:
        from nexustrade.document_inspect_receipt import require_prior_inspect_receipt

        require_prior_inspect_receipt()
        rows_schema = normalize_rows_schema(rows_schema)

    items: list[tuple[str, bytes]] = (
        list(documents.items()) if hasattr(documents, "items") else list(documents)
    )
    results: dict[str, dict[str, Any]] = {}
    if not items:
        return results

    # No artificial per-call throttle. Every independent document is eligible
    # immediately unless the caller deliberately chooses a smaller public
    # max_workers value. Provider backpressure remains explicit per document.
    workers = (
        len(items)
        if max_workers is None
        else max(1, min(int(max_workers), len(items)))
    )
    stop = False

    request_keys = {
        key: _document_request_key(
            key,
            pdf_bytes,
            markdown=markdown,
            max_pages=max_pages,
            target_schema=target_schema,
            rows_schema=rows_schema,
            rows_model=rows_model,
            rows_retries=rows_retries,
            extra_fields=(extra_fields_by_key or {}).get(key),
        )
        for key, pdf_bytes in items
    }
    batch_key = _document_batch_key(request_keys)
    try:
        _gateway_json(
            "document-extractions/begin",
            {"batchKey": batch_key, "total": len(items)},
        )
    except Exception as exc:  # noqa: BLE001 - record calls remain authoritative
        print(f"document extraction batch registration failed: {exc}")

    def run_once(key: str, pdf_bytes: bytes) -> dict[str, Any]:
        if rows_schema:
            extracted = extract_rows(
                pdf_bytes,
                schema=rows_schema,
                model=rows_model,
                max_pages=max_pages,
                source_id=key,
                retries=rows_retries,
            )
            return {
                "rows": extracted.rows,
                "markdown": extracted.markdown,
                "page_audit": extracted.page_audit,
                "apparent_table_rows": extracted.apparent_table_rows,
                "needs_review": extracted.needs_review,
                "error": None,
            }
        if markdown:
            return {
                "markdown": extract_pdf_markdown(
                    pdf_bytes, max_pages=max_pages, force_ocr=True
                ),
                "error": None,
            }
        return {
            "rows": extract_pdf(
                pdf_bytes,
                extra_fields=(extra_fields_by_key or {}).get(key),
                max_pages=max_pages,
                target_schema=target_schema,
                source_id=key,
            ),
            "error": None,
        }

    def run_one(key: str, pdf_bytes: bytes) -> tuple[dict[str, Any], bool]:
        """Retry transient failures here so the caller never has to re-drive the loop.

        A single network blip used to cost a whole document silently. Budget
        exhaustion is NEVER retried — a 429 is backpressure, and retrying it just
        spends the remaining allowance faster.
        """
        cached = _document_cache_lookup(request_keys[key])
        if cached is not None:
            return cached, True
        last: Exception | None = None
        result: dict[str, Any] | None = None
        for attempt in range(max_attempts):
            try:
                result = run_once(key, pdf_bytes)
                break
            except OcrBudgetExhausted:
                raise
            except GatewayChatError:
                # gateway_chat already owns bounded transport/permanent request
                # handling. Repeating it here would multiply the same failure by
                # both document and schema retry budgets.
                raise
            except Exception as exc:
                last = exc
                if attempt + 1 < max_attempts:
                    time.sleep(retry_backoff_s * (2**attempt))
        if result is None:
            raise last if last else RuntimeError("ocr failed with no exception")
        try:
            _document_cache_record(
                batch_key=batch_key,
                request_key=request_keys[key],
                document_id=key,
                payload=result,
                total=len(items),
            )
        except Exception as exc:
            # The server may have committed before its response socket died.
            # Resolve that ambiguity from the durable store; never pay to
            # re-extract a valid result merely because its acknowledgement was
            # lost.
            committed = _document_cache_lookup(request_keys[key])
            if committed is not None:
                return committed, True
            raise RuntimeError(
                f"validated extraction could not be durably committed: {exc}"
            ) from exc
        return result, False

    empty = "markdown" if markdown else "rows"
    blank = "" if markdown else []

    def unstarted(reason: str) -> dict[str, Any]:
        return {empty: "" if markdown else [], "error": reason}

    # Submit incrementally, keeping at most `workers` in flight. Submitting the
    # whole batch up front would make budget backpressure a no-op: every document
    # is already scheduled by the time the first 429 comes back.
    pending = list(items)
    completed = 0
    failed = 0
    cache_hits = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        in_flight: dict[Any, str] = {}
        while pending or in_flight:
            while pending and len(in_flight) < workers and not stop:
                key, pdf_bytes = pending.pop(0)
                in_flight[pool.submit(run_one, key, pdf_bytes)] = key
            if not in_flight:
                break
            done = next(as_completed(list(in_flight)))
            key = in_flight.pop(done)
            try:
                result, cache_hit = done.result()
                results[key] = result
                completed += 1
                cache_hits += int(cache_hit)
            except OcrBudgetExhausted as exc:
                stop = True
                results[key] = unstarted(f"budget_exhausted: {exc}")
                failed += 1
            except Exception as exc:  # per-document: one bad file cannot lose the rest
                results[key] = unstarted(str(exc))
                failed += 1
            _document_batch_progress(
                batch_key=batch_key,
                total=len(items),
                completed=completed,
                failed=failed,
                cache_hits=cache_hits,
                done=False,
            )
        for key, _ in pending:
            results[key] = unstarted("not_started: ocr budget exhausted")
            failed += 1

    for key, _ in items:
        results.setdefault(key, unstarted("not_started: ocr budget exhausted"))
    _document_batch_progress(
        batch_key=batch_key,
        total=len(items),
        completed=completed,
        failed=failed,
        cache_hits=cache_hits,
        done=True,
    )
    return results

def extract_pdf_markdown_with_audit(
    pdf_bytes: bytes,
    max_pages: int | None = None,
    force_ocr: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Whole document markdown plus per-page extraction receipts.

    Confidence is present for Mistral OCR and explicitly unknown for the free
    text-layer path. Callers can therefore distinguish trusted, review-marked,
    and unavailable quality evidence instead of silently losing it.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    page_count = len(doc)
    limit = page_count if max_pages is None else min(page_count, max_pages)

    if force_ocr:
        return _mistral_document_markdown_with_audit(
            pdf_bytes, page_count=limit
        )

    chunks: list[str] = []
    audit: list[dict[str, Any]] = []
    for page_index in range(limit):
        try:
            textpage = doc[page_index].get_textpage()
            text = (textpage.get_text_range() or "").strip()
        except Exception:
            text = ""
        audit.append(
            {
                "page_index": page_index,
                "confidence_grade": None,
                "average_confidence": None,
                "minimum_confidence": None,
                "markdown_chars": len(text),
                "apparent_table_rows": _apparent_markdown_table_rows(text),
                "needs_review": not text,
                "reason": "empty_text_layer" if not text else None,
            }
        )
        if text:
            chunks.append(f"--- PAGE {page_index + 1} ---\n{text}")
    return "\n\n".join(chunks), audit


def extract_pdf_markdown(
    pdf_bytes: bytes,
    max_pages: int | None = None,
    force_ocr: bool = False,
) -> str:
    """Whole document as markdown — prose, headings and tables in reading order."""
    markdown, _ = extract_pdf_markdown_with_audit(
        pdf_bytes, max_pages=max_pages, force_ocr=force_ocr
    )
    return markdown


DEFAULT_EXTRACT_ROWS_MODEL = "openai/gpt-5.6-luna"

# The care points below are not decoration: this exact wording is what scored
# 220/220 rows across 64 documents on 2026-07-22. A generic "extract the rows"
# instruction was NOT measured and should not be assumed equivalent.
_EXTRACT_ROWS_SYSTEM = (
    "You convert OCR markdown of a document table into JSON rows matching the "
    "provided schema. Transcribe; do not summarise, filter, or drop records. "
    "Two complete records that look identical are two separate records and "
    "must both appear. A table row that continues onto the next page is one "
    "record: emit it once with one amount, not two fragments.\n\n"
    "The mistakes that matter, in order of how often they are made:\n"
    "1. A value in ROUND parentheses and a code in SQUARE brackets can COLLIDE "
    "(e.g. `AllianceBernstein Holding L.P. Units (AB) [AB]`). Read each from its "
    "own bracket type; never copy one into the other, and never drop a row "
    "because they match.\n"
    "2. When the schema includes a dedicated column for a semantic field, "
    "transcribe that column literally — description prose must not override it. "
    "When a schema field has no dedicated column and prose is the only evidence, "
    "transcribe from that prose into the schema field once; do not run a second "
    "keyword or regex classification pass afterward.\n"
    "3. A row can WRAP across lines or across a page boundary. The continued "
    "fragment is the same record, not a second row and not a row to reject. "
    "Combine it into one JSON object before emitting; never add the amount "
    "twice.\n"
    "4. Older documents may omit a code entirely. Return null; never infer one."
)


class _RowsStructuringError(RuntimeError):
    """Schema-bound structuring failed after OCR already completed."""


@dataclass
class ExtractedRows:
    """Structured rows plus the markdown they came from.

    The markdown is the run's audit trail: when a row looks wrong you can diff it
    against the source text without re-fetching or re-paying for OCR.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    markdown: str = ""
    source_id: str | None = None
    page_audit: list[dict[str, Any]] = field(default_factory=list)
    apparent_table_rows: int = 0
    needs_review: bool = False

    def __iter__(self):
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


def _rows_from_structured_result(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        rows = result.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    raise RuntimeError(
        f"extract_rows: structured output missing rows array: {result!r}"
    )


class RowsSchemaError(ValueError):
    """Raised when rows_schema is a shape we cannot turn into a JSON Schema.

    Loud, and raised before any OCR or gateway call. The old code forwarded
    whatever it was handed straight into
    `response_format.json_schema.schema` with `strict: true`, so the flat
    `{field: "string"}` shorthand every operator writes reached OpenAI as an
    object with no `properties` and came back:

        Invalid schema for response_format 'extract_rows':
        In context=(), object schema missing properties. (invalid_json_schema)

    Both race legs hit the same backend, so the run saw only `all_legs_failed`
    once per document per retry -- on 2026-08-09 that was 1,488 rejected calls
    across 62 filings before the operator gave up on the argument entirely.
    """


_ROWS_SCHEMA_SCALAR_TYPES: dict[str, str] = {
    "bool": "boolean",
    "boolean": "boolean",
    "float": "number",
    "int": "integer",
    "integer": "integer",
    "number": "number",
    "str": "string",
    "string": "string",
    "text": "string",
}

# Keys that mark a value as already being JSON Schema rather than a field name.
_JSON_SCHEMA_MARKERS = frozenset(
    {"type", "properties", "items", "$schema", "anyOf", "oneOf", "allOf", "$ref"}
)


def _strict_object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    """Object schema in the form strict structured output requires.

    Strict mode demands `additionalProperties: false` and every property listed
    in `required`; omitting either is rejected by the provider, not ignored.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _nullable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Allow JSON null without weakening the schema's non-null branch."""
    out = dict(schema)
    schema_type = out.get("type")
    if schema_type == "null":
        return out
    if isinstance(schema_type, str):
        out["type"] = [schema_type, "null"]
    elif isinstance(schema_type, list):
        if "null" not in schema_type:
            out["type"] = [*schema_type, "null"]
    elif isinstance(out.get("anyOf"), list):
        if not any(
            isinstance(option, Mapping) and option.get("type") == "null"
            for option in out["anyOf"]
        ):
            out["anyOf"] = [*out["anyOf"], {"type": "null"}]
    else:
        return {"anyOf": [out, {"type": "null"}]}

    enum = out.get("enum")
    if isinstance(enum, list) and None not in enum:
        out["enum"] = [*enum, None]
    return out


def _schema_allows_null(schema: Mapping[str, Any]) -> bool:
    schema_type = schema.get("type")
    if schema_type == "null" or (
        isinstance(schema_type, list) and "null" in schema_type
    ):
        return True
    enum = schema.get("enum")
    if isinstance(enum, list) and None in enum:
        return True
    branches = schema.get("anyOf")
    return isinstance(branches, list) and any(
        isinstance(branch, Mapping) and _schema_allows_null(branch)
        for branch in branches
    )


def _schema_has_type(schema: Mapping[str, Any], expected: str) -> bool:
    schema_type = schema.get("type")
    return schema_type == expected or (
        isinstance(schema_type, list) and expected in schema_type
    )


def _strict_json_schema(
    schema: Mapping[str, Any], *, force_required: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Recursively adapt JSON Schema to strict structured-output semantics.

    JSON Schema normally expresses an optional field by leaving it out of
    `required`. Strict structured output instead requires every property name,
    so optional fields become required-but-nullable. This preserves the
    caller's semantics while ensuring absent values are returned explicitly.
    """
    out = dict(schema)
    explicitly_nullable = _schema_allows_null(schema)

    for union_key in ("anyOf", "oneOf", "allOf"):
        branches = out.get(union_key)
        if isinstance(branches, list):
            out[union_key] = [
                _strict_json_schema(branch) if isinstance(branch, Mapping) else branch
                for branch in branches
            ]

    properties = out.get("properties")
    is_object = _schema_has_type(out, "object") or isinstance(properties, Mapping)
    if is_object:
        if not isinstance(properties, Mapping) or not properties:
            raise RowsSchemaError(
                "rows_schema contains an object with no usable `properties`; "
                "strict structured output requires properties on every object"
            )

        declared_required = out.get("required", [])
        if not isinstance(declared_required, list) or not all(
            isinstance(name, str) for name in declared_required
        ):
            raise RowsSchemaError(
                "rows_schema object `required` must be a list of property names"
            )
        unknown_required = set(declared_required) - set(properties)
        if unknown_required:
            raise RowsSchemaError(
                "rows_schema object `required` names unknown properties: "
                + ", ".join(sorted(unknown_required))
            )

        semantically_required = set(declared_required) | set(force_required)
        normalized_properties: dict[str, Any] = {}
        for field_name, property_schema in properties.items():
            if not isinstance(field_name, str) or not isinstance(
                property_schema, Mapping
            ):
                raise RowsSchemaError(
                    "rows_schema object properties must map field names to schemas"
                )
            normalized = _strict_json_schema(property_schema)
            if field_name not in semantically_required:
                normalized = _nullable_schema(normalized)
            normalized_properties[field_name] = normalized

        out["type"] = "object"
        out["properties"] = normalized_properties
        out["required"] = list(normalized_properties)
        out["additionalProperties"] = False

    if _schema_has_type(out, "array"):
        items = out.get("items")
        if not isinstance(items, Mapping):
            raise RowsSchemaError(
                "rows_schema array `items` must contain a JSON Schema object"
            )
        out["items"] = _strict_json_schema(items)

    return _nullable_schema(out) if explicitly_nullable else out


def _rows_envelope(row_schema: dict[str, Any]) -> dict[str, Any]:
    return _strict_object_schema({"rows": {"type": "array", "items": row_schema}})


def normalize_rows_schema(rows_schema: Any) -> dict[str, Any]:
    """Canonicalize rows_schema to the strict `{"rows": [...]}` envelope.

    `_rows_from_structured_result` needs a `rows` array, but nothing used to say
    so and nothing enforced it, so a caller could pass a schema that validated
    fine and still lose every row. Accepts every shape callers actually pass:

      {"ticker": "string", "amount": "number"}   - field -> scalar type name
      {"ticker": {"type": "string"}}             - field -> property schema
      {"type": "object", "properties": {...}}    - one ROW's object schema
      {"type": "object", "properties":
          {"rows": {"type": "array", ...}}}      - already the envelope

    Full JSON Schema retains its normal optional-field semantics: properties
    omitted from the caller's `required` list become required-but-nullable in
    the provider schema. Every object is recursively closed and all keys are
    returned, so missing evidence is explicit `null` rather than a missing key.

    Anything else raises rather than reaching the provider as an invalid schema.
    """
    if not isinstance(rows_schema, Mapping) or not rows_schema:
        raise RowsSchemaError(
            "rows_schema must be a non-empty dict: either {field: 'string'} or a "
            f"JSON Schema object, got {type(rows_schema).__name__}"
        )

    looks_like_json_schema = any(key in rows_schema for key in _JSON_SCHEMA_MARKERS)
    if looks_like_json_schema:
        properties = rows_schema.get("properties")
        if not isinstance(properties, Mapping) or not properties:
            raise RowsSchemaError(
                "rows_schema is a JSON Schema with no usable `properties`; strict "
                "structured output rejects it with 'object schema missing "
                "properties'. Give one property per column, e.g. "
                "{'type': 'object', 'properties': {'ticker': {'type': 'string'}}}"
            )
        rows_property = properties.get("rows")
        if isinstance(rows_property, Mapping) and _schema_has_type(
            rows_property, "array"
        ):
            # `rows` is the extraction protocol, not an optional caller field.
            return _strict_json_schema(rows_schema, force_required=frozenset({"rows"}))

        # `rows_schema` describes ONE extracted row. A common caller mistake is
        # to pass a second collection envelope such as {"records": [...]}. It
        # is valid JSON Schema, so blindly wrapping it produces
        # {"rows": [{"records": [...]}]}: one outer result per document. The
        # extraction succeeds and downstream code can then silently see zero
        # semantic rows. Reject that ambiguous shape before OCR instead.
        if len(properties) == 1:
            collection_name, collection_schema = next(iter(properties.items()))
            collection_items = (
                collection_schema.get("items")
                if isinstance(collection_schema, Mapping)
                else None
            )
            if (
                collection_name != "rows"
                and isinstance(collection_schema, Mapping)
                and _schema_has_type(collection_schema, "array")
                and isinstance(collection_items, Mapping)
                and (
                    _schema_has_type(collection_items, "object")
                    or isinstance(collection_items.get("properties"), Mapping)
                )
            ):
                raise RowsSchemaError(
                    "rows_schema describes ONE extracted row, but the supplied "
                    f"schema is a collection envelope named {collection_name!r}. "
                    "Pass the array's item object as rows_schema, or use the "
                    "reserved JSON Schema `rows` array extraction envelope."
                )
        return _rows_envelope(_strict_json_schema(rows_schema))

    row_properties: dict[str, Any] = {}
    for field_name, spec in rows_schema.items():
        name = str(field_name)
        if isinstance(spec, Mapping):
            row_properties[name] = _strict_json_schema(spec)
            continue
        if spec is None:
            row_properties[name] = {"type": "string"}
            continue
        if isinstance(spec, str):
            json_type = _ROWS_SCHEMA_SCALAR_TYPES.get(spec.strip().lower())
            if json_type is None:
                raise RowsSchemaError(
                    f"rows_schema field {name!r} names an unknown type {spec!r}; "
                    f"use one of {sorted(set(_ROWS_SCHEMA_SCALAR_TYPES))}"
                )
            row_properties[name] = {"type": json_type}
            continue
        raise RowsSchemaError(
            f"rows_schema field {name!r} must map to a type name or a property "
            f"schema, got {type(spec).__name__}"
        )

    return _rows_envelope(_strict_object_schema(row_properties))


def extract_rows(
    pdf_bytes: bytes,
    *,
    schema: dict[str, Any],
    model: str | None = None,
    max_pages: int | None = None,
    force_ocr: bool = True,
    schema_name: str = "extract_rows",
    source_id: str | None = None,
    retries: int = 1,
) -> ExtractedRows:
    """OCR the document, then structure the markdown with a cheap schema-bound LLM.

    Replaces hand-written regex over OCR markdown \u2014 the defect class measured on
    2026-07-22 (page-boundary wraps, ticker/asset-code collisions, transaction
    type read from neighbouring prose). Mistral emits clean inline pipe tables; a
    schema-bound text model structures them at ~$0.0002/document.

    Bare json_object mode does NOT bind every model \u2014 always pass an explicit
    schema. Without one, some provider routes return prose fragments.

    `schema` describes ONE ROW and is normalized by `normalize_rows_schema`, so
    the flat `{"ticker": "string", "amount": "number"}` shorthand and a full
    JSON Schema object are both accepted; the `{"rows": [...]}` envelope strict
    mode needs is added for you. An uninterpretable schema raises
    `RowsSchemaError` before any OCR or gateway call.

    `source_id` and a host-owned zero-based `_source_row_index` are stamped onto
    every returned row. The host derives per-document reconciliation from
    `source_id`; the row index keeps every extracted source row distinct when
    callers build a transaction ledger or combine batch and serial fallback
    results.
    """
    from nexustrade.document_inspect_receipt import require_prior_inspect_receipt

    require_prior_inspect_receipt()
    # Before the OCR hop, not after: a schema the provider will reject costs
    # nothing to catch here and a full document's OCR to catch downstream.
    normalized_schema = normalize_rows_schema(schema)

    from nexustrade.host import GatewayChatError, gateway_chat_json

    markdown, page_audit = extract_pdf_markdown_with_audit(
        pdf_bytes, max_pages=max_pages, force_ocr=force_ocr
    )
    apparent_table_rows = sum(
        int(page.get("apparent_table_rows") or 0) for page in page_audit
    )
    needs_review = any(page.get("needs_review") is True for page in page_audit)
    configured = os.environ.get("EXTRACT_ROWS_MODEL", "").strip()
    structure_model = model or configured or DEFAULT_EXTRACT_ROWS_MODEL

    user_prompt = markdown
    if source_id:
        user_prompt = f"source_id: {source_id}\n\n{markdown}"

    attempts = max(1, retries + 1)
    rows: list[dict[str, Any]] = []
    for attempt in range(attempts):
        try:
            result = gateway_chat_json(
                prompt=user_prompt,
                system=_EXTRACT_ROWS_SYSTEM,
                model=structure_model,
                json_schema=normalized_schema,
                schema_name=schema_name,
                temperature=0,
            )
            rows = _rows_from_structured_result(result)
            if not rows and apparent_table_rows > 0:
                raise RuntimeError(
                    "structured output returned zero rows despite "
                    f"{apparent_table_rows} apparent OCR table row(s)"
                )
            break
        except GatewayChatError:
            raise
        except Exception as exc:  # noqa: BLE001 - retried, re-raised on the last attempt
            # Structured output is intermittently truncated on some provider
            # routes; one retry costs a fraction of a cent against losing the
            # whole exec round.
            if attempt == attempts - 1:
                raise _RowsStructuringError(
                    f"extract_rows failed after {attempts} attempt(s): {exc}"
                ) from exc

    if source_id:
        for row_index, row in enumerate(rows):
            row["source_id"] = source_id
            row["_source_row_index"] = row_index
    if needs_review:
        for row in rows:
            row.setdefault("_needs_review", True)
            row.setdefault("_reason", "ocr_page_requires_review")

    return ExtractedRows(
        rows=rows,
        markdown=markdown,
        source_id=source_id,
        page_audit=page_audit,
        apparent_table_rows=apparent_table_rows,
        needs_review=needs_review,
    )
