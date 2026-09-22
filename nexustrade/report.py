"""Write investor-grade report artifacts for sandbox_finish(kind=\"report\").

Deep Research pattern:
1. Compute stats + save plots in the sandbox
2. Call `report.write(inputs={...})` with structured research and calculation outputs
3. report_inputs.json carries the evidence; optional local Markdown is not an authoring input
4. `sandbox_finish(kind=\"report\")` — the host compiles a claim-bound report
   template from report_inputs.json, embeds CDN images, appends code appendix,
   and uploads the PDF
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, Union

WORK_DIR = os.environ.get("NEXUSTRADE_WORK_DIR", "/work")
DEFAULT_MARKDOWN_PATH = os.path.join(WORK_DIR, "output.md")
# Deliverables live under /work/out: only that directory becomes bundle members
# (§2b), and the host inventories it to build declared_members. Datasets were
# moved to /work/out/rows.jsonl already; reports are the other half of that
# migration. Writing outside it produced report bundles that declared nothing.
DEFAULT_IMAGES_DIR = os.path.join(WORK_DIR, "out", "images")
DEFAULT_INPUTS_PATH = os.path.join(WORK_DIR, "out", "report_inputs.json")
# Read-side fallbacks for a workspace written by an older sandbox image.
LEGACY_IMAGES_DIR = os.path.join(WORK_DIR, "output", "images")
LEGACY_INPUTS_PATH = os.path.join(WORK_DIR, "report_inputs.json")
# Code is evidence, not a deliverable, so it stays outside the bundle.
DEFAULT_CODE_DIR = os.path.join(WORK_DIR, "output", "code")

ImageSpec = Union[
    tuple[str, str],  # (path, caption)
    tuple[Any, str],  # (matplotlib Figure, caption)
    str,  # path only
]


def _ensure_dirs(images_dir: str, code_dir: str) -> None:
    Path(images_dir).mkdir(parents=True, exist_ok=True)
    Path(code_dir).mkdir(parents=True, exist_ok=True)


def _slug(caption: str, index: int) -> str:
    base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in caption.strip())
    base = base.strip("_") or f"figure_{index}"
    return base[:80]


def _logical_work_path(path: str) -> str:
    """Map a local-backend host path back to the sandbox's /work namespace."""
    candidate = Path(path)
    work = Path(WORK_DIR)
    try:
        relative = candidate.resolve().relative_to(work.resolve())
    except ValueError:
        return path
    return str(Path("/work") / relative)


def _save_image(
    spec: ImageSpec,
    images_dir: str,
    index: int,
) -> tuple[str, str]:
    """Return (file_name, caption) after writing under images_dir."""
    if isinstance(spec, str):
        src = Path(spec)
        caption = src.stem
        dest_name = src.name if src.suffix else f"{src.name}.png"
        dest = Path(images_dir) / dest_name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        return dest_name, caption

    if not isinstance(spec, tuple) or len(spec) != 2:
        raise TypeError(
            "Each image must be a path str, (path, caption), or (matplotlib Figure, caption)"
        )

    source, caption = spec
    caption = str(caption) if caption else f"figure_{index}"
    if isinstance(source, (str, Path)):
        src = Path(source)
        dest_name = src.name if src.suffix else f"{_slug(caption, index)}.png"
        dest = Path(images_dir) / dest_name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        return dest_name, caption

    dest_name = f"{_slug(caption, index)}.png"
    dest = Path(images_dir) / dest_name
    savefig = getattr(source, "savefig", None)
    if not callable(savefig):
        raise TypeError(
            "Image source must be a file path or an object with savefig() (e.g. matplotlib Figure)"
        )
    savefig(str(dest), dpi=150, bbox_inches="tight")
    return dest_name, caption


@dataclass(frozen=True)
class _ModelReference:
    path: tuple[str | int, ...]
    provenance_path: tuple[str | int, ...] | None = None


def source_excerpts(source_id: str, visible_text: str, *, passages: Sequence[str]) -> list[dict[str, str]]:
    """Select explicit source passages without a count or character allowance.

    Supply literal supporting sentences/table spans from prepare_web_pages
    visible_text, including relevant headers and units. Separate distant passages
    even when they support claims from the same source. This helper checks exact,
    unambiguous text occurrence, not whether a quote proves an analytical claim.
    Host receipt and body verification still establish source authenticity.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id must be the durable host fetch ID")
    if not isinstance(visible_text, str):
        raise TypeError("visible_text must be source text")
    if isinstance(passages, (str, bytes)):
        raise TypeError("passages must be a sequence of separate source passages")
    text = " ".join(visible_text.split())
    excerpts = []
    for index, passage in enumerate(passages):
        if not isinstance(passage, str) or not passage.strip():
            raise ValueError(f"passage {index} must contain source text")
        quote = " ".join(passage.split())
        start = text.find(quote)
        if start < 0:
            raise ValueError(f"passage {index} does not occur in source {source_id}")
        if text.find(quote, start + 1) >= 0:
            raise ValueError(f"passage {index} is ambiguous in source {source_id}; include adjacent text")
        excerpts.append({"sourceId": source_id.strip(), "quote": quote})
    return excerpts


def ref(*path: str | int, provenance_path: Sequence[str | int] | None = None) -> _ModelReference:
    """Reference a model field in structured findings, tables, or source records.

    Example: ref('scenarios', 'base', 'per_share'). Pass the current model to
    write(inputs=..., model=...). References resolve at write time; the host gets
    ordinary JSON, never templates or OpenCode-authored report prose. Optional
    provenance_path points to current model metadata (source IDs, status, dates,
    definition). With preserve_references=True it survives in modelReferences.
    For report authorship, place each displayable number or digit-bearing string
    (including dates and filing forms) in an exact scalar ref under a
    semantically named input field. A broad object/array ref remains grader
    evidence, but its nested values are not prose-ready claims because the host
    will not infer meaning from array position.
    """
    if not path or any(isinstance(p, bool) or not isinstance(p, (str, int)) for p in path):
        raise ValueError("model reference requires string keys or nonnegative list indices")
    if any(isinstance(p, int) and p < 0 for p in path):
        raise ValueError("model reference indices must be nonnegative")
    metadata_path = None
    if provenance_path is not None:
        if isinstance(provenance_path, (str, bytes)):
            raise ValueError("provenance_path must be a sequence of model keys")
        metadata_path = ref(*provenance_path).path
    return _ModelReference(path, metadata_path)


def _resolve(value: Any, model: Mapping[str, Any] | None, *,
             references: list[dict[str, Any]] | None = None,
             input_path: tuple[str | int, ...] = (), model_source: str | None = None) -> Any:
    if isinstance(value, _ModelReference):
        if model is None:
            raise ValueError("model references require a plain model object")
        target: Any = model
        for part in value.path:
            if isinstance(target, Mapping) and isinstance(part, str) and part in target:
                target = target[part]
            elif isinstance(target, (list, tuple)) and isinstance(part, int) and 0 <= part < len(target):
                target = target[part]
            else:
                raise ValueError(f"unresolved model reference: {value.path!r}")
        # The model contains data, not another template/reference graph.
        resolved = _resolve(target, None)
        if references is not None:
            record: dict[str, Any] = {"inputPath": list(input_path), "modelPath": list(value.path)}
            if model_source is not None:
                record["modelSource"] = model_source
            if value.provenance_path is not None:
                provenance = _resolve(_ModelReference(value.provenance_path), model)
                if not isinstance(provenance, Mapping):
                    raise ValueError("provenance_path must resolve to a metadata object")
                record.update(provenancePath=list(value.provenance_path), provenance=provenance)
            references.append(record)
        return resolved
    if isinstance(value, Mapping):
        return {key: _resolve(item, model, references=references,
                             input_path=(*input_path, key), model_source=model_source)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolve(item, model, references=references,
                         input_path=(*input_path, index), model_source=model_source)
                for index, item in enumerate(value)]
    return value


def validate_source_references(payload: Mapping[str, Any], *, aliases: Mapping[str, str]) -> None:
    """Check explicit source registries; durable body verification stays host-owned.

    ``aliases`` maps durable fetch IDs to bibliography IDs; pass {} when the
    namespaces deliberately coincide. This validates linkage, not source truth,
    and never rewrites sourceExcerpts IDs needed by the host receipt verifier.
    """
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("source registry must be a list")
    ids = [s["id"] for s in sources if isinstance(s, Mapping) and "id" in s]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("source registry IDs must be unique nonempty strings")
    known = set(ids)
    if any(not isinstance(k, str) or not k or not isinstance(v, str) or v not in known for k, v in aliases.items()):
        raise ValueError("source aliases must map fetch IDs to registered bibliography IDs")
    def resolves(source_id: Any) -> bool:
        return isinstance(source_id, str) and aliases.get(source_id, source_id) in known
    for excerpt in payload.get("sourceExcerpts", []):
        if not isinstance(excerpt, Mapping) or not resolves(excerpt.get("sourceId")):
            raise ValueError("source excerpt does not resolve to the source registry")
    for item in payload.get("fetch_reconciliation", []):
        if isinstance(item, Mapping) and item.get("used") is True and not resolves(item.get("id")):
            raise ValueError("used fetch record does not resolve to the source registry")


def write_inputs(
    payload: Mapping[str, Any],
    *,
    path: str = DEFAULT_INPUTS_PATH,
    model: Mapping[str, Any] | None = None,
    source_aliases: Mapping[str, str] | None = None,
    preserve_references: bool = False,
    model_source: str | None = None,
) -> str:
    """
    Write structured report inputs for the host-side Sandbox Report Generator prompt.

    Common keys:
      title, request, sources, methodology, statistics, images, findings, caveats

    For a staged method, use the structural delivery contract:
      reportEvidence — [{id, paragraphs=[[text, ref(...), ...]], referenceIds?}]
      reportViews — [{id, type='table'|'metricGrid', ...typed text segments...}]
      requirements — [{requirement=<staged id>, evidenceIds=[...], viewIds=[...]}]
      validationChecks — [{id, kind='independent'|'reconciliation', left, right,
                           evidenceId}]

    Every numeric or digit-bearing segment in reportEvidence/reportViews must be
    an exact scalar ref (or a labeled scalar assumption). The host copies these
    blocks exactly and refuses a generated report that omits a mapped id. This is
    a delivery contract, not a semantic claim that the evidence is sufficient.
    Each validation side names an exact scalar inputPath. The host derives that
    scalar's sourceIds from its bound model provenance and verifies fetch/lake
    lineage. Independent sides must be disjoint; same-lineage comparisons are
    reconciliation checks. Never place free values or sourceIds on the check.

    Supply structured research and calculation outputs. The host authors the report.
    Pass model= to resolve report.ref fields at write time and export the complete
    current calculation object as calculationModel. This argument replaces any
    older calculationModel in payload. Exact scalar references authorize numeric
    report claims; broad references preserve model evidence for the grader but do
    not authorize nested numbers for prose or tables. Keep model focused on
    calculation data, assumptions, and provenance rather than raw files.
    Optional source_aliases
    maps durable fetch IDs to bibliography IDs and validates explicit linkage;
    {} checks an intentionally shared namespace. Legacy calls leave receipt
    verification to the host. This does not verify the content of source claims.
    preserve_references adds modelReferences linking output paths to current model
    paths and optional provenance objects. Supply model_source with the actual
    model artifact path when file-backed; without it paths refer only to this
    in-memory model argument. The map is executor-declared lineage, never proof
    of source authority. A supplied modelReferences map cannot replace fresh
    references in this mode.

    Optional insight slots (relationship reports):
      regimes — [{label, start, end, r?, p?, n?, ...}] peri-break / regime stats
      eventContext — [{date, event, url}] verified news/context around break dates
      interpretation — [str, ...] notes grounded only in statistics / eventContext
      statistics.break — {detected, dates, method}
      statistics.power_note / stationarity / effective_n_note — hygiene fields

    `images` should be [{fileName, caption}, ...] matching files under DEFAULT_IMAGES_DIR.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if model_source is not None:
        if not isinstance(model_source, str) or not model_source.strip():
            raise ValueError("model_source must be a nonempty artifact path")
        model_source = _logical_work_path(model_source.strip())
    if preserve_references and "modelReferences" in payload:
        raise ValueError("modelReferences is generated from current references; remove the supplied map")
    structured = dict(payload)
    if "method_requirements" in structured:
        if (
            "requirements" in structured
            and structured["requirements"] != structured["method_requirements"]
        ):
            raise ValueError("requirements and method_requirements contain different values")
        structured["requirements"] = structured.pop("method_requirements")
    if model is not None:
        structured.pop("calculationModel", None)
    references: list[dict[str, Any]] | None = [] if preserve_references else None
    inputs = _resolve(structured, model, references=references, model_source=model_source)
    if model is not None:
        inputs["calculationModel"] = _resolve(model, None)
    if references is not None:
        inputs["modelReferences"] = references
    if source_aliases is not None:
        validate_source_references(inputs, aliases=source_aliases)
    Path(path).write_text(json.dumps(inputs, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def write(
    markdown: str = "",
    images: Sequence[ImageSpec] | None = None,
    code_paths: Iterable[str] | None = None,
    *,
    title: str | None = None,
    inputs: Mapping[str, Any] | None = None,
    model: Mapping[str, Any] | None = None,
    source_aliases: Mapping[str, str] | None = None,
    preserve_references: bool = False,
    model_source: str | None = None,
    markdown_path: str = DEFAULT_MARKDOWN_PATH,
    images_dir: str = DEFAULT_IMAGES_DIR,
    code_dir: str = DEFAULT_CODE_DIR,
    inputs_path: str = DEFAULT_INPUTS_PATH,
    path: str | None = None,
) -> str:
    """
    Materialize report artifacts for sandbox_finish(kind=\"report\").

    Pass `inputs=` for structured evidence, calculations, and research findings.
    `path=` is a compatibility alias for `inputs_path=`. The legacy
    `method_requirements` input key is normalized to the host's `requirements` key.
    The host Report Generator authors the document that is graded and delivered.
    Optional `markdown` is a local compatibility export only; it never enters
    report_inputs.json and is never recovered from an earlier output.md.
    """
    if path is not None:
        if inputs_path != DEFAULT_INPUTS_PATH and inputs_path != path:
            raise ValueError("path and inputs_path identify different report input files")
        inputs_path = path

    _ensure_dirs(images_dir, code_dir)

    image_meta: list[dict[str, str]] = []
    for index, spec in enumerate(images or [], start=1):
        file_name, caption = _save_image(spec, images_dir, index)
        image_meta.append({"fileName": file_name, "caption": caption})

    body = (markdown or "").strip()
    markdown_file = Path(markdown_path)
    if not body and title:
        body = f"# {title.strip()}\n\n_Report will be authored by Sandbox Report Generator._\n"
    elif title and body and not body.lstrip().startswith("#"):
        body = f"# {title.strip()}\n\n{body}"

    image_blocks = [f"![{row['caption']}](images/{row['fileName']})" for row in image_meta]
    if body and image_blocks and "](images/" not in body:
        body = f"{body}\n\n" + "\n\n".join(image_blocks)
    if not body and image_blocks:
        body = "\n\n".join(image_blocks)

    if inputs is not None or model is not None:
        payload = dict(inputs or {})
        if title and "title" not in payload:
            payload["title"] = title
        if image_meta and "images" not in payload:
            payload["images"] = image_meta
        write_inputs(payload, path=inputs_path, model=model, source_aliases=source_aliases,
                     preserve_references=preserve_references, model_source=model_source)

    markdown_file.parent.mkdir(parents=True, exist_ok=True)
    markdown_file.write_text((body.rstrip() + "\n") if body else "", encoding="utf-8")

    # When code_paths is omitted, copy the executed job so the host appendix is
    # real source — not a comment stub the LLM invented under /work/output/code/.
    for code_path in _default_code_paths(code_paths):
        src = Path(code_path)
        if not src.is_file():
            raise FileNotFoundError(f"code_paths entry not found: {code_path}")
        dest = Path(code_dir) / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)

    return markdown_path


def _default_code_paths(explicit: Iterable[str] | None) -> list[str]:
    """Prefer explicit paths; otherwise copy the executed job for the audit appendix."""
    if explicit is not None:
        return list(explicit)
    job = Path(WORK_DIR) / "job.py"
    return [str(job)] if job.is_file() else []
