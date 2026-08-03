"""Contract tests for the publishable NexusTrade JSON client."""

from __future__ import annotations

import datetime
import json
import os
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from unittest import mock

from nexustrade import client as client_module


class FakeTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body=None,
        idempotency_key=None,
    ) -> dict:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "idempotency_key": idempotency_key,
            }
        )
        scripted = self.responses.pop(0)
        # A scripted exception simulates a transport-layer failure mid-poll.
        if isinstance(scripted, Exception):
            raise scripted
        return scripted


class UploadingTransport(FakeTransport):
    """FakeTransport that can also record presigned PUTs."""

    def __init__(self, responses: list[dict]) -> None:
        super().__init__(responses)
        self.uploads: list[dict] = []

    def put_bytes(self, url: str, data: bytes, *, content_type: str) -> None:
        self.uploads.append(
            {"url": url, "data": data, "content_type": content_type}
        )


class AlwaysRunningTransport:
    """Never terminal — for asserting the waiter's own give-up behavior."""

    def __init__(self) -> None:
        self.call_count = 0

    def request(self, method: str, path: str, **_: object) -> dict:
        self.call_count += 1
        return {"operation": {"id": "bt-1", "kind": "backtest", "status": "running"}}


class NexusTradeClientTests(unittest.TestCase):
    def test_create_portfolio_uses_stable_json_contract(self) -> None:
        transport = FakeTransport(
            [{"portfolio": {"portfolioId": "p-1", "portfolioName": "Book"}}]
        )
        client = client_module.NexusTradeClient(transport=transport)

        result = client.create_portfolio(
            {"name": "Book", "strategies": [{"name": "s"}]},
            idempotency_key="book-v1",
        )

        self.assertEqual(result["portfolioId"], "p-1")
        self.assertEqual(
            transport.calls,
            [
                {
                    "method": "POST",
                    "path": "portfolios",
                    "body": {
                        "name": "Book",
                        "strategies": [{"name": "s"}],
                    },
                    "idempotency_key": "book-v1",
                }
            ],
        )

    def test_backtest_batch_returns_operation_handles(self) -> None:
        transport = FakeTransport(
            [
                {
                    "operations": [
                        {
                            "id": "bt-1",
                            "kind": "backtest",
                            "status": "running",
                        }
                    ]
                }
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)

        operations = client.create_backtests(
            [
                {
                    "portfolio": {"name": "Book"},
                    "startDate": "2024-01-01",
                    "endDate": "2024-12-31",
                }
            ],
            idempotency_key="bt-v1",
        )

        self.assertEqual(operations[0]["id"], "bt-1")
        self.assertEqual(transport.calls[0]["path"], "backtests/batch")

    def test_generated_backtest_handle_is_normalized_without_raw_wire_json(self) -> None:
        transport = FakeTransport(
            [
                {
                    "operations": [
                        {
                            "id": "bt-1",
                            "kind": "backtest",
                            "status": "running",
                        }
                    ]
                }
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)

        operation = client.create_backtest(
            {
                "tool": "backtest_portfolio",
                "portfolio": {"name": "Book", "strategies": [{"name": "s"}]},
                "args": {
                    "start_date": "2024-01-01",
                    "end_date": "2024-12-31",
                    "baseline_symbol": "QQQ",
                    "initial_value": 25_000,
                },
            },
            idempotency_key="bt-generated-v1",
        )

        self.assertEqual(operation["id"], "bt-1")
        self.assertEqual(
            transport.calls[0]["body"],
            {
                "backtests": [
                    {
                        "portfolio": {
                            "name": "Book",
                            "strategies": [{"name": "s"}],
                        },
                        "startDate": "2024-01-01",
                        "endDate": "2024-12-31",
                        "baseline": "QQQ",
                        "initialValue": 25_000,
                    }
                ]
            },
        )

    def test_backtest_client_rejects_non_backtest_generated_handles(self) -> None:
        client = client_module.NexusTradeClient(
            transport=FakeTransport([]),
        )

        with self.assertRaisesRegex(ValueError, "backtest"):
            client.create_backtests(
                [
                    {
                        "tool": "optimize_portfolio",
                        "portfolio": {},
                        "args": {},
                    }
                ],
                idempotency_key="wrong-handle",
            )

    def test_job_builders_send_portfolio_and_args_without_internal_tool_name(self) -> None:
        transport = FakeTransport(
            [
                {
                    "operation": {
                        "id": "opt-1",
                        "kind": "optimization",
                        "status": "running",
                    }
                }
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)

        operation = client.create_optimization(
            {
                "tool": "optimize_portfolio",
                "portfolio": {"name": "Book"},
                "args": {"start_date": "2022-01-01"},
            },
            idempotency_key="opt-v1",
        )

        self.assertEqual(operation["id"], "opt-1")
        self.assertEqual(
            transport.calls[0]["body"],
            {
                "portfolio": {"name": "Book"},
                "args": {"start_date": "2022-01-01"},
            },
        )

    def test_walk_forward_and_owner_scoped_reads_use_public_paths(self) -> None:
        transport = FakeTransport(
            [
                {
                    "operation": {
                        "id": "wf-1",
                        "kind": "walk_forward",
                        "status": "running",
                    }
                },
                {
                    "operation": {
                        "id": "wf-1",
                        "kind": "walk_forward",
                        "status": "completed",
                        "result": {"studyId": "wf-1", "status": "COMPLETE"},
                    }
                },
                {
                    "operation": {
                        "id": "bt-1",
                        "kind": "backtest",
                        "status": "running",
                    }
                },
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)
        handle = {
            "tool": "run_walk_forward_study",
            "portfolio": {"name": "Book"},
            "args": {"global_start_date": "2022-01-01"},
        }

        created = client.create_walk_forward(
            handle,
            idempotency_key="wf-v1",
        )
        completed = client.get_walk_forward("wf-1")
        backtest = client.get_backtest("bt-1")

        self.assertEqual(created["id"], "wf-1")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(backtest["id"], "bt-1")
        self.assertEqual(
            [call["path"] for call in transport.calls],
            [
                "walk-forward-studies",
                "walk-forward-studies/wf-1",
                "backtests/bt-1",
            ],
        )

    def test_http_transport_sends_bearer_and_idempotency_headers(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(
            {"portfolio": {"portfolioId": "p-1"}}
        ).encode("utf-8")
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with mock.patch.object(
            client_module,
            "_urlopen",
            return_value=response,
        ) as urlopen:
            transport = client_module.HttpTransport(
                "sk-temp",
                "https://gateway.example/api/v1",
            )
            result = transport.request(
                "POST",
                "portfolios",
                body={"name": "Book"},
                idempotency_key="book-v1",
            )

        self.assertEqual(result["portfolio"]["portfolioId"], "p-1")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://gateway.example/api/v1/nexustrade/portfolios",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-temp")
        self.assertEqual(request.get_header("Idempotency-key"), "book-v1")
        self.assertNotIn("sk-temp", repr(transport))

    def test_http_transport_rejects_malformed_credentials_and_base_urls(self) -> None:
        for key in ("", "sk-token\nInjected: value", "sk token"):
            with self.subTest(key=repr(key)):
                with self.assertRaisesRegex(ValueError, "api_key"):
                    client_module.HttpTransport(
                        key,
                        "https://gateway.example/api/v1",
                    )
        for url in (
            "https://user:pass@gateway.example/api/v1",
            "https://gateway.example/api/v1?tenant=other",
            "https://gateway.example/api/v1#fragment",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    client_module.HttpTransport("sk-temp", url)

    def test_socket_failures_raise_stable_transport_error(self) -> None:
        with mock.patch.object(
            client_module,
            "_urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            with self.assertRaises(client_module.NexusTradeApiError) as raised:
                client_module.HttpTransport(
                    "sk-temp",
                    "https://gateway.example/api/v1",
                ).request("GET", "backtests/bt-1")

        self.assertEqual(raised.exception.status, 0)
        self.assertEqual(raised.exception.code, "transport_error")

    def test_http_errors_raise_stable_api_error(self) -> None:
        body = BytesIO(
            json.dumps(
                {
                    "error": {
                        "code": "insufficient_scope",
                        "message": "write required",
                    }
                }
            ).encode("utf-8")
        )
        error = urllib.error.HTTPError(
            "https://gateway.example",
            403,
            "Forbidden",
            {},
            body,
        )
        with mock.patch.object(
            client_module,
            "_urlopen",
            side_effect=error,
        ):
            with self.assertRaises(client_module.NexusTradeApiError) as raised:
                client_module.HttpTransport(
                    "sk-temp",
                    "https://gateway.example/api/v1",
                ).request("GET", "backtests/bt-1")

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "insufficient_scope")

    def test_environment_does_not_reuse_unrelated_openai_credentials(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "sk-unrelated",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
            },
            clear=True,
        ):
            with self.assertRaises(ValueError):
                client_module.NexusTradeClient.from_environment()

    def test_http_transport_rejects_invalid_success_json(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b"not-json"
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with mock.patch.object(
            client_module,
            "_urlopen",
            return_value=response,
        ):
            with self.assertRaises(client_module.NexusTradeApiError) as raised:
                client_module.HttpTransport(
                    "sk-temp",
                    "https://gateway.example/api/v1",
                ).request("GET", "backtests/bt-1")

        self.assertEqual(raised.exception.code, "invalid_response")

    def test_http_transport_rejects_cross_origin_redirects(self) -> None:
        handler = client_module._SameOriginRedirectHandler()
        request = urllib.request.Request(
            "https://gateway.example/api/v1/nexustrade/portfolios",
            headers={"Authorization": "Bearer sk-temp"},
        )

        with self.assertRaises(client_module.NexusTradeApiError) as raised:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://attacker.example/collect",
            )

        self.assertEqual(raised.exception.code, "unsafe_redirect")

    def test_same_origin_redirect_on_a_mutation_is_refused(self) -> None:
        # urllib's default downgrades a redirected POST to a bodyless GET; the
        # TypeScript SDK's manual loop would re-POST and double-submit a paid
        # job. Both SDKs refuse, so the two cannot disagree.
        handler = client_module._SameOriginRedirectHandler()
        request = urllib.request.Request(
            "https://gateway.example/api/v1/nexustrade/backtests/batch",
            data=b"{}",
            headers={"Authorization": "Bearer sk-temp"},
            method="POST",
        )

        with self.assertRaises(client_module.NexusTradeApiError) as raised:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://gateway.example/api/v1/nexustrade/backtests/batch/",
            )

        self.assertEqual(raised.exception.code, "unsafe_redirect")
        self.assertIn("POST", raised.exception.message)

    def test_same_origin_redirect_on_a_read_is_followed(self) -> None:
        handler = client_module._SameOriginRedirectHandler()
        request = urllib.request.Request(
            "https://gateway.example/api/v1/nexustrade/backtests/bt-1",
            headers={"Authorization": "Bearer sk-temp"},
        )

        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://gateway.example/api/v1/nexustrade/backtests/bt-2",
        )

        self.assertIsNotNone(redirected)
        self.assertEqual(
            redirected.full_url,
            "https://gateway.example/api/v1/nexustrade/backtests/bt-2",
        )

    def test_redirect_budget_matches_the_typescript_sdk(self) -> None:
        self.assertEqual(client_module._MAX_REDIRECTS, 5)
        self.assertEqual(
            client_module._SameOriginRedirectHandler.max_redirections,
            client_module._MAX_REDIRECTS,
        )

    def test_wait_times_out_without_cancelling_the_job(self) -> None:
        # A timeout is a client-side give-up: the job keeps running, so the
        # message must not imply the work was cancelled or should be resubmitted.
        transport = AlwaysRunningTransport()
        client = client_module.NexusTradeClient(transport=transport)

        with self.assertRaises(client_module.NexusTradeApiError) as raised:
            client.wait_for_backtest(
                "bt-1",
                timeout_seconds=0.05,
                poll_interval_seconds=0,
            )

        self.assertEqual(raised.exception.code, "operation_timeout")
        self.assertIn("still running", raised.exception.message)
        self.assertGreater(transport.call_count, 0)

    def test_wait_can_return_a_failed_envelope_instead_of_raising(self) -> None:
        transport = FakeTransport(
            [{"operation": {"id": "bt-1", "status": "failed", "error": {"code": "x"}}}]
        )
        client = client_module.NexusTradeClient(transport=transport)

        operation = client.wait_for_backtest(
            "bt-1",
            poll_interval_seconds=0,
            raise_on_failure=False,
        )

        self.assertEqual(operation["status"], "failed")

    def test_wait_backs_off_and_respects_the_interval_ceiling(self) -> None:
        slept: list[float] = []
        responses = [{"operation": {"id": "bt-1", "status": "running"}} for _ in range(6)]
        responses.append({"operation": {"id": "bt-1", "status": "completed"}})
        transport = FakeTransport(responses)
        client = client_module.NexusTradeClient(transport=transport)

        with mock.patch.object(client_module.time, "sleep", slept.append):
            client.wait_for_backtest("bt-1", poll_interval_seconds=2)

        # 2 -> 3 -> 4.5 -> 6.75 -> 10.125 -> capped at 15.
        self.assertEqual(slept[:5], [2, 3, 4.5, 6.75, 10.125])
        self.assertTrue(all(delay <= 15 for delay in slept))

    def test_wait_does_not_swallow_transport_failures(self) -> None:
        # Reporting an outage as "still running" would hide it behind a timeout.
        transport = FakeTransport(
            [client_module.NexusTradeApiError(0, "transport_error", "dns")]
        )
        client = client_module.NexusTradeClient(transport=transport)

        with self.assertRaises(client_module.NexusTradeApiError) as raised:
            client.wait_for_backtest("bt-1", poll_interval_seconds=0)

        self.assertEqual(raised.exception.code, "transport_error")

    def test_wait_rejects_a_nonpositive_timeout(self) -> None:
        client = client_module.NexusTradeClient(transport=FakeTransport([]))
        with self.assertRaises(ValueError):
            client.wait_for_backtest("bt-1", timeout_seconds=0)

    def test_http_transport_requires_https_except_on_loopback(self) -> None:
        with self.assertRaises(ValueError):
            client_module.HttpTransport(
                "sk-temp",
                "http://gateway.example/api/v1",
            )

        loopback = client_module.HttpTransport(
            "sk-temp",
            "http://127.0.0.1:3000/api/v1",
        )
        self.assertEqual(loopback.base_url, "http://127.0.0.1:3000/api/v1")


class CustomIndicatorTests(unittest.TestCase):
    @staticmethod
    def _big_points(count: int) -> list[dict]:
        return [
            {"timestamp": f"2024-04-{(index % 28) + 1:02d}", "value": index,
             "ticker": f"TCK{index:05d}"}
            for index in range(count)
        ]

    def test_a_large_batch_uploads_instead_of_inlining(self) -> None:
        points = self._big_points(20_000)
        transport = UploadingTransport(
            [
                {"indicator": {"customIndicatorId": "ci-1", "pointCount": 0}},
                {
                    "ticket": {
                        "jobId": "job-1",
                        "uploadUrl": "https://storage.example/put",
                        "headers": {"Content-Type": "application/x-ndjson"},
                    }
                },
                {"operation": {"id": "job-1", "status": "queued"}},
                {
                    "operation": {
                        "id": "job-1",
                        "status": "completed",
                        "result": {"acceptedRows": len(points)},
                    }
                },
                {
                    "indicator": {
                        "customIndicatorId": "ci-1",
                        "pointCount": len(points),
                    }
                },
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)

        result = client.create_custom_indicator(
            {"name": "Big", "scope": "asset", "points": points},
            idempotency_key="big-v1",
        )

        # The create request carries no points; they went to storage directly.
        self.assertNotIn("points", transport.calls[0]["body"])
        self.assertEqual(
            [call["path"] for call in transport.calls],
            [
                "custom-indicators",
                "custom-indicators/ci-1/uploads",
                "custom-indicators/ci-1/uploads/job-1/complete",
                "custom-indicators/ci-1/uploads/job-1",
                "custom-indicators/ci-1",
            ],
        )
        ticket_body = transport.calls[1]["body"]
        self.assertEqual(ticket_body["format"], "jsonl")
        self.assertEqual(ticket_body["sizeBytes"], len(transport.uploads[0]["data"]))
        # The server namespaces claims by operation, so reusing the key is
        # safe and cannot overrun its length limit.
        self.assertEqual(transport.calls[1]["idempotency_key"], "big-v1")
        self.assertEqual(transport.uploads[0]["url"], "https://storage.example/put")
        self.assertEqual(
            transport.uploads[0]["content_type"],
            "application/x-ndjson",
        )
        self.assertEqual(
            transport.uploads[0]["data"].count(b"\n"),
            len(points),
        )
        self.assertEqual(result["pointCount"], len(points))
        self.assertEqual(result["upload"]["result"]["acceptedRows"], len(points))

    def test_a_retry_resumes_polling_instead_of_resending_the_batch(self) -> None:
        """An upload interrupted after its PUT must be resumable.

        The replayed ticket carries no uploadUrl because the bytes already
        landed; re-sending them (or failing outright) would strand a caller
        whose only fault was a poll timeout.
        """
        points = self._big_points(20_000)
        transport = UploadingTransport(
            [
                {"indicator": {"customIndicatorId": "ci-1", "pointCount": 0}},
                # Replayed ticket: job known, nothing left to upload.
                {"ticket": {"jobId": "job-1", "status": "validating"}},
                {
                    "operation": {
                        "id": "job-1",
                        "status": "completed",
                        "result": {"acceptedRows": len(points)},
                    }
                },
                {
                    "indicator": {
                        "customIndicatorId": "ci-1",
                        "pointCount": len(points),
                    }
                },
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)

        result = client.create_custom_indicator(
            {"name": "Big", "scope": "asset", "points": points},
            idempotency_key="big-v1",
        )

        self.assertEqual(transport.uploads, [])
        self.assertEqual(
            [call["path"] for call in transport.calls],
            [
                "custom-indicators",
                "custom-indicators/ci-1/uploads",
                # No /complete — the first attempt already started validation.
                "custom-indicators/ci-1/uploads/job-1",
                "custom-indicators/ci-1",
            ],
        )
        self.assertEqual(result["pointCount"], len(points))

    def test_a_failed_upload_raises_rather_than_reporting_success(self) -> None:
        transport = UploadingTransport(
            [
                {"indicator": {"customIndicatorId": "ci-1"}},
                {
                    "ticket": {
                        "jobId": "job-1",
                        "uploadUrl": "https://storage.example/put",
                        "headers": {"Content-Type": "application/x-ndjson"},
                    }
                },
                {"operation": {"id": "job-1", "status": "queued"}},
                {
                    "operation": {
                        "id": "job-1",
                        "status": "failed",
                        "error": {
                            "code": "custom_indicator_upload_failed",
                            "message": "row 4: Invalid datetime",
                        },
                    }
                },
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)

        with self.assertRaises(client_module.NexusTradeApiError) as raised:
            client.create_custom_indicator(
                {"name": "Big", "scope": "asset", "points": self._big_points(20_000)},
                idempotency_key="big-v1",
            )
        self.assertEqual(raised.exception.code, "custom_indicator_upload_failed")

    def test_a_large_batch_needs_an_upload_capable_transport(self) -> None:
        transport = FakeTransport([{"indicator": {"customIndicatorId": "ci-1"}}])
        client = client_module.NexusTradeClient(transport=transport)

        with self.assertRaises(client_module.NexusTradeApiError) as raised:
            client.create_custom_indicator(
                {"name": "Big", "points": self._big_points(20_000)},
                idempotency_key="big-v1",
            )
        self.assertEqual(raised.exception.code, "unsupported_transport")

    def test_datetime_points_are_serialized_as_iso_strings(self) -> None:
        transport = FakeTransport([{"indicator": {"customIndicatorId": "ci-1"}}])
        client = client_module.NexusTradeClient(transport=transport)

        client.create_custom_indicator(
            {
                "name": "Dated",
                "points": [{"timestamp": datetime.date(2024, 4, 1), "value": 3}],
            },
            idempotency_key="dated-v1",
        )
        self.assertEqual(
            transport.calls[0]["body"]["points"],
            [{"timestamp": "2024-04-01", "value": 3}],
        )

    def test_list_passes_the_archive_filter(self) -> None:
        transport = FakeTransport([{"indicators": [{"customIndicatorId": "ci-1"}]}])
        client = client_module.NexusTradeClient(transport=transport)

        indicators = client.list_custom_indicators(include_archived=True)

        self.assertEqual(
            transport.calls[0]["path"],
            "custom-indicators?includeArchived=true",
        )
        self.assertEqual(indicators, [{"customIndicatorId": "ci-1"}])

    def test_replace_archive_restore_use_public_lifecycle(self) -> None:
        transport = FakeTransport(
            [
                {"indicator": {"customIndicatorId": "ci-1", "dataVersion": 2}},
                {"archive": {"customIndicatorId": "ci-1", "forksPaused": 0}},
                {"indicator": {"customIndicatorId": "ci-1", "status": "active"}},
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)

        client.replace_custom_indicator_points(
            "ci-1",
            [{"timestamp": "2024-04-01", "value": 3}],
            idempotency_key="replace-v2",
            allow_shrink=True,
        )
        client.archive_custom_indicator("ci-1", confirm=True)
        client.restore_custom_indicator("ci-1")

        self.assertEqual(
            transport.calls,
            [
                {
                    "method": "PUT",
                    "path": "custom-indicators/ci-1/points",
                    "body": {
                        "points": [{"timestamp": "2024-04-01", "value": 3}],
                        "allowShrink": True,
                    },
                    "idempotency_key": "replace-v2",
                },
                {
                    "method": "DELETE",
                    "path": "custom-indicators/ci-1",
                    "body": {"confirm": True},
                    "idempotency_key": None,
                },
                {
                    "method": "POST",
                    "path": "custom-indicators/ci-1/restore",
                    "body": {},
                    "idempotency_key": None,
                },
            ],
        )

    def test_put_bytes_refuses_a_plaintext_upload_url(self) -> None:
        transport = client_module.HttpTransport("sk-temp", "https://api.example/v1")
        with self.assertRaises(client_module.NexusTradeApiError) as raised:
            transport.put_bytes("http://storage.example/put", b"x", content_type="text/csv")
        self.assertEqual(raised.exception.code, "unsafe_upload_url")

    def test_put_bytes_sends_no_credential(self) -> None:
        transport = client_module.HttpTransport("sk-secret", "https://api.example/v1")
        captured: list[urllib.request.Request] = []

        def fake_urlopen(request, timeout=None):  # noqa: ANN001 - test double
            captured.append(request)
            response = mock.MagicMock()
            response.status = 204
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            return response

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            transport.put_bytes(
                "https://storage.example/put",
                b"rows",
                content_type="application/x-ndjson",
            )

        self.assertEqual(captured[0].get_method(), "PUT")
        self.assertIsNone(captured[0].get_header("Authorization"))
        self.assertEqual(captured[0].data, b"rows")

class HttpTransportUploadTests(unittest.TestCase):
    def test_put_bytes_retries_transient_connection_reset(self) -> None:
        transport = client_module.HttpTransport("sk-temp", "https://api.example/v1")
        attempts = {"count": 0}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001 - test double
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise ConnectionResetError("[Errno 104] Connection reset by peer")
            response = mock.MagicMock()
            response.status = 204
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            return response

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            with mock.patch.object(client_module.time, "sleep"):
                transport.put_bytes(
                    "https://storage.example/put",
                    b"rows",
                    content_type="application/x-ndjson",
                )

        self.assertEqual(attempts["count"], 2)

    def test_put_bytes_does_not_retry_permanent_403(self) -> None:
        transport = client_module.HttpTransport("sk-temp", "https://api.example/v1")
        attempts = {"count": 0}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001 - test double
            attempts["count"] += 1
            raise urllib.error.HTTPError(
                "https://storage.example/put",
                403,
                "Forbidden",
                {},
                None,
            )

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            with mock.patch.object(client_module.time, "sleep"):
                with self.assertRaises(client_module.NexusTradeApiError) as raised:
                    transport.put_bytes(
                        "https://storage.example/put",
                        b"rows",
                        content_type="application/x-ndjson",
                    )

        self.assertEqual(attempts["count"], 1)
        self.assertEqual(raised.exception.code, "upload_failed")
        self.assertEqual(raised.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
