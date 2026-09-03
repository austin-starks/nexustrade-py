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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from .host import _touch_host_activity

DEFAULT_MISTRAL_OCR_MODEL = "mistral-ocr-latest"
_MISTRAL_POOR_CONFIDENCE = 0.70
_MISTRAL_FAIR_CONFIDENCE = 0.85

_LOW_CONFIDENCE_GRADES = frozenset({"POOR", "FAIR"})


class _WebTextParser(HTMLParser):
    """Extract visible article/main/body text and small publisher metadata."""

    _HIDDEN = frozenset(
        {"script", "style", "noscript", "svg", "template", "footer", "nav"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = {
            "hidden": 0,
            "body": 0,
            "main": 0,
            "article": 0,
            "header": 0,
            "title": 0,
        }
        self.parts: dict[str, list[str]] = {
            "body": [],
            "main": [],
            "article": [],
            "title": [],
        }
        self.meta: dict[str, str] = {}
        self.time_hint: str | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        attributes = {
            name.lower(): value.strip()
            for name, value in attrs
            if value is not None and value.strip()
        }
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key and attributes.get("content") and key not in self.meta:
                self.meta[key] = attributes["content"]
        elif tag == "time" and self.time_hint is None:
            self.time_hint = attributes.get("datetime")
        if tag in self._HIDDEN:
            self.depth["hidden"] += 1
        if tag in self.depth:
            self.depth[tag] += 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._HIDDEN and self.depth["hidden"] > 0:
            self.depth["hidden"] -= 1
        if tag in self.depth and self.depth[tag] > 0:
            self.depth[tag] -= 1

    def handle_data(self, data: str) -> None:
        if self.depth["hidden"] > 0:
            return
        if (
            self.depth["header"] > 0
            and self.depth["main"] == 0
            and self.depth["article"] == 0
        ):
            return
        if self.depth["title"] > 0:
            self.parts["title"].append(data)
        if self.depth["body"] == 0:
            return
        self.parts["body"].append(data)
        for scope in ("main", "article"):
            if self.depth[scope] > 0:
                self.parts[scope].append(data)

    def text(self, scope: str) -> str:
        return " ".join(" ".join(self.parts[scope]).split())

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

    A PDF can carry a full text layer that looks complete yet decodes visible labels with
    implausible mixed case, while broken ToUnicode maps decode to U+FFFD/U+FFFE or the
    private-use area. `has_text_layer` alone therefore cannot establish usability.
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
    page_failures: dict[int, str],
) -> str | None:
    failure_reason = page_failures.get(page_index)
    if failure_reason is not None:
        return failure_reason
    if row_count <= 0:
        if grade in _LOW_CONFIDENCE_GRADES:
            return "no_apparent_table_low_confidence"
        return "no_apparent_table"
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


def _mistral_page_has_table_evidence(page: dict[str, Any]) -> bool:
    """Return whether Mistral exposed table-shaped evidence on this page.

    A prose-only page returning no rows is not a failed table extraction. A page
    carrying a table payload or materialized table markup but yielding no parsed
    rows is. Keep this mechanical distinction separate from the caller's later
    decision about whether prose on the page matters to the requested task.
    """
    tables = page.get("tables")
    if isinstance(tables, list):
        for table in tables:
            if not isinstance(table, dict):
                continue
            content = table.get("content") or table.get("markdown") or table.get("html")
            if isinstance(content, str) and content.strip():
                return True
    markdown = page.get("markdown")
    return isinstance(markdown, str) and _apparent_markdown_table_rows(markdown) > 0


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
    _touch_host_activity()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        _touch_host_activity()
    except urllib.error.HTTPError as exc:
        _touch_host_activity()
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
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, str | None], dict[int, str]]:
    """Run Mistral OCR and return per-page table rows + confidence grades."""
    payload = _mistral_ocr_document(
        pdf_bytes, page_count=page_count, total_pages=total_pages
    )
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise RuntimeError("mistral OCR response missing pages")

    by_page: dict[int, list[dict[str, Any]]] = {i: [] for i in range(page_count)}
    grades: dict[int, str | None] = dict.fromkeys(range(page_count), None)
    page_failures: dict[int, str] = {}
    pages_by_index: dict[int, dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_index = page.get("index")
        if not isinstance(page_index, int) or page_index < 0 or page_index >= page_count:
            continue
        if page_index in pages_by_index:
            page_failures[page_index] = "duplicate_ocr_page"
            continue
        pages_by_index[page_index] = page

    for page_index in range(page_count):
        page = pages_by_index.get(page_index)
        if page is None:
            page_failures[page_index] = "missing_ocr_page"
            continue
        confidence = page.get("confidence_scores")
        grade = _mistral_grade_from_confidence(
            confidence if isinstance(confidence, dict) else None
        )
        grades[page_index] = grade
        has_table_evidence = _mistral_page_has_table_evidence(page)
        try:
            page_rows = _mistral_page_rows(page)
        except Exception:
            page_failures[page_index] = "table_parse_failed"
            continue
        if not page_rows and has_table_evidence:
            page_failures[page_index] = "table_parse_failed"
        if (
            not page_rows
            and not has_table_evidence
            and not _mistral_page_markdown(page).strip()
        ):
            page_failures[page_index] = "empty_ocr_page"
        for row in page_rows:
            row["_extract_source"] = "mistral"
            row["_page_index"] = page_index
            row["_page_number"] = page_index + 1
            row["_page_index_base"] = 0
            if grade is not None:
                row["_page_grade"] = grade
            by_page[page_index].append(row)
    return by_page, grades, page_failures


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
        row["_page_number"] = page_index + 1
        row["_page_index_base"] = 0
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


def render_pdf_page_png(pdf_bytes: bytes, page_index: int, scale: float = 2.0) -> bytes:
    """Render one zero-based PDF page to PNG bytes for page-scoped vision."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page_count = len(doc)
        if page_index < 0 or page_index >= page_count:
            raise IndexError(
                f"page_index {page_index} out of range (pages={page_count})"
            )
        page = doc[page_index]
        try:
            bitmap = page.render(scale=scale)
            try:
                pil = bitmap.to_pil()
                try:
                    buf = io.BytesIO()
                    pil.save(buf, format="PNG")
                    return buf.getvalue()
                finally:
                    pil.close()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()


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
        "_page_number": page_index + 1,
        "_page_index_base": 0,
        "_page_grade": grade,
        "_needs_review": True,
        "_reason": reason,
    }


def _no_apparent_table_marker(
    page_index: int,
    grade: str | None,
) -> dict[str, Any]:
    """Preserve coverage for a page with no table-shaped evidence.

    This is deliberately not an unresolved extraction row. The page may still
    contain task-relevant prose, but the table extractor did not encounter a
    table that it failed to parse. Callers can inspect the self-described page
    when the natural-language request makes that prose material.
    """
    return {
        "_extract_source": "page_audit",
        "_page_index": page_index,
        "_page_number": page_index + 1,
        "_page_index_base": 0,
        "_page_grade": grade,
        "_needs_review": False,
        "_reason": "no_apparent_table",
    }


def _assemble_mistral_extract_rows(
    *,
    limit: int,
    alias_map: dict[str, str],
    fields: list[str],
    extra_fields: dict[str, Any] | None,
    by_page: dict[int, list[dict[str, Any]]],
    grades: dict[int, str | None],
    page_failures: dict[int, str],
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
            page_failures=page_failures,
        )
        if reason is not None:
            if page_rows:
                for row in page_rows:
                    row["_needs_review"] = True
                    row["_reason"] = reason
            else:
                marker = (
                    _no_apparent_table_marker(page_index, grade)
                    if reason == "no_apparent_table"
                    else _unresolved_page_marker(page_index, grade, reason)
                )
                all_rows.append(marker)
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

    Mistral parses table structure. A page with no apparent table emits a
    metadata-only `_extract_source=page_audit` coverage marker; a page whose
    apparent table could not be parsed or whose evidence is low-confidence is
    flagged `_needs_review` or emits `_extract_source=unresolved`. Neither path
    silently drops a page, and neither runs a second vision-model parser.

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
        by_page, grades, page_failures = _mistral_rows_by_page(
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
        page_failures=page_failures,
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
        text = ocr_png_bytes(render_pdf_page_png(pdf_bytes, page_index))
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
                    "page_number": page_index + 1,
                    "page_index_base": 0,
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
                "page_number": page_index + 1,
                "page_index_base": 0,
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
                "page_number": None,
                "page_index_base": 0,
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



DOCUMENT_EXTRACTION_PROTOCOL_VERSION = "document-extractions/v9"
DEFAULT_EXTRACT_ROWS_PDF_MAX_BYTES = 50 * 1024 * 1024
GROUP_EXTRACT_GATEWAY_TIMEOUT_SEC = 15 * 60 + 30
# Keep this aligned with NexusGenAI's decoded attachment-byte ceiling. The
# gateway accepts any number of files that fit inside this real payload bound.
DEFAULT_EXTRACT_ROWS_REQUEST_MAX_BYTES = 50 * 1024 * 1024


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
        _touch_host_activity()
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                parsed = json.loads(response.read().decode("utf-8"))
            _touch_host_activity()
            if not isinstance(parsed, dict):
                raise RuntimeError(f"document extraction gateway returned {type(parsed)}")
            return parsed
        except Exception as exc:  # noqa: BLE001 - bounded transport retry
            _touch_host_activity()
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
    document_schema: dict[str, Any] | None = None,
    rows_schema: dict[str, Any] | None,
    rows_model: str | None,
    rows_retries: int,
    rows_include_pdf: bool,
    rows_pdf_max_bytes: int,
    min_rows_per_document: int = 0,
    rows_force_ocr: bool = True,
    rows_schema_name: str = "extract_rows",
    instructions: str | None = None,
    documents_per_request: int = 1,
    group_context_key: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> str:
    descriptor = {
        "protocol": DOCUMENT_EXTRACTION_PROTOCOL_VERSION,
        # Every semantic input belongs in this key so session-local replay cannot
        # survive a changed corpus, schema, prompt, or model.
        "document_id": key,
        "document_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "markdown": markdown,
        "max_pages": max_pages,
        "target_schema": normalize_target_schema(target_schema),
        "document_schema": document_schema,
        "rows_schema": rows_schema,
        "rows_system_sha256": (
            hashlib.sha256(_EXTRACT_ROWS_SYSTEM.encode("utf-8")).hexdigest()
            if rows_schema is not None or document_schema is not None
            else None
        ),
        "group_system_sha256": (
            hashlib.sha256(_EXTRACT_PDF_GROUP_SYSTEM.encode("utf-8")).hexdigest()
            if documents_per_request > 1
            and (rows_schema is not None or document_schema is not None)
            else None
        ),
        "rows_model": rows_model or os.environ.get("EXTRACT_ROWS_MODEL", "").strip()
        or DEFAULT_EXTRACT_ROWS_MODEL,
        "rows_retries": rows_retries,
        "rows_include_pdf": rows_include_pdf,
        "rows_pdf_max_bytes": rows_pdf_max_bytes,
        "min_rows_per_document": min_rows_per_document,
        "rows_force_ocr": rows_force_ocr,
        "rows_schema_name": rows_schema_name,
        "instructions_sha256": (
            hashlib.sha256(instructions.encode("utf-8")).hexdigest()
            if instructions
            else None
        ),
        "documents_per_request": documents_per_request,
        "group_context_key": group_context_key,
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


def _document_group_context_key(group: list[tuple[str, bytes]]) -> str:
    encoded = json.dumps(
        [
            [source_id, hashlib.sha256(pdf_bytes).hexdigest()]
            for source_id, pdf_bytes in group
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_group_request_keys(
    group: list[tuple[str, bytes]],
    *,
    markdown: bool,
    max_pages: int | None,
    target_schema: Any,
    document_schema: dict[str, Any] | None,
    rows_schema: dict[str, Any] | None,
    rows_model: str | None,
    rows_retries: int,
    rows_include_pdf: bool,
    rows_pdf_max_bytes: int,
    min_rows_per_document: int,
    instructions: str | None,
    extra_fields_by_key: "Mapping[str, dict[str, Any]] | None",
) -> dict[str, str]:
    """Address results by the exact peer corpus sent to the model."""
    context_key = _document_group_context_key(group)
    return {
        key: _document_request_key(
            key,
            pdf_bytes,
            markdown=markdown,
            max_pages=max_pages,
            target_schema=target_schema,
            document_schema=document_schema,
            rows_schema=rows_schema,
            rows_model=rows_model,
            rows_retries=rows_retries,
            rows_include_pdf=rows_include_pdf,
            rows_pdf_max_bytes=rows_pdf_max_bytes,
            min_rows_per_document=min_rows_per_document,
            rows_force_ocr=True,
            rows_schema_name="extract_rows",
            instructions=instructions,
            documents_per_request=len(group),
            group_context_key=context_key,
            extra_fields=(extra_fields_by_key or {}).get(key),
        )
        for key, pdf_bytes in group
    }


def _partition_pdf_document_groups(
    items: list[tuple[str, bytes]],
    *,
    documents_per_request: int,
) -> list[list[tuple[str, bytes]]]:
    groups: list[list[tuple[str, bytes]]] = []
    current: list[tuple[str, bytes]] = []
    current_bytes = 0
    for item in items:
        item_bytes = len(item[1])
        if current and (
            len(current) >= documents_per_request
            or current_bytes + item_bytes > DEFAULT_EXTRACT_ROWS_REQUEST_MAX_BYTES
        ):
            groups.append(current)
            current = []
            current_bytes = 0
        current.append(item)
        current_bytes += item_bytes
    if current:
        groups.append(current)
    return groups


def _document_result_record(
    *,
    batch_key: str,
    request_key: str,
    document_id: str,
    payload: dict[str, Any],
    total: int,
    track_progress: bool = True,
) -> None:
    response = _gateway_json(
        "document-extractions/record",
        {
            "batchKey": batch_key,
            "requestKey": request_key,
            "documentId": document_id,
            "payload": payload,
            "total": total,
            "trackProgress": track_progress,
        },
    )
    if response is not None and response.get("ok") is not True:
        raise RuntimeError("document extraction result was not durably committed")


def _document_result_lookup(request_key: str) -> dict[str, Any] | None:
    response = _gateway_json(
        "document-extractions/lookup",
        {"requestKey": request_key},
    )
    if response is None or response.get("hit") is not True:
        return None
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("document extraction replay payload is not an object")
    return payload


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


_EXTRACT_PDF_GROUP_SYSTEM = (
    "Complete the caller's schema-bound extraction task from every attached PDF. "
    "The caller instructions define the requested rows and any inclusion or "
    "exclusion semantics. Use the PDF bytes as the authoritative visual source. "
    "Return a documents array containing exactly one object for every supplied "
    "source_id and no other source_id. Keep every source fact attributed to the "
    "PDF where it is visible. "
    "When the caller's schema or instructions request a cross-document relationship, "
    "such as an amendment, restatement, or duplicate, compare the attached PDFs to "
    "identify that relationship. Never copy, reuse, or complete a source-local field "
    "from another attachment, even when rows look similar. "
    "Before returning, verify every non-null row field against that same attachment. "
    "Map source fields by their visible role and the declared output field meaning. "
    "When a document has distinct entity, asset, description, note, or transaction "
    "columns, keep those roles distinct; never place one column's text into a "
    "different semantic field merely because the names are similar. "
    "Preserve source row order and repeated complete rows. Join wrapped "
    "lines or page-boundary continuations to their logical row, but never merge two "
    "separately printed rows. Copy literal source text character-for-character when "
    "a string output field represents a printed fact: do not drop leading date digits, "
    "alter punctuation, thousands separators, or whitespace inside a printed identifier, "
    "or expand an absent identifier from the entity's real-world identity. If the same "
    "printed identifier is visible both as a standalone output field and inside another "
    "extracted field from that row, those outputs must preserve the same exact token. "
    "When the caller requests a stock ticker or symbol, return the exact source "
    "identifier. For the common ASCII exchange-symbol grammar, letters and digits may "
    "be separated by meaningful periods or hyphens: GOOGL, 0700.HK, BRK.B, and BF-B "
    "are well-formed; GOOG L and visually similar non-ASCII letters are not. Express "
    "the source's actual grammar in the caller schema when it differs. "
    "For example, printed date 11/26/13 stays "
    "11/26/13, printed 1,000 stays 1,000, and an empty ticker or symbol cell stays "
    "null even if the company is recognizable. When a visible source legend defines "
    "an exact code, preserve the raw code in its matching source field and do not "
    "paraphrase or classify it; the caller can map it after extraction. Copy source "
    "facts faithfully, represent absent values as null, "
    "and never invent facts from world knowledge. Return only the strict "
    "structured response. Never emit an all-null placeholder row to satisfy an "
    "array minimum; every row must represent one actual source record. "
)


def _group_response_schema(
    normalized_schema: dict[str, Any],
    source_ids: list[str],
    *,
    min_rows_per_document: int = 0,
) -> dict[str, Any]:
    normalized_properties = normalized_schema.get("properties")
    if not isinstance(normalized_properties, Mapping):
        raise RowsSchemaError("normalized extraction schema has no properties")
    if not source_ids:
        raise RowsSchemaError("group response schema requires at least one source_id")
    document_properties = dict(normalized_properties)
    rows_property = document_properties.get("rows")
    if rows_property is not None:
        if not isinstance(rows_property, Mapping) or not _schema_has_type(
            rows_property, "array"
        ):
            raise RowsSchemaError(
                "normalized extraction schema rows property must be an array"
            )
        document_properties["rows"] = _array_schema_with_min_items(
            rows_property,
            min_rows_per_document,
        )
    elif min_rows_per_document > 0:
        raise RowsSchemaError("min_rows_per_document requires a rows_schema")
    document_properties = {
        "source_id": {"type": "string", "enum": source_ids},
        **document_properties,
    }
    # A single repeated item schema is substantially smaller and easier for the
    # extraction model to execute than one object property per source. The exact
    # item count plus source-id reconciliation below preserves corpus ownership.
    return _strict_object_schema(
        {
            "documents": {
                "type": "array",
                "minItems": len(source_ids),
                "maxItems": len(source_ids),
                "items": _strict_object_schema(document_properties),
            }
        }
    )


def _extract_pdf_document_group(
    group: list[tuple[str, bytes]],
    *,
    normalized_schema: dict[str, Any],
    rows_model: str | None,
    rows_retries: int,
    rows_pdf_max_bytes: int,
    min_rows_per_document: int = 0,
    instructions: str | None,
    extra_fields_by_key: "Mapping[str, dict[str, Any]] | None",
    idempotency_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    from nexustrade.host import (
        GatewayChatError,
        GatewayChatTransportError,
        gateway_chat_json,
        gateway_file_part,
        gateway_multimodal_messages,
    )

    oversized = [key for key, data in group if len(data) > rows_pdf_max_bytes]
    if oversized:
        raise ValueError(
            "multi-document extraction requires each PDF to fit rows_pdf_max_bytes; "
            f"oversized source_ids: {', '.join(oversized)}"
        )
    total_pdf_bytes = sum(len(data) for _, data in group)
    if total_pdf_bytes > DEFAULT_EXTRACT_ROWS_REQUEST_MAX_BYTES:
        raise GatewayChatTransportError(
            "multi-document extraction corpus exceeds the gateway's aggregate "
            f"attachment-byte limit ({total_pdf_bytes} > "
            f"{DEFAULT_EXTRACT_ROWS_REQUEST_MAX_BYTES})"
        )
    source_ids = [key for key, _ in group]
    mapping = [
        {"attachment": f"source-{index + 1}.pdf", "source_id": key}
        for index, (key, _) in enumerate(group)
    ]
    prompt_parts = [
        "# Source mapping",
        json.dumps(mapping, ensure_ascii=False),
    ]
    if instructions:
        prompt_parts.extend(["# Task instructions", instructions.strip()])
    else:
        prompt_parts.extend(
            ["# Task instructions", "Return every logical source-table row."]
        )
    attachments = [
        gateway_file_part(
            data,
            filename=f"source-{index + 1}.pdf",
            mime_type="application/pdf",
        )
        for index, (_, data) in enumerate(group)
    ]
    attempts = max(1, rows_retries + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        attempt_idempotency_key = (
            f"{idempotency_key}.{attempt}" if idempotency_key else None
        )
        try:
            response = gateway_chat_json(
                gateway_multimodal_messages("\n\n".join(prompt_parts), attachments),
                system=_EXTRACT_PDF_GROUP_SYSTEM,
                model=rows_model or os.environ.get("EXTRACT_ROWS_MODEL", "").strip()
                or DEFAULT_EXTRACT_ROWS_MODEL,
                json_schema=_group_response_schema(
                    normalized_schema,
                    source_ids,
                    min_rows_per_document=min_rows_per_document,
                ),
                schema_name="extract_pdf_documents",
                temperature=0,
                # Stream the single logical corpus request so reverse proxies
                # receive progress bytes while the provider performs a long
                # schema-bound extraction. This does not partition or retry the
                # model call.
                stream=True,
                # The NexusTrade-to-NexusGenAI hop owns a 15-minute upstream
                # bound. Leave response overhead so this client does not abort
                # first and strand a still-accounting logical request.
                timeout_sec=GROUP_EXTRACT_GATEWAY_TIMEOUT_SEC,
                max_transport_attempts=1,
                idempotency_key=attempt_idempotency_key,
            )
            if not isinstance(response, dict):
                raise RuntimeError("multi-document extraction omitted documents")
            raw_documents = response.get("documents")
            if isinstance(raw_documents, list):
                document_entries = []
                for raw in raw_documents:
                    if not isinstance(raw, dict):
                        raise RuntimeError("multi-document result is not an object")
                    document_entries.append((raw.get("source_id"), raw))
            elif isinstance(raw_documents, dict):
                # Backward-compatible parsing for durable results written before
                # the compact array response contract.
                document_entries = list(raw_documents.items())
            else:
                raise RuntimeError("multi-document extraction omitted documents")
            raw_by_source_id: dict[str, dict[str, Any]] = {}
            conflicting_source_ids: set[str] = set()
            for source_id, raw in document_entries:
                if not isinstance(source_id, str) or source_id not in source_ids:
                    # An unattributable extra result cannot invalidate the valid,
                    # requested document results returned alongside it. Any
                    # requested id displaced by it is surfaced as missing below.
                    continue
                if not isinstance(raw, dict):
                    conflicting_source_ids.add(source_id)
                    continue
                previous = raw_by_source_id.get(source_id)
                if previous is None:
                    raw_by_source_id[source_id] = raw
                elif previous != raw:
                    # Never choose silently between conflicting model outputs for
                    # the same source. Byte-equivalent duplicates are harmless.
                    conflicting_source_ids.add(source_id)

            grouped: dict[str, dict[str, Any]] = {}
            for source_id in source_ids:
                raw = raw_by_source_id.get(source_id)
                if source_id in conflicting_source_ids:
                    grouped[source_id] = {
                        "document": {},
                        "rows": [],
                        "needs_review": True,
                        "error": (
                            "multi-document extraction returned conflicting duplicate "
                            f"results for source_id {source_id!r}"
                        ),
                    }
                    continue
                if raw is None:
                    grouped[source_id] = {
                        "document": {},
                        "rows": [],
                        "needs_review": True,
                        "error": (
                            "multi-document extraction omitted source_id "
                            f"{source_id!r}"
                        ),
                    }
                    continue
                try:
                    rows = _rows_from_structured_result(raw)
                    has_placeholder_row = any(
                        not any(value is not None for value in row.values())
                        for row in rows
                    )
                    extra = (extra_fields_by_key or {}).get(source_id, {})
                    for row_index, row in enumerate(rows):
                        row.update(extra)
                        row["source_id"] = source_id
                        row["_source_row_index"] = row_index
                    document = _document_from_structured_result(raw)
                    document.update(extra)
                    document["source_id"] = source_id
                    grouped[source_id] = {
                        "document": document,
                        "rows": rows,
                        "markdown": "",
                        "page_audit": [],
                        # The grouped raw-PDF vision path does not run the legacy
                        # OCR/layout audit. `None` means unmeasured, not zero visible
                        # rows. A schema-valid empty extraction therefore remains a
                        # review condition instead of masquerading as confirmed
                        # evidence that the PDF contains no requested records.
                        "apparent_table_rows": None,
                        "needs_review": not rows or has_placeholder_row,
                        "pdf_attached": True,
                        "error": None,
                    }
                except Exception as exc:
                    grouped[source_id] = {
                        "document": {},
                        "rows": [],
                        "needs_review": True,
                        "error": (
                            "multi-document extraction returned an invalid result for "
                            f"source_id {source_id!r}: {exc}"
                        ),
                    }
            local_errors = [
                payload["error"]
                for payload in grouped.values()
                if payload.get("error") is not None
            ]
            if local_errors and attempt + 1 < attempts:
                raise RuntimeError(str(local_errors[0]))
            return grouped
        except GatewayChatError:
            raise
        except Exception as exc:  # schema/content retry only
            last_error = exc
            if attempt + 1 == attempts:
                break
    raise _RowsStructuringError(
        f"multi-document extraction failed after {attempts} attempt(s): {last_error}"
    ) from last_error


def _extract_pdf_document_groups(
    items: list[tuple[str, bytes]],
    *,
    request_keys: dict[str, str],
    batch_key: str,
    normalized_schema: dict[str, Any],
    rows_model: str | None,
    rows_retries: int,
    rows_pdf_max_bytes: int,
    min_rows_per_document: int = 0,
    instructions: str | None,
    documents_per_request: int,
    max_workers: int,
    extra_fields_by_key: "Mapping[str, dict[str, Any]] | None",
    request_keys_for_group: (
        "Callable[[list[tuple[str, bytes]]], dict[str, str]] | None"
    ) = None,
    initial_results: "Mapping[str, dict[str, Any]] | None" = None,
    result_order: "Sequence[str] | None" = None,
) -> dict[str, dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, dict[str, Any]] = dict(initial_results or {})
    ordered_ids = (
        list(result_order) if result_order is not None else [key for key, _ in items]
    )
    total_documents = len(ordered_ids)
    groups = _partition_pdf_document_groups(
        items,
        documents_per_request=documents_per_request,
    )
    cache_hits = 0
    completed = sum(payload.get("error") is None for payload in results.values())
    failed = len(results) - completed

    def run_exact_group(
        group: list[tuple[str, bytes]],
        group_request_keys: dict[str, str] | None,
    ) -> dict[str, dict[str, Any]]:
        return _extract_pdf_document_group(
            group,
            normalized_schema=normalized_schema,
            rows_model=rows_model,
            rows_retries=rows_retries,
            rows_pdf_max_bytes=rows_pdf_max_bytes,
            min_rows_per_document=min_rows_per_document,
            instructions=instructions,
            extra_fields_by_key=extra_fields_by_key,
            idempotency_key=(
                _document_batch_key(group_request_keys)
                if group_request_keys is not None
                else None
            ),
        )

    def exact_keys(
        group: list[tuple[str, bytes]],
        fallback: dict[str, str] | None = None,
    ) -> dict[str, str] | None:
        if request_keys_for_group is not None:
            return request_keys_for_group(group)
        return fallback

    def lookup_exact_group(
        group: list[tuple[str, bytes]],
        group_request_keys: dict[str, str] | None,
    ) -> dict[str, dict[str, Any]] | None:
        if group_request_keys is None:
            return None
        replays: dict[str, dict[str, Any]] = {}
        for key, _data in group:
            payload = _document_result_lookup(group_request_keys[key])
            if payload is None:
                return None
            replays[key] = payload
        return replays

    def run_group(
        group: list[tuple[str, bytes]],
        group_request_keys: dict[str, str] | None,
    ) -> tuple[dict[str, dict[str, Any]], int, int, int]:
        replay = lookup_exact_group(group, group_request_keys)
        if replay is not None:
            return replay, len(replay), 0, len(replay)
        try:
            extracted = run_exact_group(group, group_request_keys)
        except Exception as exc:
            return (
                {
                    key: {"document": {}, "rows": [], "error": str(exc)}
                    for key, _data in group
                },
                0,
                len(group),
                0,
            )
        if group_request_keys is not None:
            for key, payload in extracted.items():
                _document_result_record(
                    batch_key=batch_key,
                    request_key=group_request_keys[key],
                    document_id=key,
                    payload=payload,
                    total=total_documents,
                    track_progress=False,
                )
        extracted_completed = sum(
            payload.get("error") is None for payload in extracted.values()
        )
        return (
            extracted,
            extracted_completed,
            len(extracted) - extracted_completed,
            0,
        )

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(groups) or 1))) as pool:
        futures = {
            pool.submit(
                run_group,
                group,
                exact_keys(
                    group,
                    {key: request_keys[key] for key, _data in group},
                ),
            ): group
            for group in groups
        }
        for future in as_completed(futures):
            group = futures[future]
            try:
                extracted, group_completed, group_failed, group_cache_hits = (
                    future.result()
                )
            except Exception as exc:
                extracted = {
                    key: {"document": {}, "rows": [], "error": str(exc)}
                    for key, _ in group
                }
                group_completed = 0
                group_failed = len(group)
                group_cache_hits = 0
            results.update(extracted)
            completed += group_completed
            failed += group_failed
            cache_hits += group_cache_hits
            _document_batch_progress(
                batch_key=batch_key,
                total=total_documents,
                completed=completed,
                failed=failed,
                cache_hits=cache_hits,
                done=False,
            )
    _document_batch_progress(
        batch_key=batch_key,
        total=total_documents,
        completed=completed,
        failed=failed,
        cache_hits=cache_hits,
        done=True,
    )
    return {key: results[key] for key in ordered_ids}


def _prepare_pdf_document(
    source_id: str,
    value: bytes | bytearray | memoryview | Mapping[str, Any],
) -> tuple[bytes | None, str | None]:
    if isinstance(value, bytes):
        return value, None
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value), None
    if isinstance(value, Mapping):
        if value.get("ok") is not True:
            return None, f"fetch result for {source_id!r} is not successful"
        from nexustrade.tigris import read_fetch_result

        try:
            pdf_bytes = read_fetch_result(dict(value))
        except Exception as exc:  # one bad receipt must not hide the other sources
            return None, f"fetch result for {source_id!r} could not be read: {exc}"
        if pdf_bytes is None:
            return None, f"fetch result for {source_id!r} has no staged body"
        return pdf_bytes, None
    return None, (
        f"document {source_id!r} must be PDF bytes or a host.fetch result, "
        f"got {type(value).__name__}"
    )


def extract_pdfs(
    documents: (
        "Mapping[str, bytes | Mapping[str, Any]] | "
        "Sequence[tuple[str, bytes | Mapping[str, Any]]]"
    ),
    *,
    max_workers: int | None = None,
    max_attempts: int = 3,
    retry_backoff_s: float = 0.5,
    markdown: bool = False,
    extra_fields_by_key: "Mapping[str, dict[str, Any]] | None" = None,
    max_pages: int | None = None,
    target_schema: Any = None,
    document_schema: dict[str, Any] | None = None,
    rows_schema: dict[str, Any] | None = None,
    rows_model: str | None = None,
    rows_retries: int = 0,
    rows_include_pdf: bool = True,
    rows_pdf_max_bytes: int = DEFAULT_EXTRACT_ROWS_PDF_MAX_BYTES,
    min_rows_per_document: int = 0,
    instructions: str | None = None,
    documents_per_request: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Extract a PDF corpus with one schema-bound multi-document request.

    When `rows_schema` is supplied, every eligible PDF shares one Luna request by
    default. By default, a transport or structured-output failure fails that
    exact logical group visibly; it is not converted into multiple paid model
    requests. Set `rows_retries` explicitly only when retrying the same peer
    corpus is desired. Set a positive `documents_per_request` only to
    intentionally partition the corpus; pass 1 to use the compatibility
    OCR-plus-one-document path. `instructions`
    supplies concise task semantics, including requested inclusion/exclusion rules;
    the helper remains agnostic to the source domain.

    `documents` may contain PDF bytes or successful `host.fetch` result objects;
    fetch receipts are hydrated internally. Returns
    {key: {"rows"|"markdown": ..., "error": str | None}} — one entry per
    input, ALWAYS. A document that fails is reported rather than dropped, so a
    partial batch is visible instead of looking like a smaller-but-clean result.

    `min_rows_per_document` is an optional caller-declared invariant for the
    selected source class. Set it to 1, for example, only when every selected
    report is definitionally expected to contain at least one transaction. It
    constrains the same one-call schema; it does not add a validation call or a
    retry. Leave it at zero when an empty document is valid.

    The legacy path retries transient failures per document (max_attempts,
    exponential backoff). The grouped path gives each exact group one transport
    attempt. Explicit `documents_per_request` and the real aggregate byte ceiling
    are its only partition boundaries. A later remediation may retry the same
    logical request; it does not silently change peer context or multiply spend.

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

    Pass `document_schema` alongside `rows_schema` for facts present once per
    document. Both shapes are returned by the same schema-bound call as
    `{"document": {...}, "rows": [...]}`. Pass trusted caller or publisher
    inventory metadata through `extra_fields_by_key`; it is stamped mechanically
    on each document and row in grouped and serial paths instead of being
    re-extracted from PDF contents.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from nexustrade.host import GatewayChatError

    # Validate once for the whole batch rather than once per document. This is
    # a programming error in the caller's schema, not a per-document outcome, so
    # it raises instead of returning 62 identical `error` entries after paying
    # for 62 OCRs.
    if (
        not isinstance(min_rows_per_document, int)
        or isinstance(min_rows_per_document, bool)
        or min_rows_per_document < 0
    ):
        raise ValueError("min_rows_per_document must be a non-negative integer")
    if min_rows_per_document > 0 and rows_schema is None:
        raise ValueError("min_rows_per_document requires rows_schema")

    if rows_schema is not None or document_schema is not None:
        if rows_pdf_max_bytes < 1:
            raise ValueError("rows_pdf_max_bytes must be positive")
        normalized_schema = normalize_extraction_schema(
            rows_schema=rows_schema,
            document_schema=document_schema,
            min_rows=min_rows_per_document,
        )
        document_schema = (
            normalize_document_schema(document_schema)
            if document_schema is not None
            else None
        )
        rows_schema = (
            normalize_rows_schema(rows_schema) if rows_schema is not None else None
        )
        if documents_per_request is not None and documents_per_request < 1:
            raise ValueError("documents_per_request must be positive when supplied")
        if instructions is not None and not instructions.strip():
            raise ValueError("instructions must be non-empty when supplied")
    else:
        normalized_schema = None
        if instructions is not None:
            raise ValueError("instructions requires rows_schema or document_schema")

    raw_items = (
        list(documents.items()) if hasattr(documents, "items") else list(documents)
    )
    result_order = [key for key, _ in raw_items]
    results: dict[str, dict[str, Any]] = {}
    items: list[tuple[str, bytes]] = []
    for key, value in raw_items:
        pdf_bytes, error = _prepare_pdf_document(key, value)
        if pdf_bytes is not None:
            items.append((key, pdf_bytes))
            continue
        if normalized_schema is not None:
            results[key] = {"document": {}, "rows": [], "error": error}
        else:
            results[key] = {
                "markdown" if markdown else "rows": "" if markdown else [],
                "error": error,
            }
    if not raw_items:
        return results
    if not items:
        return {key: results[key] for key in result_order}
    total_documents = len(raw_items)
    effective_documents_per_request = (
        len(items) if documents_per_request is None else documents_per_request
    )

    use_group_extraction = (
        normalized_schema is not None
        and rows_schema is not None
        and len(items) > 1
        and effective_documents_per_request > 1
        and max_pages is None
        and rows_include_pdf
        and all(len(pdf_bytes) <= rows_pdf_max_bytes for _, pdf_bytes in items)
    )
    replay_documents_per_request = (
        effective_documents_per_request if use_group_extraction else 1
    )
    group_request_keys_by_key: dict[str, str] = {}
    if use_group_extraction:
        for group in _partition_pdf_document_groups(
            items,
            documents_per_request=effective_documents_per_request,
        ):
            group_request_keys_by_key.update(
                _document_group_request_keys(
                    group,
                    markdown=markdown,
                    max_pages=max_pages,
                    target_schema=target_schema,
                    document_schema=document_schema,
                    rows_schema=rows_schema,
                    rows_model=rows_model,
                    rows_retries=rows_retries,
                    rows_include_pdf=rows_include_pdf,
                    rows_pdf_max_bytes=rows_pdf_max_bytes,
                    min_rows_per_document=min_rows_per_document,
                    instructions=instructions,
                    extra_fields_by_key=extra_fields_by_key,
                )
            )

    # Compute sessions route every schema-bound LLM call through a ledger-backed
    # dollar gate. An unbounded default here used to turn a 100-document corpus
    # into 100 simultaneous paid-call admission attempts. The host injects a
    # small session default; standalone callers can still choose explicitly.
    configured_workers = int(os.environ.get("NEXUSTRADE_DOCUMENT_MAX_WORKERS", "2"))
    if configured_workers < 1:
        raise ValueError("NEXUSTRADE_DOCUMENT_MAX_WORKERS must be positive")
    workers = (
        min(configured_workers, len(items))
        if max_workers is None
        else max(1, min(int(max_workers), len(items)))
    )
    stop = False

    request_keys = {
        key: group_request_keys_by_key.get(key)
        or _document_request_key(
            key,
            pdf_bytes,
            markdown=markdown,
            max_pages=max_pages,
            target_schema=target_schema,
            document_schema=document_schema,
            rows_schema=rows_schema,
            rows_model=rows_model,
            rows_retries=rows_retries,
            rows_include_pdf=rows_include_pdf,
            rows_pdf_max_bytes=rows_pdf_max_bytes,
            min_rows_per_document=min_rows_per_document,
            rows_force_ocr=True,
            rows_schema_name="extract_rows",
            instructions=instructions,
            documents_per_request=replay_documents_per_request,
            group_context_key=None,
            extra_fields=(extra_fields_by_key or {}).get(key),
        )
        for key, pdf_bytes in items
    }
    batch_key = _document_batch_key(request_keys)
    try:
        _gateway_json(
            "document-extractions/begin",
            {"batchKey": batch_key, "total": total_documents},
        )
    except Exception as exc:  # noqa: BLE001 - record calls remain authoritative
        print(f"document extraction batch registration failed: {exc}")

    if use_group_extraction:
        return _extract_pdf_document_groups(
            items,
            request_keys=request_keys,
            batch_key=batch_key,
            normalized_schema=normalized_schema,
            rows_model=rows_model,
            rows_retries=rows_retries,
            rows_pdf_max_bytes=rows_pdf_max_bytes,
            min_rows_per_document=min_rows_per_document,
            instructions=instructions,
            documents_per_request=effective_documents_per_request,
            max_workers=workers,
            extra_fields_by_key=extra_fields_by_key,
            request_keys_for_group=lambda group: _document_group_request_keys(
                group,
                markdown=markdown,
                max_pages=max_pages,
                target_schema=target_schema,
                document_schema=document_schema,
                rows_schema=rows_schema,
                rows_model=rows_model,
                rows_retries=rows_retries,
                rows_include_pdf=rows_include_pdf,
                rows_pdf_max_bytes=rows_pdf_max_bytes,
                min_rows_per_document=min_rows_per_document,
                instructions=instructions,
                extra_fields_by_key=extra_fields_by_key,
            ),
            initial_results=results,
            result_order=result_order,
        )

    def run_once(key: str, pdf_bytes: bytes) -> dict[str, Any]:
        if normalized_schema is not None:
            extracted = extract_rows(
                pdf_bytes,
                schema=rows_schema,
                document_schema=document_schema,
                model=rows_model,
                max_pages=max_pages,
                source_id=key,
                retries=rows_retries,
                include_pdf=rows_include_pdf,
                pdf_max_bytes=rows_pdf_max_bytes,
                min_rows=min_rows_per_document,
                instructions=instructions,
                _durable_replay=False,
            )
            extra = (extra_fields_by_key or {}).get(key, {})
            document = {**extracted.document, **extra, "source_id": key}
            rows: list[dict[str, Any]] = []
            for row_index, extracted_row in enumerate(extracted.rows):
                row = {**extracted_row, **extra}
                row["source_id"] = key
                row["_source_row_index"] = row_index
                rows.append(row)
            return {
                "document": document,
                "rows": rows,
                "markdown": extracted.markdown,
                "page_audit": extracted.page_audit,
                "apparent_table_rows": extracted.apparent_table_rows,
                "needs_review": extracted.needs_review,
                "pdf_attached": extracted.pdf_attached,
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
        replay = _document_result_lookup(request_keys[key])
        if replay is not None:
            return replay, True

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
            except _RowsStructuringError:
                # extract_rows already spent its caller-owned schema retry. A
                # second document-level wave repeats the same paid semantic
                # request without changing the OCR, schema, or source bytes.
                raise
            except Exception as exc:
                last = exc
                if attempt + 1 < max_attempts:
                    time.sleep(retry_backoff_s * (2**attempt))
        if result is None:
            raise last if last else RuntimeError("ocr failed with no exception")
        try:
            _document_result_record(
                batch_key=batch_key,
                request_key=request_keys[key],
                document_id=key,
                payload=result,
                total=total_documents,
            )
        except Exception as exc:
            raise RuntimeError(
                f"validated extraction could not be durably committed: {exc}"
            ) from exc
        return result, False

    empty = "markdown" if markdown else "rows"

    def unstarted(reason: str) -> dict[str, Any]:
        if normalized_schema is not None:
            return {"document": {}, "rows": [], "error": reason}
        return {empty: "" if markdown else [], "error": reason}

    # Submit incrementally, keeping at most `workers` in flight. Submitting the
    # whole batch up front would make budget backpressure a no-op: every document
    # is already scheduled by the time the first 429 comes back.
    pending = list(items)
    completed = sum(payload.get("error") is None for payload in results.values())
    failed = len(results) - completed
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
                result, replayed = done.result()
                results[key] = result
                completed += 1
                if replayed:
                    cache_hits += 1
            except OcrBudgetExhausted as exc:
                stop = True
                results[key] = unstarted(f"budget_exhausted: {exc}")
                failed += 1
            except Exception as exc:  # per-document: one bad file cannot lose the rest
                results[key] = unstarted(str(exc))
                failed += 1
            _document_batch_progress(
                batch_key=batch_key,
                total=total_documents,
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
        total=total_documents,
        completed=completed,
        failed=failed,
        cache_hits=cache_hits,
        done=True,
    )
    return {key: results[key] for key in result_order}

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
                "page_number": page_index + 1,
                "page_index_base": 0,
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

_EXTRACT_ROWS_SYSTEM = (
    "Complete the caller's schema-bound task from the source document as JSON "
    "matching the provided schema. Apply caller task instructions, including "
    "requested inclusion and exclusion semantics, when present; otherwise recover "
    "every logical source-table row. When the schema contains `document`, populate "
    "it once from document-level headers, labels, legends, and metadata; do not "
    "repeat or infer those facts per row. When the schema contains `rows`, recover "
    "the logical source-table rows. "
    "Use the attached PDF as the primary visual source when present and the OCR "
    "markdown as a structured second view. Ground every answer in observations "
    "visible in the row, its table "
    "headers, an inspected source legend, or document metadata included in the "
    "input. Use schema field names, descriptions, and caller instructions to locate "
    "and interpret those observations. Copy literal source text character-for-character "
    "when a string field represents a printed fact: 11/26/13 must not become 1/26/13, "
    "1,000 must not become 1.000, and an empty ticker or symbol stays null even "
    "when the entity is recognizable. If a visible legend defines an exact source "
    "code, preserve that raw code in its matching field without paraphrasing or "
    "classifying it; the caller can map it after extraction. "
    "A requested observation may be embedded inside another table cell rather "
    "than have a standalone column. Apply only the task-specific selection and "
    "interpretation the caller requests. Copy stable printed facts rather than "
    "regenerating them, and never fill a field from world knowledge. When the "
    "requested source "
    "observation is absent or ambiguous, return null.\n\n"
    "Emit exactly one object per logical source row and preserve source order. "
    "Two complete rows that look identical are two separate rows and must both "
    "appear. Wrapped lines and page-boundary continuations belong to their source "
    "row: combine the fragments into that one object and never emit a continuation "
    "as another row. Preserve separate columns, codes, descriptions, and bracketed "
    "values in their matching schema fields; a plausible value in one field never "
    "authorizes copying or interpreting it as another field. Preserve conflicts "
    "unless the caller's task and complete source evidence resolve them."
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
    pdf_attached: bool = False
    # Append new fields so legacy positional construction keeps its meaning.
    document: dict[str, Any] = field(default_factory=dict)

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


def _document_from_structured_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    document = result.get("document")
    return dict(document) if isinstance(document, dict) else {}


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
    schema: Mapping[str, Any],
    *,
    force_required: frozenset[str] = frozenset(),
    preserve_declared_required: bool = False,
) -> dict[str, Any]:
    """Recursively adapt JSON Schema to strict structured-output semantics.

    Strict structured output requires every property name. Source extraction
    cannot also require every observation to be non-null: an absent source value
    would force the structuring model to fabricate one. Ordinary properties are
    therefore required-but-nullable unless a full caller schema explicitly marks
    them required and asks us to preserve that declaration. `force_required` is
    reserved for protocol fields such as the non-null `rows` envelope.
    """
    out = dict(schema)
    explicitly_nullable = _schema_allows_null(schema)

    for union_key in ("anyOf", "oneOf", "allOf"):
        branches = out.get(union_key)
        if isinstance(branches, list):
            out[union_key] = [
                _strict_json_schema(
                    branch,
                    preserve_declared_required=preserve_declared_required,
                )
                if isinstance(branch, Mapping)
                else branch
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

        normalized_properties: dict[str, Any] = {}
        for field_name, property_schema in properties.items():
            if not isinstance(field_name, str) or not isinstance(
                property_schema, Mapping
            ):
                raise RowsSchemaError(
                    "rows_schema object properties must map field names to schemas"
                )
            normalized = _strict_json_schema(
                property_schema,
                preserve_declared_required=preserve_declared_required,
            )
            if field_name not in force_required and not (
                preserve_declared_required and field_name in declared_required
            ):
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
        out["items"] = _strict_json_schema(
            items,
            preserve_declared_required=preserve_declared_required,
        )

    return _nullable_schema(out) if explicitly_nullable else out


def _array_schema_with_min_items(
    schema: Mapping[str, Any], requested_minimum: int
) -> dict[str, Any]:
    """Preserve a caller's stronger array minimum while applying ours."""
    existing_minimum = schema.get("minItems", 0)
    if (
        not isinstance(existing_minimum, int)
        or isinstance(existing_minimum, bool)
        or existing_minimum < 0
    ):
        raise RowsSchemaError("array minItems must be a non-negative integer")

    out = dict(schema)
    effective_minimum = max(existing_minimum, requested_minimum)
    if effective_minimum > 0:
        out["minItems"] = effective_minimum
    return out


def _rows_envelope(
    row_schema: dict[str, Any], *, min_rows: int = 0
) -> dict[str, Any]:
    rows = _array_schema_with_min_items(
        {"type": "array", "items": row_schema},
        min_rows,
    )
    return _strict_object_schema({"rows": rows})


def normalize_rows_schema(rows_schema: Any, *, min_rows: int = 0) -> dict[str, Any]:
    """Canonicalize rows_schema to the strict `{"rows": [...]}` envelope.

    `_rows_from_structured_result` needs a `rows` array, but nothing used to say
    so and nothing enforced it, so a caller could pass a schema that validated
    fine and still lose every row. Accepts every shape callers actually pass:

      {"ticker": "string", "amount": "number"}   - field -> scalar type name
      {"ticker": {"type": "string"}}             - field -> property schema
      {"type": "object", "properties": {...}}    - one ROW's object schema
      {"type": "object", "properties":
          {"rows": {"type": "array", ...}}}      - already the envelope

    Shorthand source properties become required-but-nullable in the provider
    schema. A full JSON Schema object's explicitly declared `required` row fields
    retain their non-null types; its other fields become nullable. Strict mode
    still returns every key without forcing an invented optional value. The
    reserved `rows` protocol envelope remains required and non-null.

    Anything else raises rather than reaching the provider as an invalid schema.
    """
    if not isinstance(min_rows, int) or isinstance(min_rows, bool) or min_rows < 0:
        raise RowsSchemaError("min_rows must be a non-negative integer")
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
            normalized = _strict_json_schema(
                rows_schema,
                force_required=frozenset({"rows"}),
                preserve_declared_required=True,
            )
            normalized["properties"]["rows"] = _array_schema_with_min_items(
                normalized["properties"]["rows"],
                min_rows,
            )
            return normalized

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
        return _rows_envelope(
            _strict_json_schema(
                rows_schema,
                preserve_declared_required=True,
            ),
            min_rows=min_rows,
        )

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

    return _rows_envelope(
        _strict_json_schema(_strict_object_schema(row_properties)),
        min_rows=min_rows,
    )


def normalize_document_schema(
    document_schema: Any, *, preserve_declared_required: bool = False
) -> dict[str, Any]:
    """Canonicalize one document/header object for strict structured output.

    PDF source fields remain required-but-nullable by default. Generic web
    extraction sets ``preserve_declared_required`` so an explicit caller schema
    keeps its ordinary non-null guarantees instead of silently accepting null.
    """
    if not isinstance(document_schema, Mapping) or not document_schema:
        raise RowsSchemaError(
            "document_schema must be a non-empty dict describing one document object"
        )
    looks_like_json_schema = any(
        key in document_schema for key in _JSON_SCHEMA_MARKERS
    )
    if looks_like_json_schema:
        normalized = _strict_json_schema(
            document_schema,
            preserve_declared_required=preserve_declared_required,
        )
        if not _schema_has_type(normalized, "object"):
            raise RowsSchemaError("document_schema must describe one object")
        return normalized

    properties: dict[str, Any] = {}
    for field_name, spec in document_schema.items():
        name = str(field_name)
        if isinstance(spec, Mapping):
            properties[name] = _strict_json_schema(spec)
            continue
        if spec is None:
            properties[name] = {"type": "string"}
            continue
        if isinstance(spec, str):
            json_type = _ROWS_SCHEMA_SCALAR_TYPES.get(spec.strip().lower())
            if json_type is None:
                raise RowsSchemaError(
                    f"document_schema field {name!r} names an unknown type {spec!r}; "
                    f"use one of {sorted(set(_ROWS_SCHEMA_SCALAR_TYPES))}"
                )
            properties[name] = {"type": json_type}
            continue
        raise RowsSchemaError(
            f"document_schema field {name!r} must map to a type name or a property "
            f"schema, got {type(spec).__name__}"
        )
    return _strict_json_schema(_strict_object_schema(properties))


def normalize_extraction_schema(
    *,
    rows_schema: Any = None,
    document_schema: Any = None,
    min_rows: int = 0,
) -> dict[str, Any]:
    """Build the strict one-call document plus logical rows response schema."""
    if rows_schema is None and document_schema is None:
        raise RowsSchemaError(
            "extract_rows requires rows_schema, document_schema, or both"
        )
    properties: dict[str, Any] = {}
    if document_schema is not None:
        properties["document"] = normalize_document_schema(document_schema)
    if rows_schema is not None:
        normalized_rows = normalize_rows_schema(rows_schema, min_rows=min_rows)
        rows_property = normalized_rows["properties"]["rows"]
        properties["rows"] = rows_property
    return _strict_object_schema(properties)


def extract_rows(
    pdf_bytes: bytes,
    *,
    schema: dict[str, Any] | None = None,
    document_schema: dict[str, Any] | None = None,
    model: str | None = None,
    max_pages: int | None = None,
    force_ocr: bool = True,
    schema_name: str = "extract_rows",
    source_id: str | None = None,
    retries: int = 1,
    include_pdf: bool = True,
    pdf_max_bytes: int = DEFAULT_EXTRACT_ROWS_PDF_MAX_BYTES,
    min_rows: int = 0,
    instructions: str | None = None,
    _durable_replay: bool = True,
) -> ExtractedRows:
    """OCR the document, then structure the markdown with a cheap schema-bound LLM.

    Replaces hand-written regex over OCR markdown \u2014 the defect class measured on
    2026-07-22 (page-boundary wraps, ticker/asset-code collisions, transaction
    type read from neighbouring prose). Mistral emits clean inline pipe tables; a
    schema-bound text model structures them at ~$0.0002/document.

    Bare json_object mode does NOT bind every model \u2014 always pass an explicit
    schema. Without one, some provider routes return prose fragments.

    `schema` describes ONE SOURCE ROW and is normalized by
    `normalize_rows_schema`, so
    the flat `{"ticker": "string", "amount": "number"}` shorthand and a full
    JSON Schema object are both accepted; the `{"rows": [...]}` envelope strict
    mode needs is added for you. An uninterpretable schema raises
    `RowsSchemaError` before any OCR or gateway call.

    Schema keys are always present. Shorthand and optional source observations
    are nullable so extraction does not invent values merely to satisfy strict
    output; a full schema may explicitly declare guaranteed row anchors required.
    `instructions` may define task-specific inclusion, exclusion, and
    interpretation semantics for the complete source record. Mechanical
    calculations and downstream composition can remain ordinary code.

    `min_rows` is a caller-declared source invariant, not an estimate. Set it
    only when the selected document class guarantees that many logical rows;
    strict structured output then cannot satisfy the request with an empty
    shell. Leave it at zero when a valid document may contain no qualifying row.

    By default the structuring model receives both the original PDF and cached
    OCR markdown. The PDF resolves visual page boundaries and embedded cells;
    the markdown preserves the auditable text view. PDFs larger than
    `pdf_max_bytes` use OCR-only structuring rather than constructing an
    unbounded base64 request. Set `include_pdf=False` to force OCR-only mode.

    `source_id` and a host-owned zero-based `_source_row_index` are stamped onto
    every returned row. The host derives per-document reconciliation from
    `source_id`; the row index keeps every extracted source row distinct when
    callers build a transaction ledger or combine batch and serial fallback
    results.

    `document_schema` describes observations that occur once per source document
    rather than once per logical row. When supplied, document facts and rows are
    recovered in the same structured call and returned on `ExtractedRows.document`.
    `inspect_document` is an optional targeted diagnostic after this batch, not a
    prerequisite for binding the initial extraction schema.
    """
    # Before the OCR hop, not after: a schema the provider will reject costs
    # nothing to catch here and a full document's OCR to catch downstream.
    normalized_schema = normalize_extraction_schema(
        rows_schema=schema,
        document_schema=document_schema,
        min_rows=min_rows,
    )
    normalized_rows_schema = (
        normalize_rows_schema(schema, min_rows=min_rows)
        if schema is not None
        else None
    )
    normalized_document_schema = (
        normalize_document_schema(document_schema)
        if document_schema is not None
        else None
    )

    from nexustrade.host import (
        GatewayChatError,
        GatewayChatTransportError,
        gateway_chat_json,
        gateway_file_part,
        gateway_multimodal_messages,
    )

    if pdf_max_bytes < 1:
        raise ValueError("pdf_max_bytes must be positive")
    if instructions is not None and not instructions.strip():
        raise ValueError("instructions must be non-empty when supplied")

    configured = os.environ.get("EXTRACT_ROWS_MODEL", "").strip()
    structure_model = model or configured or DEFAULT_EXTRACT_ROWS_MODEL
    request_key: str | None = None
    batch_key: str | None = None
    document_id = source_id or f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}"
    if _durable_replay:
        request_key = _document_request_key(
            document_id,
            pdf_bytes,
            markdown=False,
            max_pages=max_pages,
            target_schema=None,
            document_schema=normalized_document_schema,
            rows_schema=normalized_rows_schema,
            rows_model=structure_model,
            rows_retries=retries,
            rows_include_pdf=include_pdf,
            rows_pdf_max_bytes=pdf_max_bytes,
            min_rows_per_document=min_rows,
            rows_force_ocr=force_ocr,
            rows_schema_name=schema_name,
            instructions=instructions,
            extra_fields=None,
        )
        batch_key = _document_batch_key({document_id: request_key})
        replay = _document_result_lookup(request_key)
        if replay is not None:
            replay_rows = replay.get("rows")
            replay_document = replay.get("document", {})
            replay_markdown = replay.get("markdown")
            replay_page_audit = replay.get("page_audit")
            if (
                not isinstance(replay_rows, list)
                or not isinstance(replay_document, dict)
                or not isinstance(replay_markdown, str)
                or not isinstance(replay_page_audit, list)
            ):
                raise RuntimeError("document extraction replay payload is malformed")
            return ExtractedRows(
                document=replay_document,
                rows=replay_rows,
                markdown=replay_markdown,
                source_id=source_id,
                page_audit=replay_page_audit,
                apparent_table_rows=int(replay.get("apparent_table_rows") or 0),
                needs_review=replay.get("needs_review") is True,
                pdf_attached=replay.get("pdf_attached") is True,
            )

    markdown, page_audit = extract_pdf_markdown_with_audit(
        pdf_bytes, max_pages=max_pages, force_ocr=force_ocr
    )
    apparent_table_rows = sum(
        int(page.get("apparent_table_rows") or 0) for page in page_audit
    )
    needs_review = any(page.get("needs_review") is True for page in page_audit)
    user_prompt = markdown
    if source_id:
        user_prompt = f"source_id: {source_id}\n\n{markdown}"
    if instructions:
        user_prompt = (
            f"# Task instructions\n{instructions.strip()}\n\n"
            f"# Source document\n{user_prompt}"
        )
    pdf_attached = include_pdf and len(pdf_bytes) <= pdf_max_bytes
    chat_input: dict[str, Any]
    if pdf_attached:
        chat_input = {
            "messages": gateway_multimodal_messages(
                user_prompt,
                [
                    gateway_file_part(
                        pdf_bytes,
                        filename="source.pdf",
                        mime_type="application/pdf",
                    )
                ],
            )
        }
    else:
        chat_input = {"prompt": user_prompt}

    attempts = max(1, retries + 1)
    document: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for attempt in range(attempts):
        try:
            result = gateway_chat_json(
                **chat_input,
                system=_EXTRACT_ROWS_SYSTEM,
                model=structure_model,
                json_schema=normalized_schema,
                schema_name=schema_name,
                temperature=0,
            )
            document = _document_from_structured_result(result)
            rows = (
                _rows_from_structured_result(result) if schema is not None else []
            )
            if schema is not None and not rows and apparent_table_rows > 0:
                raise RuntimeError(
                    "structured output returned zero rows despite "
                    f"{apparent_table_rows} apparent OCR table row(s)"
                )
            break
        except GatewayChatTransportError:
            # A PDF+OCR multimodal request can repeatedly outlive an HTTP edge
            # even when the already-extracted OCR is complete and sufficient.
            # Spend the one caller-owned semantic retry on a materially smaller
            # OCR-only request instead of repeating the same 524-prone payload.
            if pdf_attached and attempt < attempts - 1:
                pdf_attached = False
                chat_input = {"prompt": user_prompt}
                continue
            raise
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
        document["source_id"] = source_id
        for row_index, row in enumerate(rows):
            row["source_id"] = source_id
            row["_source_row_index"] = row_index
    if needs_review:
        for row in rows:
            row.setdefault("_needs_review", True)
            row.setdefault("_reason", "ocr_page_requires_review")

    extracted = ExtractedRows(
        document=document,
        rows=rows,
        markdown=markdown,
        source_id=source_id,
        page_audit=page_audit,
        apparent_table_rows=apparent_table_rows,
        needs_review=needs_review,
        pdf_attached=pdf_attached,
    )
    if _durable_replay and request_key is not None and batch_key is not None:
        _document_result_record(
            batch_key=batch_key,
            request_key=request_key,
            document_id=document_id,
            payload={
                "document": extracted.document,
                "rows": extracted.rows,
                "markdown": extracted.markdown,
                "page_audit": extracted.page_audit,
                "apparent_table_rows": extracted.apparent_table_rows,
                "needs_review": extracted.needs_review,
                "pdf_attached": extracted.pdf_attached,
                "error": None,
            },
            total=1,
            track_progress=False,
        )
    return extracted


_EXTRACT_WEB_SYSTEM = """# Task
Complete the caller's schema-bound extraction task from every supplied web page. Follow the
caller's requested scope exactly; do not omit requested information because it resembles another
observation.

# Source boundary
- Return exactly one document result for every supplied source_id and no other source_id.
- Treat each page as isolated. Use only its title, description, publication hint, and visible text.
- Never infer facts from the URL, another page, or general knowledge.
- Publisher metadata supports only the exact statement it contains.
- A generic application shell or landing page with no task-relevant content is not evidence.

# Output check
- Preserve conflicts and missing information in the caller-provided schema.
- If the schema asks for an evidence excerpt, copy one contiguous exact source substring.
- Before returning, verify every such excerpt after whitespace normalization. Never join passages,
  insert ellipses, or keep an observation whose excerpt cannot be verified.
- Return only the strict structured response."""


def _bounded_web_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    # News/report substance normally starts near the beginning, while update
    # notes and methodology often sit at the end. Preserve both as exact source
    # substrings instead of injecting a synthetic truncation marker that a model
    # could accidentally quote as evidence.
    head_chars = (max_chars * 3) // 4
    return value[:head_chars].rstrip() + "\n\n" + value[-(max_chars - head_chars) :].lstrip()


def _decode_web_bytes(data: bytes) -> str:
    # HTML's in-band charset declaration is not authoritative enough to justify
    # a dependency or a second parsing pass here. UTF-8 covers the fetched
    # corpus; replacement keeps a damaged page explicit rather than crashing an
    # otherwise valid batch.
    return data.decode("utf-8", errors="replace")


def _prepare_web_page(
    source_id: str,
    value: str | bytes | Mapping[str, Any],
    *,
    max_chars: int,
) -> tuple[dict[str, Any] | None, str | None, bytes | None]:
    url: str | None = None
    content_type = "text/html"
    raw: bytes | None = None

    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, bytes):
        raw = value
    elif isinstance(value, Mapping):
        inline_html = value.get("html")
        if isinstance(inline_html, str):
            raw = inline_html.encode("utf-8")
        elif isinstance(inline_html, bytes):
            raw = inline_html
        if raw is not None:
            inline_url = value.get("url")
            url = inline_url if isinstance(inline_url, str) else None
            inline_type = value.get("content_type")
            if isinstance(inline_type, str):
                content_type = inline_type
        else:
            if value.get("ok") is not True:
                return None, "fetch result is not successful", None
            result_data = value.get("data")
            if not isinstance(result_data, Mapping):
                return None, "fetch result has no data object", None
            result_type = result_data.get("contentType")
            if isinstance(result_type, str):
                content_type = result_type
            for key in ("finalUrl", "resolvedUrl", "url"):
                candidate = result_data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    url = candidate
                    break
            if "html" not in content_type.lower():
                return None, f"fetch result is not HTML ({content_type})", None
            from nexustrade.tigris import read_fetch_result

            raw = read_fetch_result(dict(value))
            if raw is None:
                return None, "fetch result body is unavailable", None
    else:
        return None, f"unsupported page input {type(value).__name__}", None

    if "html" not in content_type.lower():
        return None, f"page is not HTML ({content_type})", None
    try:
        parser = _WebTextParser()
        parser.feed(_decode_web_bytes(raw))
        parser.close()
        title = parser.meta.get("og:title") or parser.text("title") or None
        description = parser.meta.get("og:description") or parser.meta.get(
            "description"
        )
        published_at_hint = (
            parser.meta.get("article:published_time") or parser.time_hint
        )
        scoped_text = [
            text
            for text in (parser.text("article"), parser.text("main"))
            if text
        ]
        visible_text = max(scoped_text, key=len) if scoped_text else parser.text("body")
    except Exception as exc:  # keep malformed HTML source-local
        return None, f"HTML parsing failed: {exc}", None
    prepared: dict[str, Any] = {
        "source_id": source_id,
        "url": url,
        "title": title,
        "description": description,
        "published_at_hint": published_at_hint,
        "visible_text": _bounded_web_text(visible_text, max_chars),
    }
    return prepared, None, raw


def _web_response_schema(
    document_schema: dict[str, Any], source_ids: list[str]
) -> dict[str, Any]:
    properties = document_schema.get("properties")
    if not isinstance(properties, Mapping):
        raise RowsSchemaError("web page schema has no properties")
    if "source_id" in properties:
        raise RowsSchemaError("source_id is host-owned; remove it from schema")
    item_properties: dict[str, Any] = {
        "source_id": {"type": "string", "enum": source_ids},
        **dict(properties),
    }
    return _strict_object_schema(
        {
            "documents": {
                "type": "array",
                "minItems": len(source_ids),
                "maxItems": len(source_ids),
                "items": _strict_object_schema(item_properties),
            }
        }
    )


def _web_request_key(
    source_id: str,
    raw: bytes,
    prepared: Mapping[str, Any],
    *,
    document_schema: dict[str, Any],
    instructions: str,
    model: str,
    max_chars: int,
    documents_per_request: int,
) -> str:
    descriptor = {
        "protocol": "web-page-extractions/v1",
        "source_id": source_id,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "prepared_sha256": hashlib.sha256(
            json.dumps(
                dict(prepared),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
        "schema": document_schema,
        "instructions_sha256": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        "system_sha256": hashlib.sha256(_EXTRACT_WEB_SYSTEM.encode("utf-8")).hexdigest(),
        "model": model,
        "max_chars": max_chars,
        "documents_per_request": documents_per_request,
    }
    encoded = json.dumps(
        descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _web_exact_excerpt_errors(
    value: Any, source: Mapping[str, Any], path: str = "$"
) -> list[str]:
    """Validate the evidence-excerpt convention only when a schema uses it."""
    source_texts = [
        "".join(str(source.get(field) or "").split())
        for field in ("visible_text", "title", "description")
    ]
    errors: list[str] = []

    def walk(child: Any, child_path: str) -> None:
        if isinstance(child, Mapping):
            for key, nested in child.items():
                nested_path = f"{child_path}.{key}"
                if key == "evidence_excerpt" and isinstance(nested, str):
                    normalized = "".join(nested.split())
                    if not normalized or not any(
                        normalized in source_text for source_text in source_texts
                    ):
                        errors.append(
                            f"{nested_path} is not an exact source substring"
                        )
                else:
                    walk(nested, nested_path)
        elif isinstance(child, list):
            for index, nested in enumerate(child):
                walk(nested, f"{child_path}[{index}]")

    walk(value, path)
    return errors


def _extract_web_group(
    group: list[dict[str, Any]],
    *,
    document_schema: dict[str, Any],
    instructions: str,
    model: str,
    retries: int,
) -> dict[str, dict[str, Any]]:
    from nexustrade.host import GatewayChatError, gateway_chat_json

    source_ids = [str(page["source_id"]) for page in group]
    attempts = max(1, retries + 1)
    last_error: Exception | None = None
    validation_feedback: list[str] = []
    for attempt in range(attempts):
        try:
            request_payload: dict[str, Any] = {
                "task": instructions,
                "documents": group,
            }
            if validation_feedback:
                request_payload["validation_feedback"] = validation_feedback
            response = gateway_chat_json(
                prompt=json.dumps(
                    request_payload,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                system=_EXTRACT_WEB_SYSTEM,
                model=model,
                temperature=0,
                json_schema=_web_response_schema(document_schema, source_ids),
                schema_name="extract_web_pages",
                timeout_sec=300,
            )
            documents = response.get("documents") if isinstance(response, Mapping) else None
            if not isinstance(documents, list):
                raise RuntimeError("web extraction omitted documents")
            by_id: dict[str, dict[str, Any]] = {}
            for document in documents:
                if not isinstance(document, Mapping):
                    raise RuntimeError("web extraction document is not an object")
                source_id = document.get("source_id")
                if not isinstance(source_id, str) or source_id not in source_ids:
                    raise RuntimeError(
                        f"web extraction returned unknown source_id {source_id!r}"
                    )
                if source_id in by_id:
                    raise RuntimeError(
                        f"web extraction duplicated source_id {source_id!r}"
                    )
                by_id[source_id] = {
                    "document": dict(document),
                    "error": None,
                }
            missing = [source_id for source_id in source_ids if source_id not in by_id]
            if missing:
                raise RuntimeError(
                    "web extraction omitted source_id(s): " + ", ".join(missing)
                )
            source_by_id = {str(page["source_id"]): page for page in group}
            validation_feedback = [
                f"{source_id}: {message}"
                for source_id, result in by_id.items()
                for message in _web_exact_excerpt_errors(
                    result["document"], source_by_id[source_id]
                )
            ]
            if validation_feedback:
                raise RuntimeError("; ".join(validation_feedback[:8]))
            return by_id
        except GatewayChatError:
            raise
        except Exception as exc:  # schema/content repair only
            last_error = exc
            if attempt + 1 == attempts:
                break
    raise _RowsStructuringError(
        f"web extraction failed after {attempts} attempt(s): {last_error}"
    ) from last_error


def extract_web_pages(
    pages: Mapping[str, str | bytes | Mapping[str, Any]],
    *,
    instructions: str,
    schema: Mapping[str, Any],
    model: str | None = None,
    documents_per_request: int = 1,
    max_chars_per_document: int = 80_000,
    max_chars_per_request: int = 200_000,
    max_workers: int = 2,
    retries: int = 1,
) -> dict[str, dict[str, Any]]:
    """Turn fetched HTML pages into one strict typed object per source.

    ``pages`` may contain HTML strings/bytes, ``{"html": ..., "url": ...}``
    objects, or complete result rows returned by ``host.read_results()`` after
    ``host.fetch``. Fetch bodies remain in Tigris and enter this bounded helper,
    not the OpenCode transcript. ``schema`` describes ONE per-page object;
    ``source_id`` is added and checked by the host.

    Each page gets an isolated GPT-5.6 Luna request by default so one dense page
    cannot consume another page's output attention. Increase
    ``documents_per_request`` when measured accuracy permits batching.
    Successful byte-identical results are durably replayed, and every input key
    always has either ``{"document": ..., "error": None}`` or an explicit error.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not isinstance(pages, Mapping):
        raise TypeError("pages must be a mapping from source_id to page input")
    if not instructions.strip():
        raise ValueError("instructions must be non-empty")
    if documents_per_request < 1 or documents_per_request > 20:
        raise ValueError("documents_per_request must be between 1 and 20")
    if max_chars_per_document < 1 or max_chars_per_request < 1:
        raise ValueError("character limits must be positive")
    if max_chars_per_document > max_chars_per_request:
        raise ValueError("max_chars_per_document cannot exceed max_chars_per_request")
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if retries < 0:
        raise ValueError("retries must be non-negative")

    normalized_schema = normalize_document_schema(
        schema, preserve_declared_required=True
    )
    properties = normalized_schema.get("properties")
    if isinstance(properties, Mapping) and "source_id" in properties:
        raise RowsSchemaError("source_id is host-owned; remove it from schema")
    configured = os.environ.get("EXTRACT_ROWS_MODEL", "").strip()
    structure_model = model or configured or DEFAULT_EXTRACT_ROWS_MODEL

    ordered_ids = [str(source_id) for source_id in pages]
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError("page source ids must remain unique after string conversion")
    results: dict[str, dict[str, Any]] = {}
    prepared_by_id: dict[str, dict[str, Any]] = {}
    request_keys: dict[str, str] = {}
    for raw_source_id, value in pages.items():
        source_id = str(raw_source_id)
        prepared, error, raw = _prepare_web_page(
            source_id, value, max_chars=max_chars_per_document
        )
        if error or prepared is None or raw is None:
            results[source_id] = {"document": {}, "error": error or "unreadable page"}
            continue
        prepared_by_id[source_id] = prepared
        request_keys[source_id] = _web_request_key(
            source_id,
            raw,
            prepared,
            document_schema=normalized_schema,
            instructions=instructions,
            model=structure_model,
            max_chars=max_chars_per_document,
            documents_per_request=documents_per_request,
        )

    batch_key = _document_batch_key(request_keys)
    remaining: list[dict[str, Any]] = []
    cache_hits = 0
    for source_id in ordered_ids:
        if source_id not in prepared_by_id:
            continue
        replay = _document_result_lookup(request_keys[source_id])
        if replay is None:
            remaining.append(prepared_by_id[source_id])
        else:
            results[source_id] = replay
            cache_hits += 1

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for page in remaining:
        visible = page.get("visible_text")
        page_chars = len(visible) if isinstance(visible, str) else 0
        if current and (
            len(current) >= documents_per_request
            or current_chars + page_chars > max_chars_per_request
        ):
            groups.append(current)
            current = []
            current_chars = 0
        current.append(page)
        current_chars += page_chars
    if current:
        groups.append(current)

    completed = cache_hits
    failed = len(results) - cache_hits

    def run_group(group: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        extracted = _extract_web_group(
            group,
            document_schema=normalized_schema,
            instructions=instructions,
            model=structure_model,
            retries=retries,
        )
        for source_id, payload in extracted.items():
            _document_result_record(
                batch_key=batch_key,
                request_key=request_keys[source_id],
                document_id=source_id,
                payload=payload,
                total=len(pages),
                track_progress=False,
            )
        return extracted

    with ThreadPoolExecutor(max_workers=min(max_workers, len(groups) or 1)) as pool:
        futures = {pool.submit(run_group, group): group for group in groups}
        for future in as_completed(futures):
            group = futures[future]
            try:
                extracted = future.result()
                results.update(extracted)
                completed += len(group)
            except Exception as exc:  # one failed request stays explicit per source
                for page in group:
                    source_id = str(page["source_id"])
                    results[source_id] = {"document": {}, "error": str(exc)}
                    failed += 1
            _document_batch_progress(
                batch_key=batch_key,
                total=len(pages),
                completed=completed,
                failed=failed,
                cache_hits=cache_hits,
                done=False,
            )
    _document_batch_progress(
        batch_key=batch_key,
        total=len(pages),
        completed=completed,
        failed=failed,
        cache_hits=cache_hits,
        done=True,
    )
    return {source_id: results[source_id] for source_id in ordered_ids}
