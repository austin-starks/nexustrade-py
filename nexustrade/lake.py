"""First-class lake SQL API: ``import nexustrade as nt`` then ``nt.lake.sql(...)``.

HTTP submit/get/catalog live on ``NexusTradeClient``. This module is the
ergonomic layer plus local analysis helpers (``duckdb_relation``,
``iter_batches``, ``to_pandas``), which require ``nexustrade-sdk[lake]``.

Results are durable Parquet parts — never an implicit pandas DataFrame.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Union

from nexustrade.client import (
    NexusTradeApiError,
    NexusTradeClient,
    _NO_HTTP_STATUS,
    wait_for_operation,
)

LakeBindValue = Union[
    str, int, float, bool, None, list[str], list[int], list[float], list[bool]
]

_MAX_PART_READ = 64 * 1024 * 1024

# Bound `to_pandas` by default rather than requiring the caller to name one.
# A required keyword made the obvious call — `res.to_pandas()` — a TypeError,
# which is a worse outcome than a generous ceiling: the guard exists to stop a
# runaway result from OOMing the sandbox, and it does that just as well with a
# default. Callers who want a tighter budget still pass `max_bytes`.
DEFAULT_TO_PANDAS_MAX_BYTES = 512 * 1024 * 1024


class LakeResultLimitError(RuntimeError):
    """Raised when ``to_pandas`` would exceed the bound in force."""


class LakeQueryFailed(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class LakeSubmitFailed(NexusTradeApiError):
    """Submission failed without telling us whether the server accepted it.

    Deliberately NOT a ``LakeQueryFailed``. This whole mechanism exists to stop
    blind resubmits from double-billing, and a broad ``except LakeQueryFailed``
    retry that re-calls ``sql()`` without a key would do exactly that. Keeping
    it under ``NexusTradeApiError`` also preserves the behaviour callers already
    had, since submit errors were ``NexusTradeApiError`` before this existed.

    A transport error leaves the outcome unknown: the query may be queued and
    billing, or it may never have arrived. Retrying with a NEW idempotency key
    would launch a second paid query, so the key this attempt used is attached
    here — retry with it and the server returns the original operation instead
    of starting another.

        try:
            result = nt.lake.sql(sql, params)
        except nt.lake.LakeSubmitFailed as error:
            result = nt.lake.sql(sql, params, idempotency_key=error.idempotency_key)
    """

    def __init__(
        self, status: int, code: str, message: str, idempotency_key: str
    ) -> None:
        super().__init__(
            status,
            code,
            f"{message} Retry with idempotency_key={idempotency_key!r} so the "
            "server returns the original query rather than starting a new one.",
        )
        self.idempotency_key = idempotency_key


@dataclass(frozen=True)
class LakeTable:
    schema: str
    name: str
    grain: str
    tigris_fallback_supported: bool
    notes: str | None = None


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file incrementally.

    Result parts are allowed up to gigabytes; reading one into memory purely to
    checksum it defeats the whole bounded-memory contract and can OOM a 4 GB
    sandbox on its own.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


#: DuckDB types a manifest may name. Anything else is refused rather than
#: interpolated: `type` comes off the wire and lands in SQL.
_SIMPLE_SQL_TYPES = frozenset(
    {
        "BOOLEAN", "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
        "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
        "FLOAT", "DOUBLE", "REAL",
        "VARCHAR", "TEXT", "STRING", "BLOB", "UUID", "JSON",
        "DATE", "TIME", "TIMESTAMP", "TIMESTAMPTZ", "INTERVAL",
        "TIMESTAMP WITH TIME ZONE", "TIME WITH TIME ZONE",
    }
)

_SQL_TYPE_PATTERN = re.compile(
    r"""^
    (?:
        DECIMAL\s*\(\s*\d{1,3}\s*,\s*\d{1,3}\s*\)   # DECIMAL(p,s)
      | NUMERIC\s*\(\s*\d{1,3}\s*,\s*\d{1,3}\s*\)
      | VARCHAR\s*\(\s*\d{1,6}\s*\)
    )$""",
    re.VERBOSE | re.IGNORECASE,
)


def _safe_sql_type(raw: Any) -> str:
    """Validate a manifest column type before it reaches SQL.

    Column NAMES were quote-escaped but types were interpolated verbatim, so a
    malformed or hostile manifest could inject SQL through `type`. Arrays and
    nested types are allowed one level deep by recursing on the element type.
    """
    text = str(raw or "VARCHAR").strip()
    upper = text.upper()
    if upper in _SIMPLE_SQL_TYPES:
        return upper
    if _SQL_TYPE_PATTERN.match(text):
        return upper
    if upper.endswith("[]"):
        return f"{_safe_sql_type(text[:-2])}[]"
    # Unrecognized: fall back rather than interpolate something unvetted.
    return "VARCHAR"


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Write via a temp file, flush, fsync, then replace.

    The download manifest was written in place, so an interruption could leave
    it truncated — and a corrupt manifest breaks resume for every part.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _validated_parts(result: Mapping[str, Any]) -> list[tuple[int, str, int]]:
    """Parse and validate the manifest's parts.

    Previously indexed straight into each entry, so a malformed response raised
    a bare KeyError/ValueError, and duplicate or non-contiguous part numbers
    were accepted — which silently downloads the wrong set of files.
    """
    raw = result.get("parts")
    if not isinstance(raw, list):
        raise LakeQueryFailed(
            "invalid_response", "Lake manifest parts is not a list."
        )
    if not raw:
        # A query that matched no rows. Valid, and the helpers below build an
        # empty relation from the manifest schema rather than failing.
        return []

    parsed: list[tuple[int, str, int]] = []
    seen: set[int] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise LakeQueryFailed(
                "invalid_response", "Lake manifest part is not an object."
            )
        try:
            index = int(entry["part"])
            digest = str(entry["sha256"])
            size = int(entry["byteSize"])
        except (KeyError, TypeError, ValueError) as error:
            raise LakeQueryFailed(
                "invalid_response",
                f"Lake manifest part is malformed: {error}",
            ) from error
        if index < 0 or size < 0:
            raise LakeQueryFailed(
                "invalid_response",
                f"Lake manifest part {index} has a negative index or size.",
            )
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise LakeQueryFailed(
                "invalid_response",
                f"Lake manifest part {index} has a malformed sha256.",
            )
        if index in seen:
            raise LakeQueryFailed(
                "invalid_response", f"Lake manifest repeats part {index}."
            )
        seen.add(index)
        parsed.append((index, digest, size))

    parsed.sort(key=lambda item: item[0])
    if [item[0] for item in parsed] != list(range(len(parsed))):
        raise LakeQueryFailed(
            "invalid_response",
            "Lake manifest part numbers are not contiguous from 0; "
            "downloading it would silently fetch the wrong set of files.",
        )
    return parsed


def _client(client: NexusTradeClient | None) -> NexusTradeClient:
    return client or NexusTradeClient.from_environment()


def _from_operation(
    operation: Mapping[str, Any],
    client: NexusTradeClient,
) -> "LakeQueryHandle | LakeQueryResult":
    status = str(operation.get("status") or "")
    query_id = str(operation.get("id") or "")
    error = operation.get("error")
    error_dict = error if isinstance(error, dict) else None
    if status == "completed" and isinstance(operation.get("result"), dict):
        return LakeQueryResult(
            id=query_id,
            status=status,
            result=operation["result"],
            _client=client,
        )
    return LakeQueryHandle(
        id=query_id,
        status=status,
        _client=client,
        error=error_dict,
    )


@dataclass
class LakeQueryHandle:
    """A submitted query. `idempotency_key` is the key this submission used —
    reuse it to re-request the same operation rather than start a new one."""

    id: str
    status: str
    _client: NexusTradeClient
    error: dict[str, Any] | None = None
    idempotency_key: str | None = None

    def refresh(self) -> "LakeQueryHandle | LakeQueryResult":
        return get(self.id, client=self._client)

    def wait(
        self,
        *,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        max_poll_interval_seconds: float | None = None,
    ) -> "LakeQueryResult":
        options: dict[str, Any] = {}
        if timeout_seconds is not None:
            options["timeout_seconds"] = timeout_seconds
        if poll_interval_seconds is not None:
            options["poll_interval_seconds"] = poll_interval_seconds
        if max_poll_interval_seconds is not None:
            options["max_poll_interval_seconds"] = max_poll_interval_seconds
        try:
            operation = wait_for_operation(
                self._client.get_lake_query,
                self.id,
                **options,
            )
        except NexusTradeApiError as error:
            raise LakeQueryFailed(error.code, error.message) from error
        result = _from_operation(operation, self._client)
        if isinstance(result, LakeQueryResult):
            return result
        raise LakeQueryFailed(
            "invalid_response",
            f"Lake query {self.id} completed without a result payload",
        )


@dataclass
class LakeQueryResult:
    id: str
    status: str
    result: dict[str, Any]
    _client: NexusTradeClient

    @property
    def row_count(self) -> int:
        return int(self.result.get("rowCount") or 0)

    @property
    def byte_size(self) -> int:
        return int(self.result.get("byteSize") or 0)

    def download(
        self,
        directory: str | Path | None = None,
        *,
        resume: bool = True,
    ) -> Path:
        """Download Parquet parts under ``directory / <query_id>``.

        ``directory`` defaults to ``./lake-results`` (cwd-relative). Pass an
        explicit path in the sandbox if you want ``/work/...``.
        """
        root = Path(directory if directory is not None else "lake-results") / self.id
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = root / "download_manifest.json"
        completed: dict[str, Any] = {}
        if resume and manifest_path.exists():
            try:
                completed = json.loads(manifest_path.read_text())
            except (OSError, json.JSONDecodeError):
                completed = {}

        validated = _validated_parts(self.result)
        if not validated:
            # Nothing to fetch, but callers expect the directory to exist.
            return root
        for index, expected_sha, expected_size in validated:
            final_name = f"part-{index:05d}.parquet"
            final_path = root / final_name
            entry = completed.get(final_name)
            if (
                resume
                and isinstance(entry, Mapping)
                and entry.get("sha256") == expected_sha
                and final_path.exists()
                and final_path.stat().st_size == expected_size
                # Rehash before trusting it. Size plus a manifest entry accepts
                # same-size local corruption, and the manifest is just a file we
                # wrote — it is not evidence about the bytes on disk.
                and _sha256_file(final_path) == expected_sha
            ):
                continue
            self._download_part(
                index,
                final_path,
                expected_sha=expected_sha,
                expected_size=expected_size,
                resume=resume,
            )
            completed[final_name] = {
                "sha256": expected_sha,
                "byteSize": expected_size,
            }
            _write_json_atomic(manifest_path, completed)
        return root

    def _download_part(
        self,
        part: int,
        final_path: Path,
        *,
        expected_sha: str,
        expected_size: int,
        resume: bool,
    ) -> None:
        tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
        start = 0
        if resume and tmp_path.exists():
            start = tmp_path.stat().st_size
            if start > expected_size:
                tmp_path.unlink(missing_ok=True)
                start = 0

        mode = "ab" if start > 0 else "wb"
        with tmp_path.open(mode) as handle:
            offset = start
            while offset < expected_size:
                end = min(offset + _MAX_PART_READ, expected_size) - 1
                chunk = self._client.download_lake_query_part(
                    self.id,
                    part,
                    byte_range=(offset, end),
                )
                if not chunk:
                    break
                handle.write(chunk)
                offset += len(chunk)

        digest = _sha256_file(tmp_path)
        if digest != expected_sha or tmp_path.stat().st_size != expected_size:
            tmp_path.unlink(missing_ok=True)
            raise LakeQueryFailed(
                "checksum_mismatch",
                f"Part {part} failed checksum or size validation",
            )
        os.replace(tmp_path, final_path)

    def iter_batches(self, batch_size: int = 65_536) -> Iterator[Any]:
        import pyarrow.parquet as pq

        directory = self.download()
        files = sorted(directory.glob("part-*.parquet"))
        for file_path in files:
            pf = pq.ParquetFile(file_path)
            for batch in pf.iter_batches(batch_size=batch_size):
                yield batch

    def duckdb_relation(self, connection: Any | None = None) -> Any:
        """Lazy relation over the downloaded parts.

        A query matching no rows produces zero parts, and globbing
        `part-*.parquet` over an empty directory fails. The manifest still
        carries the schema, so an empty result becomes a typed zero-row
        relation — projections and joins against it still bind.
        """
        import duckdb

        con = connection or duckdb.connect()
        directory = self.download()
        files = sorted(directory.glob("part-*.parquet"))
        if not files:
            return con.sql(self._empty_relation_sql())
        return con.from_parquet(str(directory / "part-*.parquet"))

    def _empty_relation_sql(self) -> str:
        columns = self.result.get("schema")
        if not isinstance(columns, list) or not columns:
            # No schema to honour; a bare empty relation is still better than
            # a glob error, but callers cannot project against it.
            return "SELECT 1 WHERE FALSE"
        selected: list[str] = []
        for column in columns:
            if not isinstance(column, Mapping):
                continue
            name = str(column.get("name", "")).replace('"', '""')
            sql_type = _safe_sql_type(column.get("type"))
            if not name:
                continue
            selected.append(f'CAST(NULL AS {sql_type}) AS "{name}"')
        if not selected:
            return "SELECT 1 WHERE FALSE"
        return f"SELECT {', '.join(selected)} WHERE FALSE"

    def to_pandas(
        self,
        *,
        max_bytes: int = DEFAULT_TO_PANDAS_MAX_BYTES,
        max_rows: int | None = None,
    ) -> Any:
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive int")
        if max_rows is not None and self.row_count > max_rows:
            raise LakeResultLimitError(
                f"Result has {self.row_count} rows; max_rows={max_rows}"
            )

        # Compressed size is a lower bound on the in-memory size, so if even it
        # exceeds the budget the answer is no — and this needs no download and
        # no pyarrow.
        if self.byte_size > max_bytes:
            raise LakeResultLimitError(
                f"Result is {self.byte_size} compressed bytes; max_bytes={max_bytes}. "
                "Use iter_batches() or duckdb_relation() instead."
            )

        # Then bound the size of the DataFrame itself. `byte_size` is
        # ZSTD-compressed Parquet; a highly compressible result (repeated
        # tickers, sparse columns) expands many times over in memory, so the
        # compressed figure alone lets exactly the dangerous cases through.
        # Parquet records uncompressed size per column chunk, so ask it.
        import pyarrow.parquet as pq

        directory = self.download()
        uncompressed = 0
        for file_path in sorted(directory.glob("part-*.parquet")):
            metadata = pq.ParquetFile(file_path).metadata
            for group in range(metadata.num_row_groups):
                row_group = metadata.row_group(group)
                for column in range(row_group.num_columns):
                    uncompressed += row_group.column(column).total_uncompressed_size
        if uncompressed > max_bytes:
            raise LakeResultLimitError(
                f"Result expands to ~{uncompressed} bytes in memory "
                f"({self.byte_size} compressed); max_bytes={max_bytes}. "
                "Use iter_batches() or duckdb_relation() instead."
            )

        relation = self.duckdb_relation()
        return relation.df()


def submit(
    query: str,
    params: Sequence[LakeBindValue] | None = None,
    *,
    timeout_seconds: int | None = None,
    max_rows: int | None = None,
    max_result_bytes: int | None = None,
    idempotency_key: str | None = None,
    client: NexusTradeClient | None = None,
) -> LakeQueryHandle:
    nt = _client(client)
    limits: dict[str, int] = {}
    if timeout_seconds is not None:
        limits["timeoutSeconds"] = int(timeout_seconds)
    if max_rows is not None:
        limits["maxRows"] = int(max_rows)
    if max_result_bytes is not None:
        limits["maxResultBytes"] = int(max_result_bytes)
    body: dict[str, Any] = {
        "query": query,
        "params": list(params or []),
    }
    if limits:
        body["limits"] = limits
    # Minted BEFORE the request so a failure can hand it back. Generating a
    # fresh uuid on every call meant a lost response could not be retried
    # safely: the second attempt looked like a new query and was billed as one.
    key = idempotency_key or f"lake-{uuid.uuid4()}"
    try:
        operation = nt.create_lake_query(body, idempotency_key=key)
    except NexusTradeApiError as error:
        # Only wrap outcomes that are genuinely UNKNOWN: the request never
        # reached the API (status 0), or the server failed after possibly
        # accepting it (5xx). A 4xx was rejected outright — telling the caller
        # to "retry with this key" would be wrong, and wrapping it would strip
        # the status they need to tell auth from validation.
        if error.status == _NO_HTTP_STATUS or error.status >= 500:
            raise LakeSubmitFailed(
                error.status, error.code, error.message, key
            ) from error
        raise

    handle = _from_operation(operation, nt)
    if isinstance(handle, LakeQueryResult):
        return LakeQueryHandle(
            id=handle.id,
            status=handle.status,
            _client=nt,
            idempotency_key=key,
        )
    handle.idempotency_key = key
    return handle


def get(
    query_id: str,
    *,
    client: NexusTradeClient | None = None,
) -> LakeQueryHandle | LakeQueryResult:
    nt = _client(client)
    return _from_operation(nt.get_lake_query(query_id), nt)


#: How long the client keeps polling beyond the server's execution budget, to
#: cover queue time plus a little slack. A query can sit queued behind others;
#: that wait is not part of its execution allowance.
DEFAULT_QUEUE_ALLOWANCE_SECONDS = 300


def sql(
    query: str,
    params: Sequence[LakeBindValue] | None = None,
    *,
    wait: bool = True,
    query_timeout_seconds: int | None = None,
    wait_timeout_seconds: float | None = None,
    max_rows: int | None = None,
    max_result_bytes: int | None = None,
    idempotency_key: str | None = None,
    client: NexusTradeClient | None = None,
    timeout_seconds: int | None = None,
) -> LakeQueryHandle | LakeQueryResult:
    """Submit a lake query and, by default, wait for it.

    Two different clocks, deliberately separate:

    * ``query_timeout_seconds`` — how long the SERVER may spend executing.
    * ``wait_timeout_seconds``  — how long THIS CALL keeps polling.

    They were one value, which meant queue time was charged against the client's
    patience but not the server's budget: a query that waited two minutes to
    start would have the caller give up while the server was still well inside
    its allowance. Waiting now defaults to the execution budget plus
    ``DEFAULT_QUEUE_ALLOWANCE_SECONDS``.

    A client-side timeout never cancels the query. The durable id remains valid,
    so ``nt.lake.get(id)`` resumes.

    ``timeout_seconds`` is the old single-clock name, kept working as the server
    budget.
    """
    if timeout_seconds is not None and query_timeout_seconds is None:
        query_timeout_seconds = timeout_seconds

    handle = submit(
        query,
        params,
        timeout_seconds=query_timeout_seconds,
        max_rows=max_rows,
        max_result_bytes=max_result_bytes,
        idempotency_key=idempotency_key,
        client=client,
    )
    if not wait:
        return handle

    if wait_timeout_seconds is None and query_timeout_seconds is not None:
        wait_timeout_seconds = float(
            query_timeout_seconds + DEFAULT_QUEUE_ALLOWANCE_SECONDS
        )
    return handle.wait(timeout_seconds=wait_timeout_seconds)


def catalog(*, client: NexusTradeClient | None = None) -> list[LakeTable]:
    nt = _client(client)
    tables = nt.get_lake_catalog()
    out: list[LakeTable] = []
    for item in tables:
        out.append(
            LakeTable(
                schema=str(item.get("schema") or "lake"),
                name=str(item.get("name") or ""),
                grain=str(item.get("grain") or ""),
                tigris_fallback_supported=bool(
                    item.get("tigrisFallbackSupported")
                ),
                notes=(
                    str(item["notes"]) if item.get("notes") is not None else None
                ),
            )
        )
    return out


def describe(
    table: str,
    *,
    client: NexusTradeClient | None = None,
) -> dict[str, Any]:
    return _client(client).describe_lake_table(table)


__all__ = [
    "DEFAULT_QUEUE_ALLOWANCE_SECONDS",
    "LakeBindValue",
    "LakeQueryFailed",
    "LakeQueryHandle",
    "LakeQueryResult",
    "LakeResultLimitError",
    "LakeSubmitFailed",
    "LakeTable",
    "catalog",
    "describe",
    "get",
    "sql",
    "submit",
]
