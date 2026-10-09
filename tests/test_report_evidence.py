import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from nexustrade.report_evidence import MAX_INLINE_FILE_BYTES, staged_research_context
from tests.research_evidence_fixture import stage_handoff


class StagedReportEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()

    def tearDown(self):
        self.directory.cleanup()

    def test_retains_complete_selection_and_limitations_across_native_continuation_parents(self):
        accounting = {"dates": ["2024-12-31", "2025-12-31"], "selected": {"opening": 80, "closing": 100},
                      "coverage": {"other_liabilities": "unresolved classification"}, "label": "€ capital"}
        raw = json.dumps(accounting, ensure_ascii=False).encode()
        current = stage_handoff(self.root, {"0-capital.json": raw}, limitations=["Tax provision is not a normalized tax assumption"])
        stage_handoff(self.root, {"0-weather.json": b'{"location":"Station Z","temperature":0}'}, parent="retained-old-parent")
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        context = staged_research_context(self.root)
        self.assertEqual(context["inventoryState"], "staged_handoffs_present")
        self.assertEqual(len(context["handoffs"]), 2)
        handoff = next(row for row in context["handoffs"] if row["parentSessionId"] == "native-parent")
        entry = handoff["files"][0]
        self.assertEqual(entry["content"], accounting)
        self.assertEqual(entry["contentState"], "included")
        self.assertEqual(entry["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(entry["path"], "/work/" + str((current / "0-capital.json").relative_to(self.root)))
        self.assertEqual(handoff["limitations"], ["Tax provision is not a normalized tax assumption"])
        self.assertEqual(handoff["sources"][0]["sourceId"], "frozen-source")
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_empty_inventory_and_empty_selection_never_assert_source_absence(self):
        empty = staged_research_context(self.root)
        self.assertEqual(empty["inventoryState"], "no_staged_handoffs")
        self.assertIn("not", empty["sourceVerification"])
        stage_handoff(self.root, {"0-empty.json": b""})
        context = staged_research_context(self.root)
        self.assertEqual(context["handoffs"][0]["files"][0]["contentState"], "empty_file")
        self.assertNotIn("content", context["handoffs"][0]["files"][0])
        self.assertIn("No staged handoff, empty file", context["interpretation"])

    def test_large_binary_and_complete_utf8_files_have_distinct_states_without_truncation(self):
        stage_handoff(self.root, {"0-large.json": b"x" * (MAX_INLINE_FILE_BYTES + 1),
                                  "1-original.pdf": b"\x00\xffbinary", "2-text.md": "Complete café text".encode()})
        entries = staged_research_context(self.root)["handoffs"][0]["files"]
        self.assertEqual([row["contentState"] for row in entries], ["not_inlined_file_limit", "not_inlined_binary", "included"])
        self.assertEqual(entries[2]["content"], "Complete café text")
        self.assertTrue(all("content" not in row for row in entries[:2]))
        self.assertEqual(entries[0]["bytes"], MAX_INLINE_FILE_BYTES + 1)

    def test_context_budget_keeps_inventory_and_complete_smaller_selections(self):
        stage_handoff(self.root, {"0-raw.json": json.dumps({"raw": "x" * 10000}).encode(),
                                  "1-selection.json": b'{"selected_value":0,"coverage":"partial"}'})
        context = staged_research_context(self.root, max_bytes=2600)
        entries = context["handoffs"][0]["files"]
        self.assertEqual(entries[0]["contentState"], "not_inlined_context_limit")
        self.assertNotIn("content", entries[0])
        self.assertEqual(entries[1]["content"], {"selected_value": 0, "coverage": "partial"})
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()), 2600)
        with self.assertRaisesRegex(ValueError, "Complete research inventory"):
            staged_research_context(self.root, max_bytes=100)

    def test_budget_measures_encoded_json_and_does_not_head_tail_text(self):
        stage_handoff(self.root, {"0-escaped.txt": b"\\" * 1500})
        entry = staged_research_context(self.root, max_bytes=2800)["handoffs"][0]["files"][0]
        self.assertEqual(entry["contentState"], "not_inlined_context_limit")
        self.assertNotIn("content", entry)

    def test_ambiguous_json_is_kept_as_complete_text_without_last_key_wins_or_nonfinite_coercion(self):
        duplicates = b'{"value":80,"value":100,"coverage":"unresolved"}'
        nonfinite = b'{"value":NaN}'
        stage_handoff(self.root, {"0-duplicates.json": duplicates, "1-nonfinite.json": nonfinite})
        entries = staged_research_context(self.root)["handoffs"][0]["files"]
        self.assertEqual([entry["format"] for entry in entries], ["utf8_text", "utf8_text"])
        self.assertEqual(entries[0]["content"], duplicates.decode())
        self.assertEqual(entries[1]["content"], nonfinite.decode())

    def test_changed_bytes_same_length_and_missing_files_refuse_validation(self):
        directory = stage_handoff(self.root, {"0-selected.json": b'{"x":1}'})
        path = directory / "0-selected.json"
        path.write_bytes(b'{"x":2}')
        with self.assertRaisesRegex(ValueError, "checksum changed"):
            staged_research_context(self.root)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            staged_research_context(self.root)

    def test_symlinks_and_nonregular_files_are_never_read(self):
        directory = stage_handoff(self.root, {"0-selected.json": b"original"})
        path = directory / "0-selected.json"
        path.unlink()
        path.symlink_to(directory / "manifest.json")
        with self.assertRaisesRegex(ValueError, "without symlinks"):
            staged_research_context(self.root)
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(ValueError, "regular file"):
            staged_research_context(self.root)

    def test_wrong_manifest_identity_and_unsafe_file_name_are_rejected(self):
        directory = stage_handoff(self.root, {"0-selected.json": b"original"})
        wrong = directory.with_name("0" * 64)
        directory.rename(wrong)
        with self.assertRaisesRegex(ValueError, "manifest identity"):
            staged_research_context(self.root)
        wrong.rename(directory)
        raw = json.loads((directory / "manifest.json").read_text())
        raw["files"][0]["name"] = "../outside.json"
        encoded = json.dumps(raw, separators=(",", ":")).encode()
        (directory / "manifest.json").write_bytes(encoded)
        directory.rename(directory.with_name(hashlib.sha256(encoded).hexdigest()))
        with self.assertRaisesRegex(ValueError, "file inventory"):
            staged_research_context(self.root)

    def test_inflight_staging_is_not_a_published_handoff_but_missing_completed_manifest_is_corrupt(self):
        directory = stage_handoff(self.root, {"0-selected.json": b'{"x":1}'})
        inflight = directory.parent / ".staging-inflight"
        inflight.mkdir()
        (inflight / "manifest.json").write_text("partial write")
        self.assertEqual(len(staged_research_context(self.root)["handoffs"]), 1)
        (directory / "manifest.json").unlink()
        with self.assertRaises(FileNotFoundError):
            staged_research_context(self.root)

    def test_credential_files_and_symlinked_evidence_root_are_rejected(self):
        stage_handoff(self.root, {"0-auth.json": b"credentials"})
        with self.assertRaisesRegex(ValueError, "Credential files"):
            staged_research_context(self.root)
        root = self.root / ".nexustrade/research-evidence"
        root.rename(root.with_name("old-evidence"))
        root.symlink_to(root.with_name("old-evidence"), target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked directories"):
            staged_research_context(self.root)
