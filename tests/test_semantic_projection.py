from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from nexustrade.semantic import SemanticProjectionError, derive_rows


DERIVED_SCHEMA = {
    "type": "object",
    "properties": {
        "economic_event": {"type": "string"},
        "eligible": {"type": "boolean"},
        "resolution_status": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}


class SemanticProjectionTest(unittest.TestCase):
    def test_preserves_raw_rows_and_orders_one_to_one_results(self) -> None:
        rows = [
            {"transaction_type": "P", "description": "Exercised contract"},
            {"transaction_type": "P", "description": "Acquired shares"},
        ]
        original = copy.deepcopy(rows)
        response = {
            "rows": [
                {
                    "input_index": 1,
                    "derived": {
                        "economic_event": "acquisition",
                        "eligible": True,
                        "resolution_status": "resolved",
                        "evidence": ["description"],
                    },
                },
                {
                    "input_index": 0,
                    "derived": {
                        "economic_event": "exercise",
                        "eligible": False,
                        "resolution_status": "resolved_conflict",
                        "evidence": ["transaction_type", "description"],
                    },
                },
            ]
        }
        captured: dict[str, object] = {}

        def fake_gateway_chat_json(**kwargs: object) -> object:
            captured.update(kwargs)
            return response

        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            result = derive_rows(
                rows,
                instruction="Select acquisitions, excluding exercises.",
                derived_schema=DERIVED_SCHEMA,
            )

        self.assertEqual(rows, original)
        self.assertEqual([row["raw"] for row in result], original)
        self.assertFalse(result[0]["derived"]["eligible"])
        self.assertTrue(result[1]["derived"]["eligible"])
        sent = json.loads(str(captured["prompt"]))
        self.assertEqual(sent["records"][0]["raw"], original[0])
        self.assertIn("every relevant field", str(captured["system"]))
        self.assertIn("conflicting event", str(captured["system"]))

    def test_rejects_missing_duplicate_and_out_of_range_indices(self) -> None:
        rows = [{"raw": "a"}, {"raw": "b"}]
        invalid_results = [
            {"rows": [{"input_index": 0, "derived": {}}]},
            {
                "rows": [
                    {"input_index": 0, "derived": {}},
                    {"input_index": 0, "derived": {}},
                ]
            },
            {
                "rows": [
                    {"input_index": 0, "derived": {}},
                    {"input_index": 2, "derived": {}},
                ]
            },
        ]
        for response in invalid_results:
            with self.subTest(response=response):
                with (
                    patch("nexustrade.host.gateway_chat_json", return_value=response),
                    self.assertRaises(SemanticProjectionError),
                ):
                    derive_rows(
                        rows,
                        instruction="Derive the requested predicate.",
                        derived_schema=DERIVED_SCHEMA,
                    )

    def test_empty_input_makes_no_model_call(self) -> None:
        with patch("nexustrade.host.gateway_chat_json") as gateway:
            self.assertEqual(
                derive_rows(
                    [],
                    instruction="Derive the requested predicate.",
                    derived_schema=DERIVED_SCHEMA,
                ),
                [],
            )
        gateway.assert_not_called()

    def test_makes_nested_schema_strict_and_rejects_missing_fields(self) -> None:
        nested_schema = {
            "type": "object",
            "properties": {
                "eligible": {"type": "boolean"},
                "decision": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                },
            },
        }
        captured: dict[str, object] = {}

        def fake_gateway_chat_json(**kwargs: object) -> object:
            captured.update(kwargs)
            return {
                "rows": [
                    {
                        "input_index": 0,
                        "derived": {
                            "eligible": True,
                            "decision": {"status": "resolved"},
                        },
                    }
                ]
            }

        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            derive_rows(
                [{"source": "row"}],
                instruction="Derive a decision.",
                derived_schema=nested_schema,
            )
        response_schema = captured["json_schema"]
        assert isinstance(response_schema, dict)
        row_schema = response_schema["properties"]["rows"]["items"]
        derived = row_schema["properties"]["derived"]
        self.assertFalse(derived["additionalProperties"])
        self.assertFalse(derived["properties"]["decision"]["additionalProperties"])
        self.assertEqual(derived["properties"]["decision"]["required"], ["status"])

        with (
            patch(
                "nexustrade.host.gateway_chat_json",
                return_value={
                    "rows": [
                        {"input_index": 0, "derived": {"eligible": True}}
                    ]
                },
            ),
            self.assertRaisesRegex(SemanticProjectionError, "derived keys"),
        ):
            derive_rows(
                [{"source": "row"}],
                instruction="Derive a decision.",
                derived_schema=nested_schema,
            )


if __name__ == "__main__":
    unittest.main()
