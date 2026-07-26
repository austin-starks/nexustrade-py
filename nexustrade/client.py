"""Typed NexusTrade JSON API client.

The client is transport-generic: normal callers pass an API key/base URL, while
run_compute receives short-lived values through NEXUSTRADE_API_* environment
variables. It has no sandbox filesystem or yielding behavior.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from nexustrade.env import environment_value, load_dotenv_values

_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_ERROR_BYTES = 64 * 1024
# Cap on a single binary read (lake result parts).
_MAX_PART_BYTES = 64 * 1024 * 1024
# Keep in lockstep with MAX_REDIRECTS in sdk/typescript/src/client.ts —
# checkSdkClientParity.ts asserts the two numbers match.
_MAX_REDIRECTS = 5
# `status` for errors no HTTP status describes: the request never reached the
# API, or it returned 2xx with an envelope the client could not use. Reporting
# a literal 200 there would misattribute a 201 response.
_NO_HTTP_STATUS = 0

# Polling defaults. Every NexusTrade job — backtest, optimization,
# walk-forward, and any future operation kind — reports through the same
# envelope, so one poller serves all of them. Kept in lockstep with the
# TypeScript SDK by checkSdkClientParity.ts; the backoff is deterministic (no
# jitter) so both languages issue the identical request sequence.
_DEFAULT_POLL_TIMEOUT_SECONDS = 900
_DEFAULT_POLL_INTERVAL_SECONDS = 2
_MAX_POLL_INTERVAL_SECONDS = 15
_POLL_BACKOFF_FACTOR = 1.5
_TERMINAL_STATUSES = ("cancelled", "completed", "failed")


class NexusTradeApiError(RuntimeError):
    """Stable error raised for non-2xx NexusTrade SDK responses."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or default_port,
    )


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow API redirects only when the bearer credential stays same-origin.

    Mutations are never replayed. urllib's default would downgrade a redirected
    POST to a bodyless GET; the TypeScript SDK's manual loop would re-POST the
    body. Both are wrong for an API where a POST launches a paid job, and they
    are wrong in *different* ways — so neither SDK follows a redirect on a
    non-GET request.
    """

    max_redirections = _MAX_REDIRECTS

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Mapping[str, str],
        new_url: str,
    ) -> urllib.request.Request | None:
        if _origin(request.full_url) != _origin(new_url):
            raise NexusTradeApiError(
                code,
                "unsafe_redirect",
                "NexusTrade refused a cross-origin API redirect.",
            )
        if request.get_method() != "GET":
            raise NexusTradeApiError(
                code,
                "unsafe_redirect",
                "NexusTrade refused to follow a redirect on a "
                f"{request.get_method()} request.",
            )
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )


def _urlopen(
    request: urllib.request.Request,
    *,
    timeout: float,
) -> Any:
    return urllib.request.build_opener(_SameOriginRedirectHandler()).open(
        request,
        timeout=timeout,
    )


@runtime_checkable
class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class BinaryTransport(Transport, Protocol):
    """A transport that can also return raw bytes.

    Lake result parts are Parquet, so they do not fit ``Transport``, which is a
    JSON contract. Declaring the capability lets custom and test transports
    implement downloads against a typed interface instead of being probed with
    ``hasattr``. Mirrors ``BinaryTransport`` in the TypeScript SDK.
    """

    def request_bytes(
        self,
        method: str,
        path: str,
        *,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int = _MAX_PART_BYTES,
    ) -> bytes: ...


@dataclass(frozen=True)
class HttpTransport:
    api_key: str = field(repr=False)
    base_url: str
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.api_key, str)
            or not self.api_key
            or any(
                character.isspace() or ord(character) < 32
                for character in self.api_key
            )
        ):
            raise ValueError("NexusTrade api_key must be a non-empty token.")
        parsed = urllib.parse.urlsplit(self.base_url)
        if not parsed.scheme or not parsed.hostname:
            raise ValueError("NexusTrade base_url must be an absolute URL.")
        is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and is_loopback
        ):
            raise ValueError(
                "NexusTrade base_url must use HTTPS (HTTP is allowed only "
                "for loopback development)."
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("NexusTrade base_url must not contain credentials.")
        if parsed.query or parsed.fragment:
            raise ValueError(
                "NexusTrade base_url must not contain a query or fragment."
            )
        if not isinstance(self.timeout_seconds, (int, float)) or (
            self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive.")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/nexustrade/{path.lstrip('/')}"
        payload = (
            json.dumps(dict(body), separators=(",", ":")).encode("utf-8")
            if body is not None
            else None
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            url,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with _urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise NexusTradeApiError(
                        response.status,
                        "response_too_large",
                        "NexusTrade response exceeded the SDK size limit.",
                    )
                try:
                    decoded = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise NexusTradeApiError(
                        response.status,
                        "invalid_response",
                        "NexusTrade returned invalid JSON.",
                    ) from error
                if not isinstance(decoded, dict):
                    raise NexusTradeApiError(
                        response.status,
                        "invalid_response",
                        "NexusTrade returned a non-object JSON response.",
                    )
                return decoded
        except urllib.error.HTTPError as error:
            raw = error.read(_MAX_ERROR_BYTES)
            try:
                decoded = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded = {}
            error_body = decoded.get("error") if isinstance(decoded, dict) else None
            # Same fallback ladder as the TypeScript SDK: envelope message,
            # then the HTTP reason phrase, then the bare status.
            fallback = str(error.reason or "") or f"HTTP {error.code}"
            if isinstance(error_body, dict):
                code = str(error_body.get("code") or "api_error")
                message = str(error_body.get("message") or fallback)
            else:
                code = "api_error"
                message = fallback
            raise NexusTradeApiError(error.code, code, message) from error
        except urllib.error.URLError as error:
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "transport_error",
                str(error.reason),
            ) from error
        except (TimeoutError, OSError) as error:
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "transport_error",
                str(error),
            ) from error

    def request_bytes(
        self,
        method: str,
        path: str,
        *,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int = _MAX_PART_BYTES,
    ) -> bytes:
        """Bounded binary GET/POST for non-JSON SDK payloads (lake Parquet parts)."""
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive.")
        url = f"{self.base_url.rstrip('/')}/nexustrade/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/vnd.apache.parquet,application/octet-stream",
        }
        if byte_range is not None:
            start, end = byte_range
            headers["Range"] = f"bytes={start}-{end}"
            max_bytes = min(max_bytes, end - start + 1)
        request = urllib.request.Request(url, headers=headers, method=method)
        try:
            with _urlopen(request, timeout=self.timeout_seconds) as response:
                # A server that ignores Range answers 200 with the WHOLE object.
                # Appending that at a non-zero resume offset silently corrupts
                # the file, so refuse it rather than trusting the status line.
                if byte_range is not None and byte_range[0] > 0:
                    if response.status != 206:
                        raise NexusTradeApiError(
                            response.status,
                            "range_not_honored",
                            "NexusTrade returned a full response to a ranged "
                            "request; refusing to resume against it.",
                        )
                return response.read(max_bytes)
        except urllib.error.HTTPError as error:
            raw = error.read(_MAX_ERROR_BYTES)
            try:
                decoded = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded = {}
            error_body = decoded.get("error") if isinstance(decoded, dict) else None
            if isinstance(error_body, dict):
                code = str(error_body.get("code") or "api_error")
                message = str(error_body.get("message") or error.reason)
            else:
                code = "api_error"
                message = str(error.reason)
            raise NexusTradeApiError(error.code, code, message) from error
        except urllib.error.URLError as error:
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "transport_error",
                str(error.reason),
            ) from error
        except (TimeoutError, OSError) as error:
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "transport_error",
                str(error),
            ) from error


def wait_for_operation(
    fetch: Callable[[str], Mapping[str, Any]],
    operation_id: str,
    *,
    timeout_seconds: float = _DEFAULT_POLL_TIMEOUT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    max_poll_interval_seconds: float = _MAX_POLL_INTERVAL_SECONDS,
    raise_on_failure: bool = True,
) -> dict[str, Any]:
    """Poll ``fetch(operation_id)`` until the operation reaches a terminal state.

    Works for any operation kind because every NexusTrade job reports the same
    ``{id, kind, status, result?, error?}`` envelope. Pass any getter with that
    shape — ``client.get_backtest``, ``get_optimization``, ``get_walk_forward``.

    Returns the terminal operation. Raises ``NexusTradeApiError`` on timeout,
    and on a failed/cancelled operation unless ``raise_on_failure`` is False.
    Transport errors from ``fetch`` propagate — a poller that swallowed them
    would report an infrastructure outage as a still-running job.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    if poll_interval_seconds < 0 or max_poll_interval_seconds < 0:
        raise ValueError("poll intervals must not be negative.")

    deadline = time.monotonic() + timeout_seconds
    interval = min(poll_interval_seconds, max_poll_interval_seconds)
    while True:
        operation = dict(fetch(operation_id))
        status = str(operation.get("status") or "")
        if status in _TERMINAL_STATUSES:
            if raise_on_failure and status != "completed":
                raise _operation_failure(operation, operation_id, status)
            return operation

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "operation_timeout",
                f"Operation {operation_id} was still '{status or 'unknown'}' "
                f"after {timeout_seconds:g}s. It is still running — poll "
                "again with the same id rather than resubmitting.",
            )
        if interval > 0:
            time.sleep(min(interval, remaining))
        interval = min(interval * _POLL_BACKOFF_FACTOR, max_poll_interval_seconds)


def _operation_failure(
    operation: Mapping[str, Any],
    operation_id: str,
    status: str,
) -> NexusTradeApiError:
    error = operation.get("error")
    code = "operation_cancelled" if status == "cancelled" else "operation_failed"
    message = f"Operation {operation_id} {status}."
    if isinstance(error, Mapping):
        code = str(error.get("code") or code)
        message = str(error.get("message") or message)
    return NexusTradeApiError(_NO_HTTP_STATUS, code, message)


class NexusTradeClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        if transport is not None:
            self._transport = transport
            return
        # Read the file once, so a two-variable lookup does not walk the tree
        # twice and cannot see two different files mid-resolution.
        dotenv = load_dotenv_values()
        resolved_key = api_key or environment_value("NEXUSTRADE_API_KEY", dotenv)
        resolved_url = base_url or environment_value(
            "NEXUSTRADE_API_BASE_URL",
            dotenv,
        )
        if not resolved_key or not resolved_url:
            raise ValueError(
                "NexusTradeClient requires an API key. Create one at "
                "https://nexustrade.io/developers, then either pass "
                "api_key=... and base_url=... or set NEXUSTRADE_API_KEY and "
                "NEXUSTRADE_API_BASE_URL "
                "(base URL is https://nexustrade.io/api/v1). "
                "Both are also read from a .env file at or above the current "
                "directory; the real environment takes precedence. "
                "OAuth tokens are not accepted by this API."
            )
        self._transport = HttpTransport(resolved_key, resolved_url)

    @classmethod
    def from_environment(cls) -> "NexusTradeClient":
        return cls()

    def create_portfolio(
        self,
        portfolio: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        response = self._transport.request(
            "POST",
            "portfolios",
            body=portfolio,
            idempotency_key=idempotency_key,
        )
        result = response.get("portfolio")
        if not isinstance(result, dict):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Portfolio response is missing portfolio.",
            )
        return result

    def create_backtests(
        self,
        backtests: list[Mapping[str, Any]],
        *,
        idempotency_key: str,
    ) -> list[dict[str, Any]]:
        inputs = [self._backtest_input(item) for item in backtests]
        response = self._transport.request(
            "POST",
            "backtests/batch",
            body={"backtests": inputs},
            idempotency_key=idempotency_key,
        )
        operations = response.get("operations")
        if not isinstance(operations, list) or not all(
            isinstance(operation, dict) for operation in operations
        ):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Backtest response is missing operations.",
            )
        return operations

    def create_backtest(
        self,
        backtest: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Submit one generated ``backtest(...)`` handle or raw API input."""
        operations = self.create_backtests(
            [backtest],
            idempotency_key=idempotency_key,
        )
        if len(operations) != 1:
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Single backtest response returned the wrong operation count.",
            )
        return operations[0]

    def get_backtest(self, backtest_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "GET",
            f"backtests/{urllib.parse.quote(backtest_id, safe='')}",
        )
        return self._operation(response)

    def create_optimization(
        self,
        handle: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._create_portfolio_job(
            "optimizations",
            handle,
            idempotency_key,
        )

    def get_optimization(self, optimization_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "GET",
            f"optimizations/{urllib.parse.quote(optimization_id, safe='')}",
        )
        return self._operation(response)

    def create_walk_forward(
        self,
        handle: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._create_portfolio_job(
            "walk-forward-studies",
            handle,
            idempotency_key,
        )

    def get_walk_forward(self, study_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "GET",
            f"walk-forward-studies/{urllib.parse.quote(study_id, safe='')}",
        )
        return self._operation(response)

    def create_lake_query(
        self,
        request: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        response = self._transport.request(
            "POST",
            "lake/queries",
            body=request,
            idempotency_key=idempotency_key,
        )
        return self._operation(response)

    def get_lake_query(self, query_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "GET",
            f"lake/queries/{urllib.parse.quote(query_id, safe='')}",
        )
        return self._operation(response)

    def cancel_lake_query(self, query_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "POST",
            f"lake/queries/{urllib.parse.quote(query_id, safe='')}/cancel",
        )
        return self._operation(response)

    def get_lake_catalog(self) -> list[dict[str, Any]]:
        response = self._transport.request("GET", "lake/catalog")
        tables = response.get("tables")
        if not isinstance(tables, list) or not all(
            isinstance(item, dict) for item in tables
        ):
            raise NexusTradeApiError(
                200,
                "invalid_response",
                "Lake catalog response is missing tables.",
            )
        return tables

    def describe_lake_table(self, table: str) -> dict[str, Any]:
        name = table[5:] if table.startswith("lake.") else table
        response = self._transport.request(
            "GET",
            f"lake/catalog/lake/{urllib.parse.quote(name, safe='')}",
        )
        result = response.get("table")
        if not isinstance(result, dict):
            raise NexusTradeApiError(
                200,
                "invalid_response",
                "Lake describe response is missing table.",
            )
        return result

    def get_lake_query_manifest(self, query_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "GET",
            f"lake/queries/{urllib.parse.quote(query_id, safe='')}/manifest",
        )
        return self._operation(response)

    def download_lake_query_part(
        self,
        query_id: str,
        part: int,
        *,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int = _MAX_PART_BYTES,
    ) -> bytes:
        """Download one Parquet part (optionally via HTTP Range). Bound every read."""
        transport = self._transport
        if not isinstance(transport, BinaryTransport):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "unsupported_transport",
                "Lake part download requires HttpTransport.request_bytes.",
            )
        path = (
            f"lake/queries/{urllib.parse.quote(query_id, safe='')}"
            f"/parts/{int(part)}"
        )
        return transport.request_bytes(
            "GET",
            path,
            byte_range=byte_range,
            max_bytes=max_bytes,
        )

    def wait_for_lake_query(
        self,
        query_id: str,
        **options: Any,
    ) -> dict[str, Any]:
        """Block until a lake query is terminal. See ``wait_for_operation``."""
        return wait_for_operation(self.get_lake_query, query_id, **options)

    def wait_for_backtest(self, backtest_id: str, **options: Any) -> dict[str, Any]:
        """Block until a backtest is terminal. See ``wait_for_operation``."""
        return wait_for_operation(self.get_backtest, backtest_id, **options)

    def wait_for_optimization(
        self,
        optimization_id: str,
        **options: Any,
    ) -> dict[str, Any]:
        """Block until an optimization is terminal. See ``wait_for_operation``."""
        return wait_for_operation(self.get_optimization, optimization_id, **options)

    def wait_for_walk_forward(
        self,
        study_id: str,
        **options: Any,
    ) -> dict[str, Any]:
        """Block until a walk-forward study is terminal. See ``wait_for_operation``."""
        return wait_for_operation(self.get_walk_forward, study_id, **options)

    def wait_for_backtests(
        self,
        operations: Sequence[Mapping[str, Any]],
        **options: Any,
    ) -> list[dict[str, Any]]:
        """Wait on a whole ``create_backtests`` batch, in submission order."""
        return [
            self.wait_for_backtest(str(operation["id"]), **options)
            for operation in operations
        ]

    def _create_portfolio_job(
        self,
        path: str,
        handle: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        response = self._transport.request(
            "POST",
            path,
            body={
                "portfolio": handle.get("portfolio"),
                "args": handle.get("args") or {},
            },
            idempotency_key=idempotency_key,
        )
        return self._operation(response)

    @staticmethod
    def _backtest_input(item: Mapping[str, Any]) -> dict[str, Any]:
        tool = item.get("tool")
        if tool is None:
            return dict(item)
        if tool != "backtest_portfolio":
            raise ValueError(
                "create_backtests accepts backtest(...) handles or raw API inputs."
            )
        portfolio = item.get("portfolio")
        args = item.get("args")
        if not isinstance(portfolio, Mapping) or not isinstance(args, Mapping):
            raise ValueError("backtest(...) handle is missing portfolio or args.")
        mapping = {
            "start_date": "startDate",
            "end_date": "endDate",
            "baseline_symbol": "baseline",
            "interval": "interval",
            "initial_value": "initialValue",
            "generate_events": "generateEvents",
            "fee_config": "feeConfig",
        }
        normalized: dict[str, Any] = {"portfolio": dict(portfolio)}
        for source, target in mapping.items():
            value = args.get(source)
            if value is not None:
                normalized[target] = value
        return normalized

    @staticmethod
    def _operation(response: Mapping[str, Any]) -> dict[str, Any]:
        operation = response.get("operation")
        if not isinstance(operation, dict):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Response is missing operation.",
            )
        return operation


def create_portfolio(
    portfolio: Mapping[str, Any],
    *,
    idempotency_key: str,
    client: NexusTradeClient | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for scripts that do not need a persistent client."""
    return (client or NexusTradeClient.from_environment()).create_portfolio(
        portfolio,
        idempotency_key=idempotency_key,
    )


__all__ = [
    "HttpTransport",
    "NexusTradeApiError",
    "NexusTradeClient",
    "Transport",
    "create_portfolio",
    "wait_for_operation",
]
