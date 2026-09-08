import importlib
import json
import os
import tempfile
import unittest


def _read(path: str) -> list[dict[str, object]]:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class ObservationLedgerTests(unittest.TestCase):
    """The ledger is what makes a projection auditable.

    A run that extracts a corpus and then narrows it into an output dataset
    otherwise delivers only the narrowed set, and a reviewer cannot separate a
    correct exclusion from a silent loss. These tests pin the merge semantics,
    because the naive versions of them both destroy evidence: overwriting loses
    every other document when one is re-extracted, and appending double-counts
    the re-extracted one.
    """

    def setUp(self) -> None:
        self.scanned_table = importlib.import_module("nexustrade.scanned_table")

    def test_writes_every_extracted_row(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            path = os.path.join(out_dir, "ledger.jsonl")
            written = self.scanned_table._persist_observation_ledger(
                {
                    "doc_a": {"rows": [{"ticker": "AAPL"}, {"ticker": "MSFT"}], "error": None},
                    "doc_b": {"rows": [{"ticker": "NVDA"}], "error": None},
                },
                ledger_path=path,
            )
            self.assertEqual(written, 3)
            rows = _read(path)
            self.assertEqual(len(rows), 3)
            # source_id is stamped from the document key so a row always names
            # the document it came from, even when the caller did not set it.
            self.assertEqual(
                sorted(str(row["source_id"]) for row in rows),
                ["doc_a", "doc_a", "doc_b"],
            )

    def test_subset_reextract_replaces_only_that_document(self) -> None:
        """The exact shape v9 produced: one document re-extracted, 65 untouched."""
        with tempfile.TemporaryDirectory() as out_dir:
            path = os.path.join(out_dir, "ledger.jsonl")
            self.scanned_table._persist_observation_ledger(
                {
                    "doc_a": {"rows": [{"n": 1}, {"n": 2}, {"n": 3}], "error": None},
                    "doc_b": {"rows": [{"n": 9}], "error": None},
                },
                ledger_path=path,
            )
            self.scanned_table._persist_observation_ledger(
                {"doc_a": {"rows": [{"n": 1}, {"n": 2}], "error": None}},
                ledger_path=path,
            )

            rows = _read(path)
            by_source: dict[str, int] = {}
            for row in rows:
                key = str(row["source_id"])
                by_source[key] = by_source.get(key, 0) + 1
            # doc_a shrank to its re-extracted truth; doc_b was not in the second
            # call and must survive untouched rather than being overwritten away.
            self.assertEqual(by_source, {"doc_a": 2, "doc_b": 1})

    def test_failed_document_does_not_erase_recorded_rows(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            path = os.path.join(out_dir, "ledger.jsonl")
            self.scanned_table._persist_observation_ledger(
                {"doc_a": {"rows": [{"n": 1}], "error": None}},
                ledger_path=path,
            )
            written = self.scanned_table._persist_observation_ledger(
                {"doc_a": {"rows": [], "error": "gateway timeout"}},
                ledger_path=path,
            )
            self.assertEqual(written, 0)
            self.assertEqual(len(_read(path)), 1)

    def test_empty_successful_reextract_does_clear_that_document(self) -> None:
        """Zero rows with no error is a real finding, not a failure to record."""
        with tempfile.TemporaryDirectory() as out_dir:
            path = os.path.join(out_dir, "ledger.jsonl")
            self.scanned_table._persist_observation_ledger(
                {"doc_a": {"rows": [{"n": 1}], "error": None}},
                ledger_path=path,
            )
            self.scanned_table._persist_observation_ledger(
                {"doc_a": {"rows": [], "error": None}},
                ledger_path=path,
            )
            self.assertEqual(_read(path), [])

    def test_is_a_noop_outside_a_sandbox(self) -> None:
        """`/work` is a sandbox convention; a library user must be unaffected."""
        missing = os.path.join(tempfile.gettempdir(), "nexustrade-no-such-dir", "ledger.jsonl")
        written = self.scanned_table._persist_observation_ledger(
            {"doc_a": {"rows": [{"n": 1}], "error": None}},
            ledger_path=missing,
        )
        self.assertEqual(written, 0)
        self.assertFalse(os.path.exists(missing))

    def test_malformed_existing_line_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            path = os.path.join(out_dir, "ledger.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not json at all\n")
            self.scanned_table._persist_observation_ledger(
                {"doc_a": {"rows": [{"n": 1}], "error": None}},
                ledger_path=path,
            )
            with open(path, "r", encoding="utf-8") as handle:
                body = handle.read()
            # Discarding an unreadable line would destroy evidence on a merge.
            self.assertIn("not json at all", body)

    def test_extract_pdfs_persists_without_being_asked(self) -> None:
        """The point of the change: retention is not something the run elects."""
        source = importlib.import_module("inspect").getsource(
            self.scanned_table.extract_pdfs
        )
        self.assertIn("_persist_observation_ledger(results)", source)


if __name__ == "__main__":
    unittest.main()
