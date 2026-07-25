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
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Union

from nexustrade.client import (
    NexusTradeApiError,
    NexusTradeClient,
    wait_for_operation,
)

LakeBindValue = Union[
    str, int, float, bool, None, list[str], list[int], list[float], list[bool]
]

_MAX_PART_READ = 64 * 1024 * 1024


class LakeResultLimitError(RuntimeError):
    """Raised when ``to_pandas`` would exceed the caller-provided bound."""


class LakeQueryFailed(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


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
    id: str
    status: str
    _client: NexusTradeClient
    error: dict[str, Any] | None = None

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

        parts = self.result.get("parts") or []
        if not isinstance(parts, list):
            raise LakeQueryFailed("invalid_response", "manifest parts missing")

        for part in parts:
            if not isinstance(part, Mapping):
                continue
            index = int(part["part"])
            expected_sha = str(part["sha256"])
            expected_size = int(part["byteSize"])
            final_name = f"part-{index:05d}.parquet"
            final_path = root / final_name
            entry = completed.get(final_name)
            if (
                resume
                and isinstance(entry, Mapping)
                and entry.get("sha256") == expected_sha
                and final_path.exists()
                and final_path.stat().st_size == expected_size
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
            manifest_path.write_text(
                json.dumps(completed, indent=2, sort_keys=True)
            )
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
        import duckdb

        directory = self.download()
        glob = str(directory / "part-*.parquet")
        con = connection or duckdb.connect()
        return con.from_parquet(glob)

    def to_pandas(
        self,
        *,
        max_bytes: int,
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
    key = idempotency_key or f"lake-{uuid.uuid4()}"
    operation = nt.create_lake_query(body, idempotency_key=key)
    handle = _from_operation(operation, nt)
    if isinstance(handle, LakeQueryResult):
        return LakeQueryHandle(id=handle.id, status=handle.status, _client=nt)
    return handle


def get(
    query_id: str,
    *,
    client: NexusTradeClient | None = None,
) -> LakeQueryHandle | LakeQueryResult:
    nt = _client(client)
    return _from_operation(nt.get_lake_query(query_id), nt)


def sql(
    query: str,
    params: Sequence[LakeBindValue] | None = None,
    *,
    wait: bool = True,
    timeout_seconds: int | None = None,
    max_rows: int | None = None,
    max_result_bytes: int | None = None,
    idempotency_key: str | None = None,
    client: NexusTradeClient | None = None,
) -> LakeQueryHandle | LakeQueryResult:
    handle = submit(
        query,
        params,
        timeout_seconds=timeout_seconds,
        max_rows=max_rows,
        max_result_bytes=max_result_bytes,
        idempotency_key=idempotency_key,
        client=client,
    )
    if not wait:
        return handle
    return handle.wait(
        timeout_seconds=float(timeout_seconds)
        if timeout_seconds is not None
        else None
    )


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
    "LakeBindValue",
    "LakeQueryFailed",
    "LakeQueryHandle",
    "LakeQueryResult",
    "LakeResultLimitError",
    "LakeTable",
    "catalog",
    "describe",
    "get",
    "sql",
    "submit",
]
