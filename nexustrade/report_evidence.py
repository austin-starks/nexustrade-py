"""Read retained child research for readiness without changing the analysis.

Transport states describe staged bytes, never whether an issuer disclosed a fact.
Selected contents and source claims remain untrusted until independently checked.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

EVIDENCE_FIELD = "_report_readiness_evidence"
MAX_CONTEXT_BYTES = 2 * 1024 * 1024
MAX_INLINE_FILE_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_HANDOFFS = 256  # Multiple native parents can be retained by a continuation.


def _encoded_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _regular_file(work: Path, path: Path) -> os.stat_result:
    if not path.is_relative_to(work) or path.resolve() != path:
        raise ValueError("Retained research must be inside the workspace without symlinks")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Retained research must be a regular file")
    return info


def _read_exact(work: Path, path: Path, limit: int) -> bytes:
    before = _regular_file(work, path)
    if before.st_size > limit:
        raise ValueError("Retained research exceeds its transport bound; no content was truncated")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Retained research changed during capture")
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    current = _regular_file(work, path)
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if len(content) != before.st_size or identity(before) != identity(after) or identity(after) != identity(current):
        raise ValueError("Retained research changed during capture")
    return content


def _digest_name(name: str) -> bool:
    return len(name) == 64 and all(char in "0123456789abcdef" for char in name)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Retained research JSON contains duplicate keys")
        result[key] = value
    return result


def staged_research_context(work: Path, *, max_bytes: int = MAX_CONTEXT_BYTES) -> dict[str, Any]:
    """Include whole small selections plus explicit inventories for other files.

    No source fetching, inference, canonical writes or financial decisions occur.
    Size bounds apply to transport, not semantic relevance or financial values.
    """
    work = work.resolve()
    root = work / ".nexustrade" / "research-evidence"
    if root.is_symlink() or (root.exists() and root.resolve() != root):
        raise ValueError("Retained research must not contain symlinked directories")
    context: dict[str, Any] = {
        "version": 1,
        "inventoryState": "no_staged_handoffs",
        "sourceVerification": "child-declared source claims, not authenticated facts or completed calculations",
        "interpretation": "No staged handoff, empty file, query omission or non-inlined content is proof of original-source absence. Read each question, selection and limitation; distinguish unresolved accounting or analyst choices from missing disclosures.",
        "limits": {"contextBytes": max_bytes, "inlineFileBytes": MAX_INLINE_FILE_BYTES},
        "handoffs": [],
    }
    # Native staging publishes only digest-named directories by atomic rename.
    # In-flight .staging-* folders are not handoffs. A completed directory with
    # a missing manifest is corrupt and must fail capture, not disappear.
    paths = sorted(folder / "manifest.json" for folder in root.glob("*/*")
                   if _digest_name(folder.parent.name) and _digest_name(folder.name)) if root.exists() else []
    if len(paths) > MAX_HANDOFFS:
        raise ValueError("Retained research inventory exceeds the handoff bound; nothing was truncated")
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        raw = _read_exact(work, path, MAX_MANIFEST_BYTES)
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(manifest, dict):
            raise ValueError("Retained research manifest must be an object")
        parent_id = manifest.get("parentSessionId")
        if (manifest.get("version") != 1 or not isinstance(parent_id, str) or
                path.parent.parent.name != hashlib.sha256(parent_id.encode()).hexdigest() or
                path.parent.name != hashlib.sha256(raw).hexdigest()):
            raise ValueError("Retained research manifest identity does not match its staged location")
        for key in ("question", "summary", "sourceVerification", "sessionId", "role"):
            if not isinstance(manifest.get(key), str):
                raise ValueError(f"Retained research manifest omitted {key}")
        sources, limitations = manifest.get("sources"), manifest.get("limitations")
        if (not isinstance(sources, list) or not 1 <= len(sources) <= 32 or
                not all(isinstance(source, dict) and all(isinstance(source.get(key), str) and
                    0 < len(source[key]) <= 2000 for key in ("sourceId", "locator")) for source in sources) or
                not isinstance(limitations, list) or len(limitations) > 32 or
                not all(isinstance(limit, str) and 0 < len(limit) <= 2000 for limit in limitations)):
            raise ValueError("Retained research manifest omitted sources or limitations")
        handoff = {key: manifest[key] for key in ("question", "summary", "sources", "limitations", "sourceVerification", "sessionId", "parentSessionId", "role")}
        handoff.update({"manifestPath": "/work/" + str(path.relative_to(work)), "files": []})
        files = manifest.get("files")
        if not isinstance(files, list) or not files or len(files) > 8:
            raise ValueError("Retained research requires its complete selected file inventory")
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Retained research file inventory is invalid")
            name = item.get("name")
            size = item.get("bytes")
            checksum = item.get("sha256")
            if (not isinstance(name, str) or Path(name).name != name or name in {".", ".."} or name in seen or
                    not isinstance(size, int) or isinstance(size, bool) or size < 0 or
                    not isinstance(checksum, str) or not _digest_name(checksum) or
                    not isinstance(item.get("description"), str)):
                raise ValueError("Retained research file inventory is invalid")
            original_name = name.split("-", 1)[-1]
            if original_name.startswith(".env") or original_name in {"auth.json", ".jobenv", ".session_env", "gateway.env"}:
                raise ValueError("Credential files cannot be included in research evidence")
            seen.add(name)
            source = path.parent / name
            if _regular_file(work, source).st_size != size:
                raise ValueError("Retained research file size changed")
            entry = {"path": "/work/" + str(source.relative_to(work)), "description": item["description"],
                     "bytes": size, "sha256": checksum, "contentState": "not_inlined_context_limit"}
            handoff["files"].append(entry)
            if size > MAX_INLINE_FILE_BYTES:
                entry["contentState"] = "not_inlined_file_limit"
            else:
                candidates.append((source, entry))
        context["handoffs"].append(handoff)
    if paths:
        context["inventoryState"] = "staged_handoffs_present"
    if _encoded_bytes(context) > max_bytes:
        raise ValueError("Complete research inventory exceeds the validator transport bound; nothing was truncated")
    # Do not let a raw bulk response displace a smaller complete source selection.
    for source, entry in sorted(candidates, key=lambda candidate: (candidate[1]["bytes"], str(candidate[0]))):
        content = _read_exact(work, source, MAX_INLINE_FILE_BYTES)
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("Retained research file checksum changed")
        if not content:
            entry["contentState"] = "empty_file"
            continue
        try:
            decoded = content.decode("utf-8")
            if "\x00" in decoded:
                raise UnicodeError("Binary content")
        except UnicodeError:
            entry["contentState"] = "not_inlined_binary"
            continue
        try:
            selected = json.loads(decoded, object_pairs_hook=_unique_object)
            _encoded_bytes(selected)  # Non-finite numbers must not change JSON meaning.
            representation = "json"
        except ValueError:
            # Keep ambiguous or nonstandard JSON as complete original text,
            # rather than silently dropping duplicate keys or coercing NaN.
            selected = decoded
            representation = "utf8_text"
        entry.update({"contentState": "included", "format": representation, "content": selected})
        if _encoded_bytes(context) > max_bytes:
            entry.pop("content")
            entry.pop("format")
            entry["contentState"] = "not_inlined_context_limit"
    return context
