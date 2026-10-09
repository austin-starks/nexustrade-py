"""Synthetic retained handoffs matching the native stage_research_evidence format."""

import hashlib
import json
from pathlib import Path


def stage_handoff(root: Path, files: dict[str, bytes], *, parent: str = "native-parent",
                  question: str = "Resolve the selected source components",
                  limitations: list[str] | None = None) -> Path:
    manifest = {
        "version": 1, "parentSessionId": parent, "sessionId": "native-child",
        "role": "research-investigator", "question": question,
        "summary": "Selected records retained; accounting choices remain parent-owned",
        "sources": [{"sourceId": "frozen-source", "locator": "statement note, exact period"}],
        "limitations": limitations if limitations is not None else ["Other contexts not queried"],
        "sourceVerification": "child-declared; parent must inspect source and selection",
        "files": [{"name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                   "description": "Complete selected records, not the original source"}
                  for name, content in files.items()],
    }
    # Native JSON.stringify emits compact bytes without a trailing newline.
    raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode()
    directory = (root / ".nexustrade/research-evidence" / hashlib.sha256(parent.encode()).hexdigest()
                 / hashlib.sha256(raw).hexdigest())
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(raw)
    for name, content in files.items():
        (directory / name).write_bytes(content)
    return directory
