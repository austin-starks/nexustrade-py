"""Sampled vision inspection for PDFs and images.

The same public module runs locally and inside the compute sandbox. Gateway
credentials and PDF dependencies are supplied by the caller's environment.

Use on 2–3 representative documents before batch ``extract_pdfs`` to ground a
``rows_schema``, or on specific filings flagged by the grader. Reports observed
layout and evidence only — it does not choose an extraction API or parser.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from nexustrade.scanned_table import probe_pdf, render_pdf_page_png

DEFAULT_INSPECT_MODEL = "openai/gpt-5.6-luna"

_INSPECT_SYSTEM = (
    "You inspect financial disclosure documents (filings, scanned forms, tables). "
    "Report observed layout, visible fields/columns, instrument suffix codes, "
    "page continuations, ambiguities, and where evidence lives. "
    "Do NOT recommend an extraction API, parser, or mechanism "
    "(no extract_pdf_markdown, liteparse, raw text layer, or similar). "
    "The operator declares rows_schema and runs scanned_table.extract_rows or "
    "extract_pdfs; your output grounds schema design only."
)

_INSPECT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "layout": {
            "type": "string",
            "description": "Short label, e.g. tabular_disclosure, prose_letter, mixed.",
        },
        "has_tables": {"type": "boolean"},
        "has_page_continuations": {"type": "boolean"},
        "observed_fields": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Semantic fields or column headers visible in the sample.",
        },
        "instrument_suffix_codes_seen": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Observed ticker suffix codes, if any (evidence only).",
        },
        "ambiguities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Schema decisions the operator must resolve from evidence.",
        },
        "evidence_locations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Where key facts live (page, table region, continuation).",
        },
        "notes": {
            "type": "string",
            "description": "Additional evidence-only observations for schema design.",
        },
    },
    "required": [
        "layout",
        "has_tables",
        "has_page_continuations",
        "observed_fields",
        "instrument_suffix_codes_seen",
        "ambiguities",
        "evidence_locations",
        "notes",
    ],
}

_IMAGE_MIME_BY_PREFIX: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
)


def _detect_document_kind(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    for prefix, _mime in _IMAGE_MIME_BY_PREFIX:
        if data.startswith(prefix):
            return "image"
    raise ValueError(
        "inspect_document expects PDF bytes or a PNG/JPEG/WEBP image; "
        f"unrecognized header {data[:8]!r}"
    )


def _image_mime(data: bytes) -> str:
    for prefix, mime in _IMAGE_MIME_BY_PREFIX:
        if data.startswith(prefix):
            if mime == "image/webp" and b"WEBP" not in data[:16]:
                continue
            return mime
    return "image/png"


def _pdf_page_count(data: bytes) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    try:
        page_count = len(doc)
    finally:
        doc.close()
    return page_count


def _normalize_pdf_pages(
    page_count: int,
    pages: Sequence[int] | None,
    max_pages: int,
) -> list[int]:
    if page_count <= 0:
        raise ValueError("PDF has no pages")
    cap = max(1, max_pages)
    if pages is None:
        return list(range(1, min(page_count, cap) + 1))
    normalized: list[int] = []
    for page in pages:
        if page < 1 or page > page_count:
            raise IndexError(
                f"page {page} out of range for PDF with {page_count} page(s) "
                "(pages are 1-based)"
            )
        if page not in normalized:
            normalized.append(page)
        if len(normalized) >= cap:
            break
    if not normalized:
        raise ValueError("pages must include at least one 1-based page index")
    return normalized


def _build_user_prompt(
    *,
    kind: str,
    question: str | None,
    pages_inspected: list[int],
    probe: dict[str, Any] | None,
    attachment_kind: str,
) -> str:
    lines = [
        "Inspect the attached document and return structured evidence for rows_schema design.",
        "Report layout, fields, ambiguities, and evidence locations only.",
        "Do not recommend an extraction function or parser.",
        f"Document kind: {kind}.",
        f"Attachment transport: {attachment_kind}.",
    ]
    if pages_inspected:
        lines.append(
            "Focus on these 1-based page numbers: "
            + ", ".join(str(page) for page in pages_inspected)
            + "."
        )
        if kind == "pdf":
            lines.append(
                "The attached rendered page images correspond exactly to those page "
                "numbers in the same order; no unrequested PDF page is attached."
            )
    if probe is not None:
        lines.append(f"probe_pdf summary: {probe}")
    if question and question.strip():
        lines.append(f"Operator question: {question.strip()}")
    return "\n".join(lines)


def _gateway_attachments(
    data: bytes,
    kind: str,
    *,
    pages_inspected: Sequence[int],
    filename: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    from nexustrade.host import gateway_image_url_part

    if kind == "pdf":
        attachments = [
            gateway_image_url_part(
                render_pdf_page_png(data, page_number - 1),
                mime_type="image/png",
            )
            for page_number in pages_inspected
        ]
        return (
            attachments,
            "rendered_pdf_pages(" + ",".join(str(page) for page in pages_inspected) + ")",
        )
    mime = _image_mime(data)
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
        mime, "img"
    )
    name = filename or f"document.{ext}"
    return (
        [gateway_image_url_part(data, mime_type=mime)],
        f"image ({name})",
    )


def inspect_document(
    data: bytes,
    *,
    pages: Sequence[int] | None = None,
    question: str | None = None,
    model: str | None = None,
    max_pages: int = 3,
    filename: str | None = None,
) -> dict[str, Any]:
    """Sample a PDF or image and report layout evidence for schema design.

    Requested PDF pages render to page-scoped PNG attachments so vision cannot
    confuse evidence from an unrequested page. Images attach via ``image_url``.
    Always runs ``probe_pdf`` first for PDFs.
    """
    if not data:
        raise ValueError("inspect_document requires non-empty bytes")

    from nexustrade.host import gateway_chat_json, gateway_multimodal_messages

    kind = _detect_document_kind(data)
    probe: dict[str, Any] | None = None
    pages_inspected: list[int] = []

    if kind == "pdf":
        page_count = _pdf_page_count(data)
        probe = probe_pdf(data)
        pages_inspected = _normalize_pdf_pages(page_count, pages, max_pages)
    else:
        pages_inspected = [1]

    attachments, attachment_kind = _gateway_attachments(
        data,
        kind,
        pages_inspected=pages_inspected,
        filename=filename,
    )
    configured = os.environ.get("INSPECT_DOCUMENT_MODEL", "").strip()
    inspect_model = model or configured or DEFAULT_INSPECT_MODEL
    prompt = _build_user_prompt(
        kind=kind,
        question=question,
        pages_inspected=pages_inspected,
        probe=probe,
        attachment_kind=attachment_kind,
    )
    analysis = gateway_chat_json(
        messages=gateway_multimodal_messages(prompt, attachments),
        system=_INSPECT_SYSTEM,
        model=inspect_model,
        json_schema=_INSPECT_JSON_SCHEMA,
        schema_name="document_inspection",
        temperature=0,
    )
    if not isinstance(analysis, dict):
        raise RuntimeError("inspect_document expected a JSON object from the gateway")

    result = {
        "kind": kind,
        "probe": probe,
        "pages_inspected": pages_inspected,
        "page_coordinates": {
            "pages_inspected_one_based": pages_inspected,
            "pages_inspected_zero_based": [page - 1 for page in pages_inspected],
            "inspection_page_base": 1,
            "page_audit_index_base": 0,
        },
        "attachment_kind": attachment_kind,
        "analysis": analysis,
        "model": inspect_model,
    }
    from nexustrade.document_inspect_receipt import persist_inspect_receipt

    persist_inspect_receipt(result)
    return result
