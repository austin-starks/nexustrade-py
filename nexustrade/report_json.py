"""Shared complete report JSON envelope and literal pointer operations."""
from __future__ import annotations
import json
from typing import Any

REPORT_INPUTS_MAX_BYTES = 4 * 1024 * 1024
REPORT_JSON_MAX_DEPTH = 128

def assert_report_envelope(value: Any) -> None:
    pending = [(value, 0, frozenset())]
    while pending:
        node, depth, ancestors = pending.pop()
        if depth > REPORT_JSON_MAX_DEPTH:
            raise ValueError(f"report_capacity_exceeded: Report JSON exceeds depth {REPORT_JSON_MAX_DEPTH}; no claims were skipped")
        if isinstance(node, (list, dict)):
            if id(node) in ancestors:
                raise ValueError("report_capacity_exceeded: Report JSON contains a cycle")
            ancestry = ancestors | {id(node)}
            pending.extend((child, depth + 1, ancestry) for child in (node.values() if isinstance(node, dict) else node))
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > REPORT_INPUTS_MAX_BYTES:
        raise ValueError(f"report_capacity_exceeded: Report JSON exceeds {REPORT_INPUTS_MAX_BYTES} bytes; no claims were skipped")

def _resolve_pointer(root: Any, pointer: str) -> Any:
    if pointer == "":
        return root
    if not pointer.startswith("/"):
        raise ValueError("Validator inputPath must be an RFC 6901 pointer")
    value = root
    for component in pointer[1:].split("/"):
        # Validate the machine pointer format; no semantic keyword matching.
        index = 0
        while index < len(component):
            if component[index] == "~":
                if index + 1 >= len(component) or component[index + 1] not in "01":
                    raise ValueError("Invalid validator pointer escape")
                index += 1
            index += 1
        key = component.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isascii() or not key.isdecimal() or str(int(key)) != key:
                raise ValueError("Invalid validator array pointer")
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError("Validator pointer does not resolve")
    return value
