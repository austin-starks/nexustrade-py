from __future__ import annotations

import copy
import json
import threading
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
        self.assertIn("Do not assume a universal priority", str(captured["system"]))
        self.assertIn("roles and authority", str(captured["system"]))

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

    def test_batches_large_inputs_and_restores_global_order(self) -> None:
        rows = [{"row": index} for index in range(85)]
        seen_batch_sizes: list[int] = []
        lock = threading.Lock()

        def fake_gateway_chat_json(**kwargs: object) -> object:
            payload = json.loads(str(kwargs["prompt"]))
            records = payload["records"]
            with lock:
                seen_batch_sizes.append(len(records))
            return {
                "rows": [
                    {
                        "input_index": record["input_index"],
                        "derived": {
                            "economic_event": "purchase",
                            "eligible": True,
                            "resolution_status": "resolved",
                            "evidence": [str(record["raw"]["row"])],
                        },
                    }
                    for record in reversed(records)
                ]
            }

        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            result = derive_rows(
                rows,
                instruction="Derive the requested predicate.",
                derived_schema=DERIVED_SCHEMA,
            )

        self.assertCountEqual(seen_batch_sizes, [40, 40, 5])
        self.assertEqual([row["raw"] for row in result], rows)
        self.assertEqual(
            [row["derived"]["evidence"] for row in result],
            [[str(index)] for index in range(85)],
        )

    def test_bounds_parallel_batch_calls(self) -> None:
        rows = [{"row": index} for index in range(6)]
        lock = threading.Lock()
        release = threading.Event()
        active = 0
        peak_active = 0

        def fake_gateway_chat_json(**kwargs: object) -> object:
            nonlocal active, peak_active
            payload = json.loads(str(kwargs["prompt"]))
            record = payload["records"][0]
            with lock:
                active += 1
                peak_active = max(peak_active, active)
                if active == 3:
                    release.set()
            release.wait(timeout=1)
            with lock:
                active -= 1
            return {
                "rows": [
                    {
                        "input_index": 0,
                        "derived": {
                            "economic_event": "purchase",
                            "eligible": True,
                            "resolution_status": "resolved",
                            "evidence": [str(record["raw"]["row"])],
                        },
                    }
                ]
            }

        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            result = derive_rows(
                rows,
                instruction="Derive the requested predicate.",
                derived_schema=DERIVED_SCHEMA,
                batch_size=1,
                max_workers=3,
            )

        self.assertEqual(peak_active, 3)
        self.assertEqual([row["raw"] for row in result], rows)

    def test_bisects_a_partial_response_without_forwarding_it(self) -> None:
        rows = [{"row": index} for index in range(4)]
        seen_batch_sizes: list[int] = []

        def fake_gateway_chat_json(**kwargs: object) -> object:
            payload = json.loads(str(kwargs["prompt"]))
            records = payload["records"]
            seen_batch_sizes.append(len(records))
            selected = records if len(records) == 1 else records[:1]
            return {
                "rows": [
                    {
                        "input_index": record["input_index"],
                        "derived": {
                            "economic_event": "purchase",
                            "eligible": True,
                            "resolution_status": "resolved",
                            "evidence": [str(record["raw"]["row"])],
                        },
                    }
                    for record in selected
                ]
            }

        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            result = derive_rows(
                rows,
                instruction="Derive the requested predicate.",
                derived_schema=DERIVED_SCHEMA,
                batch_size=4,
                max_workers=1,
            )

        self.assertEqual(seen_batch_sizes, [4, 2, 1, 1, 2, 1, 1])
        self.assertEqual([row["raw"] for row in result], rows)

    def test_rejects_invalid_batch_controls_before_calling_the_model(self) -> None:
        for kwargs, message in (
            ({"batch_size": 0}, "positive integer"),
            ({"max_workers": 0}, "positive integer"),
            ({"max_split_depth": -1}, "non-negative integer"),
        ):
            with self.subTest(kwargs=kwargs):
                with (
                    patch("nexustrade.host.gateway_chat_json") as gateway,
                    self.assertRaisesRegex(ValueError, message),
                ):
                    derive_rows(
                        [{"row": 1}],
                        instruction="Derive the requested predicate.",
                        derived_schema=DERIVED_SCHEMA,
                        **kwargs,
                    )
                gateway.assert_not_called()

    def test_bounds_recursive_recovery_for_systemically_partial_responses(self) -> None:
        calls = 0

        def fake_gateway_chat_json(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            payload = json.loads(str(kwargs["prompt"]))
            record = payload["records"][0]
            return {
                "rows": [
                    {
                        "input_index": record["input_index"],
                        "derived": {
                            "economic_event": "purchase",
                            "eligible": True,
                            "resolution_status": "resolved",
                            "evidence": ["partial"],
                        },
                    }
                ]
            }

        with (
            patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json),
            self.assertRaises(SemanticProjectionError),
        ):
            derive_rows(
                [{"row": index} for index in range(8)],
                instruction="Derive the requested predicate.",
                derived_schema=DERIVED_SCHEMA,
                batch_size=8,
                max_workers=1,
                max_split_depth=2,
            )

        self.assertEqual(calls, 3)

    def test_does_not_multiply_gateway_transport_failures(self) -> None:
        calls = 0

        def fake_gateway_chat_json(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            raise RuntimeError("gateway retry budget exhausted")

        with (
            patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json),
            self.assertRaisesRegex(RuntimeError, "gateway retry budget exhausted"),
        ):
            derive_rows(
                [{"row": index} for index in range(8)],
                instruction="Derive the requested predicate.",
                derived_schema=DERIVED_SCHEMA,
                batch_size=8,
                max_workers=1,
            )

        self.assertEqual(calls, 1)

    def test_reports_the_global_source_row_range_for_malformed_batches(self) -> None:
        rows = [{"row": index} for index in range(5)]

        def fake_gateway_chat_json(**kwargs: object) -> object:
            payload = json.loads(str(kwargs["prompt"]))
            record = payload["records"][0]
            if record["raw"]["row"] < 4:
                records = payload["records"]
            else:
                records = []
            return {
                "rows": [
                    {
                        "input_index": item["input_index"],
                        "derived": {
                            "economic_event": "purchase",
                            "eligible": True,
                            "resolution_status": "resolved",
                            "evidence": [str(item["raw"]["row"])],
                        },
                    }
                    for item in records
                ]
            }

        with (
            patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json),
            self.assertRaisesRegex(
                SemanticProjectionError,
                "source row range 4-4",
            ),
        ):
            derive_rows(
                rows,
                instruction="Derive the requested predicate.",
                derived_schema=DERIVED_SCHEMA,
                batch_size=4,
                max_workers=1,
            )

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
