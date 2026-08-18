from __future__ import annotations

import copy
import json
import threading
import unittest
from unittest.mock import patch

from nexustrade.semantic import (
    DEFAULT_MAX_WORKERS,
    SemanticProjectionError,
    audit_inclusions,
    derive_rows,
    verify_semantic_citations,
)


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
    def test_default_parallelism_is_twenty_four(self) -> None:
        self.assertEqual(DEFAULT_MAX_WORKERS, 24)

    def test_semantic_verifier_forwards_only_the_bounded_contract(self) -> None:
        assertion = {
            "assertionId": "row-1",
            "completeRecordEvidence": {"event": "Purchased"},
            "criteria": [
                {
                    "criterionId": "purchase",
                    "positiveCondition": "The event is a purchase.",
                    "proposedOutcome": "false",
                    "proposedReason": "The event was not a purchase.",
                    "citedPaths": ["/event"],
                }
            ],
        }
        expected = {"evidenceId": "bundle-1", "assertions": []}
        with patch(
            "nexustrade.host.gateway_semantic_verify",
            return_value=expected,
        ) as gateway:
            result = verify_semantic_citations(
                evidence_id="bundle-1",
                request="Return purchase events.",
                assertions=[assertion],
                source_authority="Descriptions name the event.",
            )

        self.assertEqual(result, expected)
        gateway.assert_called_once_with(
            {
                "evidenceId": "bundle-1",
                "request": "Return purchase events.",
                "assertions": [assertion],
                "sourceAuthority": "Descriptions name the event.",
            }
        )

    def test_audits_only_direct_blockers_without_rewriting_source(self) -> None:
        rows = [
            {
                "raw_record": {"description": "Holding L.P. Units"},
                "candidate_derived": {
                    "requested_action": True,
                    "explicit_exclusion": False,
                },
            }
        ]
        captured: dict[str, object] = {}

        def fake_gateway_chat_json(**kwargs: object) -> object:
            captured.update(kwargs)
            return {
                "rows": [
                    {
                        "input_index": 0,
                        "derived": {
                            "required_predicate_contradicted": False,
                            "explicit_exclusion_present": True,
                            "reason": "The raw record directly matches an exclusion.",
                            "evidence_refs": [
                                {
                                    "predicate": "explicit_exclusion_present",
                                    "path": "/raw_record/description",
                                }
                            ],
                        },
                    }
                ]
            }

        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            result = audit_inclusions(
                rows,
                instruction="Include purchases but exclude partnership units.",
            )

        self.assertEqual(result[0]["raw"], rows[0])
        self.assertFalse(result[0]["derived"]["inclusion_supported"])
        self.assertEqual(
            result[0]["derived"]["evidence_refs"],
            [
                {
                    "predicate": "explicit_exclusion_present",
                    "path": "/raw_record/description",
                    "value": "Holding L.P. Units",
                }
            ],
        )
        payload = json.loads(str(captured["prompt"]))
        self.assertEqual(payload["records"][0]["raw"], rows[0])
        self.assertIn("directly matches an explicit", payload["instruction"])
        self.assertIn("exclusion in the caller's task instruction", payload["instruction"])
        self.assertIn("Absence of additional evidence is not a blocker", payload["instruction"])
        self.assertIn("Do not invent a new population predicate", payload["instruction"])
        self.assertIn("appears only as metadata", payload["instruction"])
        self.assertIn("natural-language description as a whole", payload["instruction"])
        self.assertIn("only the semantic dimension", payload["instruction"])
        self.assertNotIn("legal-form", payload["instruction"])
        self.assertIn("Audit required predicates as well as exclusions", payload["instruction"])
        self.assertIn("different event", payload["instruction"])
        response_schema = captured["json_schema"]
        assert isinstance(response_schema, dict)
        derived = response_schema["properties"]["rows"]["items"]["properties"]["derived"]
        self.assertEqual(
            set(derived["properties"]),
            {
                "required_predicate_contradicted",
                "explicit_exclusion_present",
                "reason",
                "evidence_refs",
            },
        )

    def test_mechanically_supports_an_inclusion_without_a_blocking_component(self) -> None:
        response = {
            "rows": [
                {
                    "input_index": 0,
                    "derived": {
                        "required_predicate_contradicted": False,
                        "explicit_exclusion_present": False,
                        "reason": "No direct blocker is present.",
                        "evidence_refs": [],
                    },
                }
            ]
        }
        with patch("nexustrade.host.gateway_chat_json", return_value=response):
            result = audit_inclusions(
                [{"description": "Eligible record with no direct blocker"}],
                instruction="Include records matching one predicate but exclude this category.",
                max_validation_retries=0,
            )
        self.assertTrue(result[0]["derived"]["inclusion_supported"])

    def test_resolves_record_local_evidence_refs_and_attaches_values(self) -> None:
        rows = [
            {
                "source": {
                    "description": "Purchased listed contract",
                    "a/b": ["first", "second"],
                }
            }
        ]
        response = {
            "rows": [
                {
                    "input_index": 0,
                    "derived": {
                        "economic_event": "purchase",
                        "eligible": True,
                        "resolution_status": "resolved",
                        "evidence": ["source description"],
                        "evidence_refs": [
                            {
                                "predicate": "economic_event",
                                "path": "/source/description",
                            },
                            {
                                "predicate": "eligible",
                                "path": "/source/a~1b/1",
                            },
                        ],
                    },
                }
            ]
        }

        with patch(
            "nexustrade.host.gateway_chat_json", return_value=response
        ) as gateway:
            result = derive_rows(
                rows,
                instruction={"request": "Select purchases."},
                derived_schema=DERIVED_SCHEMA,
                evidence_requirements={
                    "economic_event": "always",
                    "eligible": "truthy",
                },
            )

        self.assertEqual(result[0]["raw"], rows[0])
        self.assertEqual(
            result[0]["derived"]["evidence_refs"],
            [
                {
                    "predicate": "economic_event",
                    "path": "/source/description",
                    "value": "Purchased listed contract",
                },
                {
                    "predicate": "eligible",
                    "path": "/source/a~1b/1",
                    "value": "second",
                },
            ],
        )
        sent = json.loads(str(gateway.call_args.kwargs["prompt"]))
        self.assertEqual(json.loads(sent["instruction"]), {"request": "Select purchases."})
        self.assertIn("RFC 6901", str(gateway.call_args.kwargs["system"]))

    def test_rejects_missing_cross_record_and_non_scalar_evidence(self) -> None:
        rows = [{"description": "Purchased contract", "nested": {"value": "x"}}]
        invalid_refs = [
            [],
            [{"predicate": "eligible", "path": "/neighbor/description"}],
            [{"predicate": "eligible", "path": "/nested"}],
            [
                {"predicate": "eligible", "path": "/description"},
                {"predicate": "eligible", "path": "/description"},
            ],
            [{"predicate": "eligible", "path": "/nested/~2"}],
        ]
        for refs in invalid_refs:
            with self.subTest(refs=refs):
                response = {
                    "rows": [
                        {
                            "input_index": 0,
                            "derived": {
                                "economic_event": "purchase",
                                "eligible": True,
                                "resolution_status": "resolved",
                                "evidence": ["description"],
                                "evidence_refs": refs,
                            },
                        }
                    ]
                }
                with (
                    patch(
                        "nexustrade.host.gateway_chat_json",
                        return_value=response,
                    ),
                    self.assertRaises(SemanticProjectionError),
                ):
                    derive_rows(
                        rows,
                        instruction="Select purchases.",
                        derived_schema=DERIVED_SCHEMA,
                        evidence_requirements={"eligible": "truthy"},
                    )

    def test_applies_conditional_evidence_requirements(self) -> None:
        rows = [{"description": "No direct blocker"}]
        cases = [
            ("truthy", False, []),
            ("falsey", False, [{"predicate": "eligible", "path": "/description"}]),
            ("nonempty", "", []),
            (
                "nonempty",
                "resolved",
                [{"predicate": "resolution_status", "path": "/description"}],
            ),
        ]
        for requirement, value, refs in cases:
            with self.subTest(requirement=requirement, value=value):
                predicate = (
                    "resolution_status" if requirement == "nonempty" else "eligible"
                )
                response = {
                    "rows": [
                        {
                            "input_index": 0,
                            "derived": {
                                "economic_event": "unknown",
                                "eligible": value if predicate == "eligible" else False,
                                "resolution_status": (
                                    value if predicate == "resolution_status" else "unresolved"
                                ),
                                "evidence": [],
                                "evidence_refs": refs,
                            },
                        }
                    ]
                }
                with patch(
                    "nexustrade.host.gateway_chat_json", return_value=response
                ):
                    result = derive_rows(
                        rows,
                        instruction="Evaluate the requested predicate.",
                        derived_schema=DERIVED_SCHEMA,
                        evidence_requirements={predicate: requirement},  # type: ignore[dict-item]
                    )
                self.assertEqual(
                    result[0]["derived"]["evidence_refs"],
                    [
                        {
                            **ref,
                            "value": "No direct blocker",
                        }
                        for ref in refs
                    ],
                )

    def test_rejects_invalid_evidence_contract_before_model_call(self) -> None:
        with patch("nexustrade.host.gateway_chat_json") as gateway:
            with self.assertRaises(SemanticProjectionError):
                derive_rows(
                    [{"description": "x"}],
                    instruction="Select records.",
                    derived_schema=DERIVED_SCHEMA,
                    evidence_requirements={"missing": "always"},
                )
            with self.assertRaises(SemanticProjectionError):
                derive_rows(
                    [{"description": "x"}],
                    instruction="Select records.",
                    derived_schema=DERIVED_SCHEMA,
                    evidence_requirements={"eligible": "sometimes"},  # type: ignore[dict-item]
                )
        gateway.assert_not_called()

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
        self.assertIn("related economic outcome", str(captured["system"]))
        self.assertIn("ordinary semantic meaning", str(captured["system"]))

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
            ({"max_validation_retries": -1}, "non-negative integer"),
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

    def test_retries_single_row_validation_with_feedback(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_gateway_chat_json(**kwargs: object) -> object:
            payload = json.loads(str(kwargs["prompt"]))
            calls.append(payload)
            evidence_refs = []
            if len(calls) > 1:
                evidence_refs = [
                    {"predicate": "eligible", "path": "/description"}
                ]
            return {
                "rows": [
                    {
                        "input_index": 0,
                        "derived": {
                            "economic_event": "purchase",
                            "eligible": True,
                            "resolution_status": "resolved",
                            "evidence": [],
                            "evidence_refs": evidence_refs,
                        },
                    }
                ]
            }

        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            result = derive_rows(
                [{"description": "Purchased listed contract"}],
                instruction="Select the requested event.",
                derived_schema=DERIVED_SCHEMA,
                evidence_requirements={"eligible": "truthy"},
            )

        self.assertEqual(len(calls), 2)
        self.assertNotIn("validation_feedback", calls[0])
        self.assertIn("omitted required evidence", calls[1]["validation_feedback"])
        self.assertEqual(
            result[0]["derived"]["evidence_refs"][0]["value"],
            "Purchased listed contract",
        )

    def test_isolates_evidence_owned_rows_and_retries_only_the_invalid_record(self) -> None:
        calls: list[dict[str, object]] = []
        attempts: dict[str, int] = {}

        def fake_gateway_chat_json(**kwargs: object) -> object:
            payload = json.loads(str(kwargs["prompt"]))
            calls.append(payload)
            records = payload["records"]
            self.assertEqual(len(records), 1)
            description = records[0]["raw"]["description"]
            attempts[description] = attempts.get(description, 0) + 1
            path = (
                "/missing"
                if description.endswith("B") and attempts[description] == 1
                else "/description"
            )
            return {
                "rows": [
                    {
                        "input_index": 0,
                        "derived": {
                            "economic_event": "purchase",
                            "eligible": True,
                            "resolution_status": "resolved",
                            "evidence": [],
                            "evidence_refs": [
                                {"predicate": "eligible", "path": path}
                            ],
                        },
                    }
                ]
            }

        rows = [
            {"description": "Purchased listed contract A"},
            {"description": "Purchased listed contract B"},
        ]
        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            result = derive_rows(
                rows,
                instruction="Select the requested event.",
                derived_schema=DERIVED_SCHEMA,
                evidence_requirements={"eligible": "truthy"},
                batch_size=40,
                max_workers=1,
            )

        self.assertEqual([len(call["records"]) for call in calls], [1, 1, 1])
        self.assertNotIn("validation_feedback", calls[0])
        self.assertNotIn("validation_feedback", calls[1])
        self.assertIn("valid non-empty scalar paths", calls[2]["validation_feedback"])
        self.assertEqual(
            calls[2]["records"][0]["raw"],
            {"description": "Purchased listed contract B"},
        )
        self.assertEqual([row["raw"] for row in result], rows)
        self.assertEqual(
            [row["derived"]["evidence_refs"][0]["value"] for row in result],
            ["Purchased listed contract A", "Purchased listed contract B"],
        )

    def test_retry_feedback_lists_valid_same_row_scalar_paths(self) -> None:
        response = {
            "rows": [
                {
                    "input_index": 0,
                    "derived": {
                        "economic_event": "purchase",
                        "eligible": True,
                        "resolution_status": "resolved",
                        "evidence": [],
                        "evidence_refs": [
                            {"predicate": "eligible", "path": "/nested/missing"}
                        ],
                    },
                }
            ]
        }
        with (
            patch("nexustrade.host.gateway_chat_json", return_value=response),
            self.assertRaisesRegex(
                SemanticProjectionError,
                "valid non-empty scalar paths.*'/description'.*'/nested/value'",
            ),
        ):
            derive_rows(
                [{"description": "Purchased contract", "nested": {"value": "x"}}],
                instruction="Select the requested event.",
                derived_schema=DERIVED_SCHEMA,
                evidence_requirements={"eligible": "truthy"},
                max_validation_retries=0,
            )

    def test_retry_feedback_covers_late_record_branch_after_large_contract(self) -> None:
        response = {
            "rows": [
                {
                    "input_index": 0,
                    "derived": {
                        "economic_event": "purchase",
                        "eligible": True,
                        "resolution_status": "resolved",
                        "evidence": [],
                        "evidence_refs": [
                            {
                                "predicate": "eligible",
                                "path": "/complete_reconstructed_record/0/id",
                            }
                        ],
                    },
                }
            ]
        }
        raw = {
            "source_row": "row-1",
            "record_criteria": [
                {
                    "id": f"criterion-{index}",
                    "condition": f"condition-{index}",
                    "interpretation": f"interpretation-{index}",
                }
                for index in range(700)
            ],
            "complete_reconstructed_record": [
                {"id": None, "asset": "Purchased listed contract"}
            ],
        }
        with (
            patch("nexustrade.host.gateway_chat_json", return_value=response),
            self.assertRaisesRegex(
                SemanticProjectionError,
                "valid non-empty scalar paths.*"
                "'/complete_reconstructed_record/0/asset'",
            ),
        ):
            derive_rows(
                [raw],
                instruction="Select the requested event.",
                derived_schema=DERIVED_SCHEMA,
                evidence_requirements={"eligible": "truthy"},
                max_validation_retries=0,
            )

    def test_retries_terminal_multi_row_validation_batch(self) -> None:
        calls = 0

        def fake_gateway_chat_json(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            payload = json.loads(str(kwargs["prompt"]))
            selected = payload["records"] if calls > 1 else payload["records"][:1]
            return {
                "rows": [
                    {
                        "input_index": record["input_index"],
                        "derived": {
                            "economic_event": "purchase",
                            "eligible": True,
                            "resolution_status": "resolved",
                            "evidence": [],
                        },
                    }
                    for record in selected
                ]
            }

        rows = [{"row": index} for index in range(4)]
        with patch("nexustrade.host.gateway_chat_json", fake_gateway_chat_json):
            result = derive_rows(
                rows,
                instruction="Select the requested event.",
                derived_schema=DERIVED_SCHEMA,
                batch_size=4,
                max_workers=1,
                max_split_depth=0,
            )
        self.assertEqual(calls, 2)
        self.assertEqual([row["raw"] for row in result], rows)

    def test_honors_zero_single_row_validation_retries(self) -> None:
        response = {
            "rows": [
                {
                    "input_index": 0,
                    "derived": {
                        "economic_event": "purchase",
                        "eligible": True,
                        "resolution_status": "resolved",
                        "evidence": [],
                        "evidence_refs": [],
                    },
                }
            ]
        }
        with (
            patch(
                "nexustrade.host.gateway_chat_json", return_value=response
            ) as gateway,
            self.assertRaises(SemanticProjectionError),
        ):
            derive_rows(
                [{"description": "Purchased listed contract"}],
                instruction="Select the requested event.",
                derived_schema=DERIVED_SCHEMA,
                evidence_requirements={"eligible": "truthy"},
                max_validation_retries=0,
            )
        gateway.assert_called_once()

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

        self.assertEqual(calls, 5)

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
