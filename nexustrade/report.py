"""Write investor-grade report artifacts for sandbox_finish(kind=\"report\").

Deep Research pattern:
1. Compute stats + save plots in the sandbox
2. Call `report.write_inputs({...})` with structured JSON (authoritative numbers)
3. Optionally also `report.write(...)` as a fallback markdown stub
4. `sandbox_finish(kind=\"report\")` — host calls NexusGenAI **Sandbox Report Generator**
   on report_inputs.json, embeds CDN images, appends code appendix, uploads PDF
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, Union

DEFAULT_MARKDOWN_PATH = "/work/output.md"
# Deliverables live under /work/out: only that directory becomes bundle members
# (§2b), and the host inventories it to build declared_members. Datasets were
# moved to /work/out/rows.jsonl already; reports are the other half of that
# migration. Writing outside it produced report bundles that declared nothing.
DEFAULT_IMAGES_DIR = "/work/out/images"
DEFAULT_INPUTS_PATH = "/work/out/report_inputs.json"
# Read-side fallbacks for a workspace written by an older sandbox image.
LEGACY_IMAGES_DIR = "/work/output/images"
LEGACY_INPUTS_PATH = "/work/report_inputs.json"
# Code is evidence, not a deliverable, so it stays outside the bundle.
DEFAULT_CODE_DIR = "/work/output/code"

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


def write_inputs(
    payload: Mapping[str, Any],
    *,
    path: str = DEFAULT_INPUTS_PATH,
) -> str:
    """
    Write structured report inputs for the host-side Sandbox Report Generator prompt.

    Required-ish keys (all optional but recommended):
      title, request, sources, methodology, statistics, images, findings, caveats

    Optional insight slots (relationship reports):
      regimes — [{label, start, end, r?, p?, n?, ...}] peri-break / regime stats
      eventContext — [{date, event, url}] verified news/context around break dates
      interpretation — [str, ...] notes grounded only in statistics / eventContext
      statistics.break — {detected, dates, method}
      statistics.power_note / stationarity / effective_n_note — hygiene fields

    `images` should be [{fileName, caption}, ...] matching files under DEFAULT_IMAGES_DIR.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(dict(payload), indent=2, default=str) + "\n", encoding="utf-8")
    return path


def write(
    markdown: str = "",
    images: Sequence[ImageSpec] | None = None,
    code_paths: Iterable[str] | None = None,
    *,
    title: str | None = None,
    inputs: Mapping[str, Any] | None = None,
    markdown_path: str = DEFAULT_MARKDOWN_PATH,
    images_dir: str = DEFAULT_IMAGES_DIR,
    code_dir: str = DEFAULT_CODE_DIR,
    inputs_path: str = DEFAULT_INPUTS_PATH,
) -> str:
    """
    Materialize report artifacts for sandbox_finish(kind=\"report\").

    Prefer passing `inputs=` (structured stats) so the host authors markdown via
    **Sandbox Report Generator**. `markdown` is a fallback stub if the prompt fails.
    """
    _ensure_dirs(images_dir, code_dir)

    image_meta: list[dict[str, str]] = []
    for index, spec in enumerate(images or [], start=1):
        file_name, caption = _save_image(spec, images_dir, index)
        image_meta.append({"fileName": file_name, "caption": caption})

    if inputs is not None:
        payload = dict(inputs)
        if title and "title" not in payload:
            payload["title"] = title
        if image_meta and "images" not in payload:
            payload["images"] = image_meta
        write_inputs(payload, path=inputs_path)

    body = (markdown or "").strip()
    markdown_file = Path(markdown_path)
    if not body and markdown_file.is_file():
        # Structured report refreshes commonly update inputs/images after an
        # operator has already authored a fallback. Omitting `markdown=` means
        # "leave that fallback alone", never "erase it".
        body = markdown_file.read_text(encoding="utf-8").strip()
    if not body and title:
        body = f"# {title.strip()}\n\n_Report will be authored by Sandbox Report Generator._\n"
    elif title and body and not body.lstrip().startswith("#"):
        body = f"# {title.strip()}\n\n{body}"

    image_blocks = [f"![{row['caption']}](images/{row['fileName']})" for row in image_meta]
    if body and image_blocks and "](images/" not in body:
        body = f"{body}\n\n" + "\n\n".join(image_blocks)
    if not body and image_blocks:
        body = "\n\n".join(image_blocks)

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
    job = Path("/work/job.py")
    return [str(job)] if job.is_file() else []
