"""Typed NexusTrade JSON API client.

The client is transport-generic: normal callers pass an API key/base URL, while
run_compute receives short-lived values through NEXUSTRADE_API_* environment
variables. It has no sandbox filesystem or yielding behavior.
"""

from __future__ import annotations

import datetime
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

from nexustrade.env import LazyDotenv, environment_value

# sandbox-prune:begin agent-surface
if TYPE_CHECKING:  # pragma: no cover - typing only
    from nexustrade.agent import AgentRun
# sandbox-prune:end agent-surface
if TYPE_CHECKING:  # pragma: no cover - typing only
    from nexustrade.portfolio_handle import Portfolio, PortfolioList
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
_MAX_UPLOAD_PUT_ATTEMPTS = 5
_UPLOAD_PUT_INITIAL_BACKOFF_SECONDS = 0.5
_UPLOAD_PUT_MAX_BACKOFF_SECONDS = 8.0
_UPLOAD_PUT_JITTER_SECONDS = 0.25

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

# Connecting a brokerage is a human opening a browser. Long enough for that,
# short enough that a forgotten terminal does not hang all afternoon.
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 300
_CONNECT_POLL_INTERVAL_SECONDS = 3

# Point batches larger than this are uploaded rather than sent inline.
_MAX_INLINE_POINT_BYTES = 512 * 1024
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_POINT_FIELDS = {
    "timestamp": "timestamp",
    "value": "value",
    "ticker": "ticker",
    "assetType": "assetType",
    "asset_type": "assetType",
    "availableAt": "availableAt",
    "available_at": "availableAt",
}


class NexusTradeApiError(RuntimeError):
    """Stable error raised for non-2xx NexusTrade SDK responses."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        operation_id: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        # Set for operation errors (timeout / failure). A timed-out job is
        # still running, so the caller needs the id to resume waiting without
        # resubmitting — reading it out of the message is not an interface.
        self.operation_id = operation_id


def _is_retryable_upload_http_status(status: int) -> bool:
    return status in (408, 429) or status >= 500


def _is_retryable_upload_error(error: NexusTradeApiError) -> bool:
    if error.code == "transport_error":
        return True
    if error.code == "upload_failed" and error.status != _NO_HTTP_STATUS:
        return _is_retryable_upload_http_status(error.status)
    return False


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
    ``hasattr``.
    """

    def request_bytes(
        self,
        method: str,
        path: str,
        *,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int = _MAX_PART_BYTES,
    ) -> bytes: ...


@runtime_checkable
class UploadTransport(Protocol):
    """A transport that can PUT bytes to a presigned storage URL.

    The URL is absolute and the call carries no credential.
    """

    def put_bytes(
        self,
        url: str,
        data: bytes,
        *,
        content_type: str,
    ) -> None: ...


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

    def put_bytes(
        self,
        url: str,
        data: bytes,
        *,
        content_type: str,
    ) -> None:
        """PUT a payload to a presigned storage URL. Sends no credential."""
        delay = _UPLOAD_PUT_INITIAL_BACKOFF_SECONDS
        for attempt in range(_MAX_UPLOAD_PUT_ATTEMPTS):
            try:
                self._put_bytes_once(url, data, content_type=content_type)
                return
            except NexusTradeApiError as error:
                if (
                    attempt >= _MAX_UPLOAD_PUT_ATTEMPTS - 1
                    or not _is_retryable_upload_error(error)
                ):
                    raise
            delay = min(delay * 2, _UPLOAD_PUT_MAX_BACKOFF_SECONDS)
            time.sleep(delay + random.uniform(0, _UPLOAD_PUT_JITTER_SECONDS))

    def _put_bytes_once(
        self,
        url: str,
        data: bytes,
        *,
        content_type: str,
    ) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "unsafe_upload_url",
                "NexusTrade refused a non-HTTPS upload URL.",
            )
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": content_type},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                if response.status not in (200, 201, 204):
                    raise NexusTradeApiError(
                        response.status,
                        "upload_failed",
                        f"Storage rejected the upload with HTTP {response.status}.",
                    )
        except urllib.error.HTTPError as error:
            raise NexusTradeApiError(
                error.code,
                "upload_failed",
                f"Storage rejected the upload: {error.reason}.",
            ) from error
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


def _stdout_is_interactive() -> bool:
    """Whether a human is plausibly watching. Never raises on odd streams."""
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _point_timestamp(value: Any) -> Any:
    """Render date/datetime objects as ISO-8601; pass strings through."""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else value


def _custom_indicator_point(point: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one point to its wire shape.

    Accepts snake_case or camelCase field names and date/datetime objects.
    Raises on an unrecognized field rather than dropping it silently.
    """
    normalized: dict[str, Any] = {}
    for key, value in point.items():
        target = _POINT_FIELDS.get(key)
        if target is None:
            raise ValueError(
                f"Unknown custom indicator point field {key!r}. Points accept "
                "timestamp, value, ticker, asset_type, and available_at."
            )
        if value is None:
            continue
        normalized[target] = (
            _point_timestamp(value)
            if target in ("timestamp", "availableAt")
            else value
        )
    if "timestamp" not in normalized:
        raise ValueError("Every custom indicator point needs a timestamp.")
    if "value" not in normalized:
        raise ValueError("Every custom indicator point needs a value.")
    return normalized


def _custom_indicator_points(points: Any) -> list[dict[str, Any]]:
    if isinstance(points, Mapping) or not isinstance(points, Sequence):
        raise ValueError("points must be a sequence of point mappings.")
    return [_custom_indicator_point(point) for point in points]


_POINT_KINDS = {"observation", "period_aggregate", "disclosed"}
_AGGREGATE_PERIODS = {"1d", "1w", "1mo", "1q"}


def _date_only(value: Any) -> datetime.date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _utc_datetime(value: Any) -> datetime.datetime | None:
    day = _date_only(value)
    if day:
        return datetime.datetime.combine(
            day, datetime.time(), datetime.timezone.utc
        )
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def _utc_midnight(day: datetime.date) -> str:
    return f"{day.isoformat()}T00:00:00.000Z"


def _normalize_date_only_availability(value: str) -> str:
    day = _date_only(value)
    return _utc_midnight(day + datetime.timedelta(days=1)) if day else value


def _point_kind_points(
    points: list[dict[str, Any]],
    point_kind: Any,
    aggregate_period: Any = None,
) -> list[dict[str, Any]]:
    if point_kind is None:
        if aggregate_period is not None:
            raise ValueError("aggregate_period requires point_kind='period_aggregate'.")
        return points
    if point_kind not in _POINT_KINDS:
        raise ValueError(
            "point_kind must be observation, period_aggregate, or disclosed."
        )
    if point_kind == "period_aggregate":
        if aggregate_period not in _AGGREGATE_PERIODS:
            raise ValueError(
                "period_aggregate requires aggregate_period: 1d, 1w, 1mo, or 1q."
            )
    elif aggregate_period is not None:
        raise ValueError("aggregate_period is only valid for period_aggregate.")

    normalized: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        row = dict(point)
        timestamp = row["timestamp"]
        available_at = row.get("availableAt")
        event_day = _date_only(timestamp)
        available_day = _date_only(available_at)
        event_time = _utc_datetime(timestamp)
        available_time = _utc_datetime(available_at)
        if point_kind == "observation":
            if available_at is None:
                row["availableAt"] = (
                    _utc_midnight(event_day) if event_day else timestamp
                )
            elif event_day and available_day == event_day:
                row["availableAt"] = _utc_midnight(event_day)
            elif available_day:
                row["availableAt"] = _normalize_date_only_availability(available_at)
            if event_time and available_time and available_time.date() > event_time.date():
                raise ValueError(
                    f"Point {index + 1}: observation available_at is after the event date."
                )
        elif point_kind == "disclosed":
            if available_at is None:
                raise ValueError(
                    f"Point {index + 1}: disclosed point_kind requires available_at."
                )
            if available_day:
                row["availableAt"] = _normalize_date_only_availability(available_at)
        else:
            if not event_time:
                raise ValueError(
                    f"Point {index + 1}: aggregate timestamp must be date-only or include an explicit UTC offset."
                )
            if any(
                (
                    event_time.hour,
                    event_time.minute,
                    event_time.second,
                    event_time.microsecond,
                )
            ):
                raise ValueError(
                    f"Point {index + 1}: aggregate timestamp must be UTC midnight."
                )
            event_day = event_time.date()
            if aggregate_period == "1d":
                period_end = event_day + datetime.timedelta(days=1)
            elif aggregate_period == "1w":
                if event_day.weekday() != 0:
                    raise ValueError(
                        f"Point {index + 1}: 1w timestamp must be a Monday."
                    )
                period_end = event_day + datetime.timedelta(days=7)
            elif aggregate_period == "1mo":
                if event_day.day != 1:
                    raise ValueError(
                        f"Point {index + 1}: 1mo timestamp must be month start."
                    )
                period_end = datetime.date(
                    event_day.year + (1 if event_day.month == 12 else 0),
                    1 if event_day.month == 12 else event_day.month + 1,
                    1,
                )
            else:
                if event_day.day != 1 or event_day.month not in (1, 4, 7, 10):
                    raise ValueError(
                        f"Point {index + 1}: 1q timestamp must be quarter start."
                    )
                next_month = event_day.month + 3
                period_end = datetime.date(
                    event_day.year + (1 if next_month > 12 else 0),
                    next_month - 12 if next_month > 12 else next_month,
                    1,
                )
            derived = _utc_midnight(period_end)
            if available_at is None:
                row["availableAt"] = derived
            elif available_day:
                explicit = _normalize_date_only_availability(available_at)
                if explicit < derived:
                    raise ValueError(
                        f"Point {index + 1}: available_at precedes the aggregate close."
                    )
                row["availableAt"] = explicit
            elif available_time and available_time < datetime.datetime.combine(
                period_end, datetime.time(), datetime.timezone.utc
            ):
                raise ValueError(
                    f"Point {index + 1}: available_at precedes the aggregate close."
                )
        normalized.append(row)
    return normalized


def _points_jsonl(points: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        f"{json.dumps(dict(point), separators=(',', ':'))}\n" for point in points
    ).encode("utf-8")


def _inline_point_bytes(points: Sequence[Mapping[str, Any]]) -> int:
    return len(json.dumps(list(points), separators=(",", ":")).encode("utf-8"))


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
                "again with the same id (error.operation_id) rather than "
                "resubmitting.",
                operation_id,
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
    return NexusTradeApiError(_NO_HTTP_STATUS, code, message, operation_id)


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
        # Lazy and memoized: the tree is walked at most once, and not at all
        # when the environment already answers — which is always true inside
        # run_compute, where the platform injects both variables.
        dotenv = LazyDotenv()
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

    def list_portfolios(
        self,
        *,
        portfolio_ids: Sequence[str] | None = None,
        include_inactive: bool | None = None,
        include_paper: bool | None = None,
        include_live: bool | None = None,
        include_chat_portfolios: bool | None = None,
        include_positions: bool | None = None,
        search: str | None = None,
        limit: int | None = None,
        page: int | None = None,
    ) -> "PortfolioList":
        """List portfolios with optional filters and pagination.

        ``include_positions`` defaults off when ``search`` is set.
        """
        from nexustrade.portfolio_handle import Portfolio, PortfolioList

        params: list[tuple[str, str]] = []
        if portfolio_ids:
            params.append(("portfolioIds", ",".join(portfolio_ids)))
        for key, value in (
            ("includeInactive", include_inactive),
            ("includePaper", include_paper),
            ("includeLive", include_live),
            ("includeChatPortfolios", include_chat_portfolios),
            ("includePositions", include_positions),
            ("search", search),
            ("limit", limit),
            ("page", page),
        ):
            if value is not None:
                params.append(
                    (key, str(value).lower() if isinstance(value, bool) else str(value))
                )
        query = urllib.parse.urlencode(params)
        path = f"portfolios?{query}" if query else "portfolios"
        response = self._transport.request("GET", path)
        rows = response.get("portfolios")
        if not isinstance(rows, list):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Portfolio list response is missing portfolios.",
            )
        handles = [
            Portfolio(row, client=self) if isinstance(row, Mapping) else Portfolio(client=self)
            for row in rows
        ]
        return PortfolioList(
            {
                "portfolios": handles,
                "page": response.get("page", 1),
                "limit": response.get("limit", 20),
                "total": response.get("total", len(handles)),
                "totalPages": response.get("totalPages", 1),
                "scopes": response.get("scopes") or {},
            }
        )

    def get_portfolio(self, portfolio_id: str) -> "Portfolio":
        from nexustrade.portfolio_handle import Portfolio

        response = self._transport.request(
            "GET",
            f"portfolios/{urllib.parse.quote(portfolio_id, safe='')}",
        )
        result = response.get("portfolio")
        if not isinstance(result, dict):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Portfolio response is missing portfolio.",
            )
        return Portfolio(result, client=self)

    def deploy(
        self,
        portfolio_id: str,
        *,
        frequency: str | None = None,
    ) -> dict[str, Any]:
        """Mint/activate a paper portfolio from a chat draft (or re-activate)."""
        body: dict[str, Any] = {}
        if frequency is not None:
            body["frequency"] = frequency
        response = self._transport.request(
            "POST",
            f"portfolios/{urllib.parse.quote(portfolio_id, safe='')}/deploy",
            body=body,
        )
        result = response.get("deployment")
        if not isinstance(result, dict):
            result = response if isinstance(response, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("portfolioId"), str):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Deploy response is missing portfolioId.",
            )
        return result

    def undeploy(self, portfolio_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "POST",
            f"portfolios/{urllib.parse.quote(portfolio_id, safe='')}/undeploy",
            body={},
        )
        result = response.get("undeployment")
        if not isinstance(result, dict):
            result = response if isinstance(response, dict) else None
        if not isinstance(result, dict):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Undeploy response is missing body.",
            )
        return result

    def create_custom_indicator(
        self,
        indicator: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a custom data source and return it, including its id.

        Pass ``{"name": ..., "scope": ..., "description": ...,
        "point_kind": ..., "aggregate_period": ..., "points": [...]}``.
        ``scope`` is ``"global"`` (one series) or ``"asset"`` (one series per
        ticker, so every point needs a ``ticker``); it defaults to ``"global"``
        and cannot be changed later. ``points`` is optional and unlimited in
        size — small batches are sent with the request, larger ones are
        uploaded, and the returned indicator reflects what landed either way.
        ``point_kind`` applies one public availability contract before either
        path. Use ``observation`` for point-in-time samples,
        ``period_aggregate`` plus ``aggregate_period`` for closed periods, and
        ``disclosed`` when every row supplies its publication time.

        The id it returns is what a ``CustomIndicator`` node binds to::

            series = client.create_custom_indicator(
                {
                    "name": "WSB NVDA Mentions",
                    "scope": "asset",
                    "points": [
                        {"timestamp": "2024-04-01", "value": 152, "ticker": "NVDA"},
                    ],
                },
                idempotency_key="wsb-mentions-v1",
            )
            nt.CustomIndicator(
                nt.stock_asset("NVDA"), series["customIndicatorId"]
            )

        Retrying with the same ``idempotency_key`` returns the original series
        rather than creating a second one.
        """
        spec = dict(indicator)
        name = spec.pop("name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("create_custom_indicator requires a name.")
        raw_points = spec.pop("points", None)
        point_kind = spec.pop("point_kind", None)
        aggregate_period = spec.pop("aggregate_period", None)
        points = _point_kind_points(
            _custom_indicator_points(raw_points) if raw_points else [],
            point_kind,
            aggregate_period,
        )
        body: dict[str, Any] = {"name": name.strip()}
        if point_kind is not None:
            body["pointKind"] = point_kind
        if aggregate_period is not None:
            body["aggregatePeriod"] = aggregate_period
        for key in ("description", "scope"):
            value = spec.pop(key, None)
            if value is not None:
                body[key] = value
        if spec:
            raise ValueError(
                "Unknown custom indicator field(s): "
                f"{', '.join(sorted(spec))}. Expected name, description, "
                "scope, point_kind, aggregate_period, and points."
            )
        inline = points and _inline_point_bytes(points) <= _MAX_INLINE_POINT_BYTES
        if inline:
            body["points"] = points
        created = self._custom_indicator(
            self._transport.request(
                "POST",
                "custom-indicators",
                body=body,
                idempotency_key=idempotency_key,
            )
        )
        if inline or not points:
            return created
        custom_indicator_id = str(created["customIndicatorId"])
        # The same key is safe here: the server namespaces an idempotency
        # claim by operation, and deriving a suffixed key could overrun its
        # 160-character limit.
        upload = self._upload_custom_indicator_points(
            custom_indicator_id,
            points,
            idempotency_key=idempotency_key,
        )
        return {
            **self.get_custom_indicator(custom_indicator_id),
            "upload": upload,
        }

    def list_custom_indicators(
        self,
        *,
        include_archived: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List the custom data sources this account owns."""
        path = "custom-indicators"
        if include_archived is not None:
            path = f"{path}?includeArchived={str(include_archived).lower()}"
        response = self._transport.request("GET", path)
        indicators = response.get("indicators")
        if not isinstance(indicators, list):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Custom indicator list response is missing indicators.",
            )
        return [dict(row) for row in indicators if isinstance(row, Mapping)]

    def get_custom_indicator(self, custom_indicator_id: str) -> dict[str, Any]:
        """Read one custom data source, including its current point count."""
        return self._custom_indicator(
            self._transport.request(
                "GET",
                f"custom-indicators/{urllib.parse.quote(custom_indicator_id, safe='')}",
            )
        )

    def append_custom_indicator_points(
        self,
        custom_indicator_id: str,
        points: Sequence[Mapping[str, Any]],
        *,
        idempotency_key: str,
        point_kind: str | None = None,
        aggregate_period: str | None = None,
    ) -> dict[str, Any]:
        """Add points to an existing series and return the updated indicator.

        Recurring collection must append to the same ``custom_indicator_id``
        every run; creating a fresh series per run splits the history into
        fragments a strategy cannot read. Re-sending an identical batch is
        safe — the duplicate is not written twice.

        The batch is unlimited in size and, as with creation, is sent inline or
        uploaded depending on how large it is.
        """
        normalized = _point_kind_points(
            _custom_indicator_points(points), point_kind, aggregate_period
        )
        if not normalized:
            raise ValueError("append_custom_indicator_points needs at least one point.")
        quoted = urllib.parse.quote(custom_indicator_id, safe="")
        if _inline_point_bytes(normalized) <= _MAX_INLINE_POINT_BYTES:
            response = self._transport.request(
                "POST",
                f"custom-indicators/{quoted}/points",
                body={
                    "points": normalized,
                    **({"pointKind": point_kind} if point_kind else {}),
                    **(
                        {"aggregatePeriod": aggregate_period}
                        if aggregate_period
                        else {}
                    ),
                },
                idempotency_key=idempotency_key,
            )
            indicator = response.get("indicator")
            if not isinstance(indicator, dict):
                raise NexusTradeApiError(
                    _NO_HTTP_STATUS,
                    "invalid_response",
                    "Custom indicator response is missing indicator.",
                )
            return indicator
        upload = self._upload_custom_indicator_points(
            custom_indicator_id,
            normalized,
            idempotency_key=idempotency_key,
        )
        return {
            **self.get_custom_indicator(custom_indicator_id),
            "upload": upload,
        }

    def replace_custom_indicator_points(
        self,
        custom_indicator_id: str,
        points: Sequence[Mapping[str, Any]],
        *,
        idempotency_key: str,
        allow_shrink: bool = False,
        point_kind: str | None = None,
        aggregate_period: str | None = None,
    ) -> dict[str, Any]:
        """Replace the complete series while retaining the same indicator id."""
        normalized = _point_kind_points(
            _custom_indicator_points(points), point_kind, aggregate_period
        )
        if not normalized:
            raise ValueError("replace_custom_indicator_points needs at least one point.")
        quoted = urllib.parse.quote(custom_indicator_id, safe="")
        if _inline_point_bytes(normalized) <= _MAX_INLINE_POINT_BYTES:
            return self._custom_indicator(
                self._transport.request(
                "PUT",
                f"custom-indicators/{quoted}/points",
                body={
                    "points": normalized,
                    "allowShrink": allow_shrink,
                    **({"pointKind": point_kind} if point_kind else {}),
                    **(
                        {"aggregatePeriod": aggregate_period}
                        if aggregate_period
                        else {}
                    ),
                },
                idempotency_key=idempotency_key,
                )
            )
        upload = self._upload_custom_indicator_points(
            custom_indicator_id,
            normalized,
            idempotency_key=idempotency_key,
            mode="replace",
            allow_shrink=allow_shrink,
        )
        return {**self.get_custom_indicator(custom_indicator_id), "upload": upload}

    def archive_custom_indicator(
        self,
        custom_indicator_id: str,
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Soft-archive an indicator; confirm when active portfolios use it."""
        quoted = urllib.parse.quote(custom_indicator_id, safe="")
        response = self._transport.request(
            "DELETE",
            f"custom-indicators/{quoted}",
            body={"confirm": confirm},
        )
        archive = response.get("archive")
        if not isinstance(archive, dict):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Custom indicator archive response is missing archive details.",
            )
        return archive

    def restore_custom_indicator(
        self,
        custom_indicator_id: str,
    ) -> dict[str, Any]:
        """Restore a soft-archived indicator."""
        quoted = urllib.parse.quote(custom_indicator_id, safe="")
        return self._custom_indicator(
            self._transport.request(
                "POST",
                f"custom-indicators/{quoted}/restore",
                body={},
            )
        )

    def create_custom_indicator_upload(
        self,
        custom_indicator_id: str,
        *,
        file_name: str,
        idempotency_key: str,
        format: str = "jsonl",
        content_type: str | None = None,
        size_bytes: int | None = None,
        mode: str | None = None,
        allow_shrink: bool | None = None,
    ) -> dict[str, Any]:
        """Open an upload slot and return a presigned ``uploadUrl`` to PUT to.

        Accepts ``csv``, ``json``, or ``jsonl`` up to 100 MB. PUT the bytes to
        the returned URL, then call ``complete_custom_indicator_upload``. Most
        callers can pass ``points`` to ``create_custom_indicator`` or
        ``append_custom_indicator_points`` instead and skip all three steps.

        Retrying with the same ``idempotency_key`` re-signs the same job, since
        the first URL expires in 15 minutes. Once its bytes have arrived the
        reply carries no ``uploadUrl`` — there is nothing left to send, so skip
        the PUT and wait on ``jobId``.
        """
        body: dict[str, Any] = {"fileName": file_name, "format": format}
        if content_type is not None:
            body["contentType"] = content_type
        if size_bytes is not None:
            body["sizeBytes"] = size_bytes
        if mode is not None:
            body["mode"] = mode
        if allow_shrink is not None:
            body["allowShrink"] = allow_shrink
        response = self._transport.request(
            "POST",
            f"custom-indicators/{urllib.parse.quote(custom_indicator_id, safe='')}/uploads",
            body=body,
            idempotency_key=idempotency_key,
        )
        ticket = response.get("ticket")
        if not isinstance(ticket, dict) or not ticket.get("jobId"):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Upload response is missing ticket.",
            )
        return ticket

    def complete_custom_indicator_upload(
        self,
        custom_indicator_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        """Start validation of uploaded bytes and return the operation."""
        return self._operation(
            self._transport.request(
                "POST",
                f"custom-indicators/{urllib.parse.quote(custom_indicator_id, safe='')}"
                f"/uploads/{urllib.parse.quote(job_id, safe='')}/complete",
                body={},
            )
        )

    def get_custom_indicator_upload(
        self,
        custom_indicator_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        """Read an upload operation. ``phase`` distinguishes the live states."""
        return self._operation(
            self._transport.request(
                "GET",
                f"custom-indicators/{urllib.parse.quote(custom_indicator_id, safe='')}"
                f"/uploads/{urllib.parse.quote(job_id, safe='')}",
            )
        )

    def wait_for_custom_indicator_upload(
        self,
        custom_indicator_id: str,
        job_id: str,
        **options: Any,
    ) -> dict[str, Any]:
        """Block until an upload is validated. See ``wait_for_operation``."""
        return wait_for_operation(
            lambda pending_id: self.get_custom_indicator_upload(
                custom_indicator_id,
                pending_id,
            ),
            job_id,
            **options,
        )

    def _upload_custom_indicator_points(
        self,
        custom_indicator_id: str,
        points: Sequence[Mapping[str, Any]],
        *,
        idempotency_key: str,
        mode: str | None = None,
        allow_shrink: bool | None = None,
    ) -> dict[str, Any]:
        payload = _points_jsonl(points)
        if len(payload) > _MAX_UPLOAD_BYTES:
            raise ValueError(
                f"{len(points)} points serialize to "
                f"{len(payload) // (1024 * 1024)} MB, over the 100 MB upload "
                "limit. Send them in several batches."
            )
        transport = self._transport
        if not isinstance(transport, UploadTransport):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "unsupported_transport",
                "Uploading a large point batch requires HttpTransport.put_bytes.",
            )
        ticket = self.create_custom_indicator_upload(
            custom_indicator_id,
            file_name=f"{custom_indicator_id}-points.jsonl",
            format="jsonl",
            size_bytes=len(payload),
            idempotency_key=idempotency_key,
            mode=mode,
            allow_shrink=allow_shrink,
        )
        job_id = str(ticket["jobId"])
        # No upload URL means this batch already reached the server on an
        # earlier attempt, so resume at polling instead of re-sending it.
        if ticket.get("uploadUrl"):
            headers = ticket.get("headers")
            content_type = "application/x-ndjson"
            if isinstance(headers, Mapping):
                content_type = str(headers.get("Content-Type") or content_type)
            transport.put_bytes(
                str(ticket["uploadUrl"]),
                payload,
                content_type=content_type,
            )
            self.complete_custom_indicator_upload(custom_indicator_id, job_id)
        return self.wait_for_custom_indicator_upload(custom_indicator_id, job_id)

    @staticmethod
    def _custom_indicator(response: Mapping[str, Any]) -> dict[str, Any]:
        indicator = response.get("indicator")
        if not isinstance(indicator, dict) or not indicator.get("customIndicatorId"):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Custom indicator response is missing indicator.",
            )
        return indicator

    # sandbox-prune:begin trading-surface
    def list_brokerages(self) -> list[dict[str, Any]]:
        """Every connectable brokerage and whether this account has linked it.

        Reports all of them, connected or not, each with a ``connectUrl`` — an
        empty result would say nothing about what to do next.
        """
        response = self._transport.request("GET", "brokerages")
        brokerages = response.get("brokerages")
        if not isinstance(brokerages, list):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Brokerage response is missing brokerages.",
            )
        return [dict(row) for row in brokerages if isinstance(row, Mapping)]

    def get_brokerage(self, brokerage: str) -> dict[str, Any]:
        """Whether one named brokerage is connected."""
        response = self._transport.request(
            "GET",
            f"brokerages/{urllib.parse.quote(brokerage, safe='')}",
        )
        result = response.get("brokerage")
        if not isinstance(result, dict):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Brokerage response is missing brokerage.",
            )
        return result

    def connect_brokerage(
        self,
        brokerage: str,
        *,
        wait: bool | None = None,
        timeout_seconds: float = _DEFAULT_CONNECT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = _CONNECT_POLL_INTERVAL_SECONDS,
    ) -> dict[str, Any]:
        """Return the brokerage once connected, printing where to connect it.

        Linking a brokerage is an OAuth redirect, so an API key cannot complete
        it — a human has to open the URL. What this does is make that
        unmissable and then wait for it.

        ``wait`` defaults to whether stdout is a terminal. Interactively it
        prints the URL and polls until the link appears. Non-interactively — CI,
        cron, a piped script — it raises ``brokerage_not_connected`` at once
        rather than stalling for the timeout in front of nobody. Pass ``wait``
        explicitly to override either way.
        """
        current = self.get_brokerage(brokerage)
        if current.get("connected"):
            return current

        connect_url = str(current.get("connectUrl") or "")
        message = (
            f"{brokerage} is not connected. Connect it at {connect_url} — "
            "linking a brokerage is a browser flow an API key cannot complete."
        )
        should_wait = _stdout_is_interactive() if wait is None else bool(wait)
        if not should_wait:
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "brokerage_not_connected",
                message,
            )

        print(message, flush=True)
        print(f"Waiting for {brokerage} to be connected…", flush=True)
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NexusTradeApiError(
                    _NO_HTTP_STATUS,
                    "brokerage_not_connected",
                    f"{brokerage} was still not connected after "
                    f"{timeout_seconds:g}s. {message}",
                )
            time.sleep(min(poll_interval_seconds, remaining))
            current = self.get_brokerage(brokerage)
            if current.get("connected"):
                print(f"{brokerage} connected.", flush=True)
                return current

    def create_orders(
        self,
        portfolio_id: str,
        orders: Sequence[Mapping[str, Any]],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Stage orders against a portfolio.

        **Paper orders are accepted immediately. Live orders are staged for
        approval and are never sent to a broker by this call.** A live response
        carries ``requiresApproval: True`` and an ``approvalUrl``; nothing has
        traded until a human approves it there. No argument changes that.

        ```python
        result = client.create_orders(
            portfolio_id,
            [{"asset": {"name": "SPY", "type": "STOCK", "symbol": "SPY"},
              "side": "BUY", "quantity": 10, "orderType": "MARKET"}],
            idempotency_key="rebalance-2024-04-01",
        )
        if result["requiresApproval"]:
            print("approve at", result["approvalUrl"])
        ```
        """
        if not isinstance(orders, Sequence) or isinstance(orders, Mapping):
            raise ValueError("orders must be a sequence of order mappings.")
        if not orders:
            raise ValueError("create_orders needs at least one order.")
        return self._transport.request(
            "POST",
            "orders",
            body={
                "portfolioId": portfolio_id,
                "orders": [dict(order) for order in orders],
            },
            idempotency_key=idempotency_key,
        )

    # sandbox-prune:end trading-surface

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

    # sandbox-prune:begin agent-surface
    def create_agent(
        self,
        prompt: str,
        *,
        idempotency_key: str,
        max_iterations: int | None = None,
    ) -> "AgentRun":
        """Start an agent run and return an iterable handle.

        Unlike the other job kinds this is not fire-and-poll: iterate the run to
        receive its events, and answer it when it asks. See ``AgentRun``.
        """
        from nexustrade.agent import AgentRun  # local: breaks an import cycle

        body: dict[str, Any] = {"prompt": prompt}
        if max_iterations is not None:
            body["maxIterations"] = max_iterations
        response = self._transport.request(
            "POST",
            "agents",
            body=body,
            idempotency_key=idempotency_key,
        )
        agent = response.get("agent")
        if not isinstance(agent, dict) or not agent.get("id"):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Agent response is missing agent.",
            )
        return AgentRun(
            id=str(agent["id"]),
            _client=self,
            status=str(agent.get("status") or "initializing"),
        )

    def attach_agent(self, agent_id: str, *, cursor: str | None = None) -> "AgentRun":
        """Reattach to a run already in flight.

        The run lives server-side and bills whether or not anyone is listening,
        so a dropped connection must not orphan it. Omit ``cursor`` to replay
        from the beginning; events are durable, so replay is exact.
        """
        from nexustrade.agent import AgentRun  # local: breaks an import cycle

        agent = self.get_agent(agent_id)
        run = AgentRun(
            id=str(agent.get("id") or agent_id),
            _client=self,
            status=str(agent.get("status") or "initializing"),
            terminal=bool(agent.get("terminal")),
        )
        run._cursor = cursor
        return run

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "GET",
            f"agents/{urllib.parse.quote(agent_id, safe='')}",
        )
        agent = response.get("agent")
        if not isinstance(agent, dict):
            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Agent response is missing agent.",
            )
        return agent

    # sandbox-prune:end agent-surface
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

    def create_nl_screen(
        self,
        question: str,
        *,
        return_query: bool = True,
    ) -> dict[str, Any]:
        """Submit a natural-language stock screen. Returns immediately; poll it.

        ``return_query`` keeps the generated SQL, engine and catalog version on
        the result. It defaults on: the SQL is the audit trail, and without it
        the rows are a number nobody can re-derive. It is returned on failure
        regardless, because a rejected query is the most useful thing to read.
        """
        response = self._transport.request(
            "POST",
            "nl/screens",
            body={"question": question, "returnQuery": return_query},
        )
        return self._operation(response)

    def get_nl_screen(self, screen_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "GET",
            f"nl/screens/{urllib.parse.quote(screen_id, safe='')}",
        )
        return self._operation(response)

    def cancel_nl_screen(self, screen_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "POST",
            f"nl/screens/{urllib.parse.quote(screen_id, safe='')}/cancel",
        )
        return self._operation(response)

    def wait_for_nl_screen(self, screen_id: str, **options: Any) -> dict[str, Any]:
        """Block until a screen is terminal. See ``wait_for_operation``."""
        return wait_for_operation(self.get_nl_screen, screen_id, **options)

    def create_lake_ask(self, question: str) -> dict[str, Any]:
        """Submit a natural-language lake ask. Returns immediately; poll it."""
        response = self._transport.request(
            "POST",
            "lake/ask",
            body={"question": question},
        )
        return self._operation(response)

    def get_lake_ask(self, ask_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "GET",
            f"lake/ask/{urllib.parse.quote(ask_id, safe='')}",
        )
        return self._operation(response)

    def cancel_lake_ask(self, ask_id: str) -> dict[str, Any]:
        response = self._transport.request(
            "POST",
            f"lake/ask/{urllib.parse.quote(ask_id, safe='')}/cancel",
        )
        return self._operation(response)

    def wait_for_lake_ask(self, ask_id: str, **options: Any) -> dict[str, Any]:
        """Block until a lake ask is terminal. See ``wait_for_operation``."""
        return wait_for_operation(self.get_lake_ask, ask_id, **options)

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
            # Prefer portfolioId over an inline body when both are present.
            portfolio_id = item.get("portfolioId") or item.get("portfolio_id")
            if isinstance(portfolio_id, str) and portfolio_id:
                normalized = {
                    key: value
                    for key, value in dict(item).items()
                    if key not in ("portfolio", "portfolio_id")
                }
                normalized["portfolioId"] = portfolio_id
                return normalized
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


def create_custom_indicator(
    indicator: Mapping[str, Any],
    *,
    idempotency_key: str,
    client: NexusTradeClient | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for scripts that do not need a persistent client."""
    return (client or NexusTradeClient.from_environment()).create_custom_indicator(
        indicator,
        idempotency_key=idempotency_key,
    )


__all__ = [
    "HttpTransport",
    "NexusTradeApiError",
    "NexusTradeClient",
    "Transport",
    "UploadTransport",
    "create_custom_indicator",
    "create_portfolio",
    "wait_for_operation",
]
