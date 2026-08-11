"""DuckDB httpfs reads against Tigris (fetch blobs + lake parquet).

Key mechanics (no ListBucket — exact keys only):

- **Year:** ``{prefix}/{YYYY}.manifest.json`` names the live shard(s); ``{prefix}/{YYYY}-all.parquet`` only for prefixes publishing no manifest
- **Day:** ``{prefix}/{YYYY-MM}-{YYYY-MM-DD}.parquet``
- **Snapshot:** ``{prefix}/current.parquet``

Prefer ``read_year_shards`` / ``read_day_shards`` / ``read_snapshot`` (or aliases).
Full prefix list: ``LAKE_CATALOG`` + notes in NexusTrade ``lakeMarketCatalog.ts``.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

import duckdb

# Modal omits /root/.duckdb at sandbox start; the image bakes httpfs here.
# sitecustomize.py also SETs this on every duckdb.connect() so raw LOAD works.
_DEFAULT_EXTENSION_DIR = "/opt/duckdb/extensions"

Grain = Literal["year", "day", "snapshot", "flat_date", "month", "hive"]

# Footgun guard: unfiltered month of options bars can OOM a standard sandbox.
_DEFAULT_MAX_DAY_SHARDS = 62

# Resolved live shard keys per (bucket, prefix, year). Manifests flip at most
# a few times a day and a compute job is short-lived, so one lookup per year
# per process is enough.
_YEAR_SHARD_URL_CACHE: dict[tuple[str, str, int], tuple[str, ...]] = {}

_OPTIONS_COLS = (
    "ticker",
    "underlying",
    "expirationDate",
    "strike",
    "optionType",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
    "implied_volatility",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
)

# Default projection for options helpers — avoid SELECT * on wide chains.
_OPTIONS_SLIM_COLS = (
    "ticker",
    "underlying",
    "expirationDate",
    "strike",
    "optionType",
    "timestamp",
    "implied_volatility",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
)

# Per-process guard: N+1 options reads against year shards hang Modal sandboxes.
_OPTIONS_READ_CALL_LIMIT = 8
_options_read_calls = 0

_FINANCIALS_COLS = (
    "ticker",
    "symbol",
    "date",
    "name",
    "totalRevenue",
    "netIncome",
    "ebitda",
    "grossProfit",
    "freeCashFlow",
    "totalAssets",
    "totalLiab",
    "commonStockSharesOutstanding",
    "shortTermDebt",
    "longTermDebt",
    "operatingIncome",
)

_EARNINGS_COLS = (
    "ticker",
    "symbol",
    "date",
    "epsActual",
    "epsEstimate",
    "epsDifference",
    "surprisePercent",
)

_INTRADAY_BAR_COLS = ("ticker", "timestamp", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class LakeDataset:
    """One lake prefix the sandbox may read."""

    prefix: str
    grain: Grain
    identity: str
    columns: tuple[str, ...]
    notes: str = ""
    key_template: str = ""  # human-readable path pattern when non-standard


def _ds(
    prefix: str,
    grain: Grain,
    identity: str,
    columns: tuple[str, ...],
    notes: str,
    key_template: str = "",
) -> LakeDataset:
    return LakeDataset(
        prefix=prefix,
        grain=grain,
        identity=identity,
        columns=columns,
        notes=notes,
        key_template=key_template,
    )


# Keep in sync with server/src/services/sandbox/lakeMarketCatalog.ts
LAKE_CATALOG: dict[str, LakeDataset] = {
    "sec_daily_ohlc": _ds(
        "sec_daily_ohlc",
        "year",
        "ticker",
        (
            "ticker",
            "date",
            "openingPrice",
            "highestPrice",
            "lowestPrice",
            "closingPrice",
            "volume",
            "marketCap",
            "peRatioTTM",
            "psRatioTTM",
            "pbRatioTTM",
            "enterpriseValue",
            "dividendYield",
        ),
        "Equity daily OHLC + light fundamentals (SEC SoT). Split-adjusted. Prefer.",
    ),
    "daily_ohlc": _ds(
        "daily_ohlc",
        "year",
        "ticker",
        (
            "ticker",
            "date",
            "openingPrice",
            "highestPrice",
            "lowestPrice",
            "closingPrice",
            "volume",
            "marketCap",
            "peRatioTTM",
        ),
        "Legacy/vendor daily OHLC. Prefer sec_daily_ohlc.",
    ),
    "crypto_daily": _ds(
        "crypto_daily",
        "year",
        "symbol",
        (
            "symbol",
            "date",
            "openingPrice",
            "highestPrice",
            "lowestPrice",
            "closingPrice",
            "tradingVolume",
        ),
        "Crypto daily OHLC. Canonical pairs (BTC-USD), not Polygon X: forms.",
    ),
    "options_daily": _ds(
        "options_daily",
        "year",
        "underlying",
        _OPTIONS_COLS,
        "Daily options + IV/Greeks. Prefer read_options_event_windows / one "
        "bulk read_options_daily (not per-event loops). Served by the lake "
        "query API, which owns engine selection.",
    ),
    "intraday_stock": _ds(
        "intraday_stock",
        "day",
        "ticker",
        _INTRADAY_BAR_COLS,
        "Equity intraday bars. Filter tickers + short windows; large memory for wide pulls.",
    ),
    "intraday_crypto": _ds(
        "intraday_crypto",
        "day",
        "ticker",
        _INTRADAY_BAR_COLS,
        "Crypto intraday bars (canonical tickers).",
    ),
    "intraday_options": _ds(
        "intraday_options",
        "day",
        "underlying",
        _OPTIONS_COLS,
        "Options intraday bars. Always filter underlying + dates.",
    ),
    "intraday_options_v2": _ds(
        "intraday_options_v2",
        "day",
        "underlying",
        _OPTIONS_COLS,
        "Merged trades+quotes options intraday (prefer when present).",
    ),
    "intraday_options_quotes": _ds(
        "intraday_options_quotes",
        "flat_date",
        "underlying",
        ("*",),
        "OPRA NBBO quotes.",
        key_template="{prefix}/{YYYY-MM-DD}.parquet",
    ),
    "intraday_stock_monthly": _ds(
        "intraday_stock_monthly",
        "month",
        "ticker",
        _INTRADAY_BAR_COLS,
        "Monthly rollup of equity intraday.",
        key_template="{prefix}/{YYYY-MM}.parquet",
    ),
    "intraday_options_v3_type_dte_monthly": _ds(
        "intraday_options_v3_type_dte_monthly",
        "hive",
        "underlying",
        ("*",),
        "Type×DTE monthly options substrate.",
        key_template="{prefix}/{YYYY-MM}/type={call|put}/dte={bucket}.parquet",
    ),
    "intraday_options_day_substrate_v1": _ds(
        "intraday_options_day_substrate_v1",
        "hive",
        "underlying",
        ("*",),
        "Session-day × type × DTE substrate (advanced).",
        key_template="see pipeline docs; probe exact keys",
    ),
    "canonical_quarterly_financials": _ds(
        "canonical_quarterly_financials",
        "year",
        "ticker",
        _FINANCIALS_COLS,
        "THE quarterly fundamentals table, and what the backtest engine reads. "
        "Point-in-time: `date` is when the statement became publicly available, "
        "never the period end, so it is safe to use as-of any date.",
    ),
    "canonical_annual_financials": _ds(
        "canonical_annual_financials",
        "year",
        "ticker",
        _FINANCIALS_COLS,
        "THE annual fundamentals table. Same point-in-time contract as the "
        "quarterly sibling. Foreign private issuers file 20-F and no 10-Q, so "
        "this is their only statement source.",
    ),
    "sec_quarterly_financials": _ds(
        "sec_quarterly_financials",
        "year",
        "ticker",
        _FINANCIALS_COLS,
        "SEC source-of-truth quarterly mirror incl. filing metadata. "
        "Coverage may be narrower; follow get_manifest role/years.",
    ),
    "sec_annual_financials": _ds(
        "sec_annual_financials",
        "year",
        "ticker",
        _FINANCIALS_COLS,
        "SEC source-of-truth annual mirror incl. filing metadata. "
        "Coverage may be narrower; follow get_manifest role/years.",
    ),
    "quarterly_financials": _ds(
        "quarterly_financials",
        "year",
        "ticker",
        _FINANCIALS_COLS,
        "Primary broad-coverage vendor quarterly financials. "
        "This is not a deprecated table; follow get_manifest role/years.",
    ),
    "annual_financials": _ds(
        "annual_financials",
        "year",
        "ticker",
        _FINANCIALS_COLS,
        "Primary broad-coverage vendor annual financials. "
        "This is not a deprecated table; follow get_manifest role/years.",
    ),
    "sec_quarterly_earnings": _ds(
        "sec_quarterly_earnings",
        "year",
        "ticker",
        _EARNINGS_COLS,
        "SEC earnings calendar + EPS actual/estimate/surprise.",
    ),
    "quarterly_earnings": _ds(
        "quarterly_earnings",
        "year",
        "ticker",
        _EARNINGS_COLS,
        "Legacy/vendor earnings.",
    ),
    "sec_facts": _ds(
        "sec_facts",
        "year",
        "ticker",
        (
            "cik",
            "ticker",
            "taxonomy",
            "concept",
            "unit",
            "val",
            "periodStart",
            "periodEnd",
            "filed",
            "availableAt",
            "accession",
        ),
        "Raw XBRL facts — filter concept for line items (incl. revenue).",
    ),
    "economic": _ds(
        "economic",
        "year",
        "indicator",
        ("indicator", "date", "value"),
        "Macro economic series.",
    ),
    "dividends": _ds(
        "dividends",
        "year",
        "ticker",
        (
            "ticker",
            "cashAmount",
            "declarationDate",
            "exDividendDate",
            "recordDate",
            "payDate",
            "frequency",
            "dividendType",
        ),
        "Dividend history.",
    ),
    "stock_splits": _ds(
        "stock_splits",
        "year",
        "ticker",
        ("ticker", "executionDate", "splitFrom", "splitTo"),
        "Splits. Do not re-apply onto sec_daily_ohlc (already adjusted).",
    ),
    "stock_industries": _ds(
        "stock_industries",
        "snapshot",
        "ticker",
        ("ticker", "symbol", "name", "description"),
        "Industry tag snapshot (+ many boolean industry columns).",
        key_template="{prefix}/current.parquet",
    ),
    "stock_industry_classifications": _ds(
        "stock_industry_classifications",
        "snapshot",
        "ticker",
        ("ticker", "symbol", "name", "description", "classifiedAt"),
        "Canonical industry snapshot (+ required booleans, scores, provenance).",
        key_template="{prefix}/current.parquet",
    ),
    "index_constituents": _ds(
        "index_constituents",
        "snapshot",
        "indexCode",
        (
            "indexCode",
            "indexName",
            "componentCode",
            "componentName",
            "sector",
            "industry",
            "weight",
            "startDate",
            "endDate",
            "isActive",
        ),
        "Index membership history snapshot.",
        key_template="{prefix}/current.parquet",
    ),
    "international_stock": _ds(
        "international_stock",
        "year",
        "ticker",
        ("ticker", "symbol", "country", "commonStockSharesOutstanding", "date", "name"),
        "Non-US equity metadata / shares outstanding.",
    ),
}


def _require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(f"missing required env {key}")
    return value


def _lake_bucket() -> str:
    return _require_env("LAKE_BUCKET")


def _tigris_host() -> str:
    endpoint = _require_env("AWS_ENDPOINT_URL_S3")
    return endpoint.replace("https://", "").replace("http://", "").split("/")[0]


def _load_httpfs(con: duckdb.DuckDBPyConnection) -> None:
    """LOAD httpfs from the image bake path (Modal-safe), then fall back to HOME."""
    ext_dir = os.environ.get("DUCKDB_EXTENSION_DIRECTORY", _DEFAULT_EXTENSION_DIR).strip()
    if ext_dir and os.path.isdir(ext_dir):
        con.execute(f"SET extension_directory='{ext_dir}'")
    con.execute("LOAD httpfs")


def _sdk_lake_enabled() -> bool:
    return bool(
        os.environ.get("NEXUSTRADE_API_BASE_URL", "").strip()
        and os.environ.get("NEXUSTRADE_API_KEY", "").strip()
    )


def _sdk_lake_client() -> Any:
    from nexustrade.client import NexusTradeClient

    return NexusTradeClient.from_environment()


def connect() -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection with httpfs configured for Tigris (not AWS default).

    LOCAL DEVELOPMENT ONLY. Production sandboxes always have the lake API
    (`usesCloudDatabase()` injects NEXUSTRADE_API_BASE_URL/KEY) and every reader
    routes through it; this path exists because a Modal guest cannot reach a
    localhost API. Prefer
    ``nt.lake.sql(...).duckdb_relation()`` for local post-processing.
    """
    warnings.warn(
        "nexustrade.tigris.connect() is deprecated; use nt.lake.sql + "
        "result.duckdb_relation() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    con = duckdb.connect()
    _load_httpfs(con)
    host = _tigris_host()
    con.execute(f"SET s3_endpoint='{host}'")
    con.execute("SET s3_url_style='path'")
    con.execute(f"SET s3_access_key_id='{_require_env('AWS_ACCESS_KEY_ID')}'")
    con.execute(f"SET s3_secret_access_key='{_require_env('AWS_SECRET_ACCESS_KEY')}'")
    region = os.environ.get("AWS_REGION", "auto")
    con.execute(f"SET s3_region='{region}'")
    # Pin the session tz: CAST(timestamp AS DATE) otherwise follows the
    # container's TZ, so the same row lands in different calendar days on
    # different machines. Best-effort — duckdb is unpinned in the image and
    # this setting can require ICU, which cannot autoload inside the egress
    # cage. Losing UTC pinning is survivable; breaking every lake read is not.
    try:
        con.execute("SET TimeZone='UTC'")
    except Exception:
        pass
    return con



def max_options_lake_rows() -> int:
    """Row cap for options reads, shared with the host's MotherDuck cap.

    The worker injects ``SANDBOX_MAX_OPTIONS_LAKE_ROWS`` so both paths refuse
    the same oversized read; without it the Tigris fallback would be unbounded.
    """
    raw = os.environ.get("SANDBOX_MAX_OPTIONS_LAKE_ROWS", "").strip()
    try:
        parsed = int(raw)
    except ValueError:
        return 100_000
    return parsed if parsed > 0 else 100_000



def _require_options_window(
    years: Sequence[int] | None,
    start: str | None,
    end: str | None,
    *,
    label: str,
    event_dates: Sequence[str] | None = None,
) -> None:
    if event_dates:
        return
    if years is None and not start and not end:
        raise ValueError(
            f"{label}: pass years=... and/or start=/end= (YYYY-MM-DD). "
            "Unscoped options reads are rejected."
        )


def _note_options_read(label: str) -> None:
    """Fail fast on per-event options scan loops (Modal hang class)."""
    global _options_read_calls
    _options_read_calls += 1
    if _options_read_calls > _OPTIONS_READ_CALL_LIMIT:
        raise RuntimeError(
            f"{label}: exceeded {_OPTIONS_READ_CALL_LIMIT} options lake reads in one process. "
            "Do not call read_options_daily inside an event loop — use "
            "read_options_event_windows(underlyings, event_dates, …) or ONE bulk "
            "read_options_daily for the full window, then join locally."
        )


def _reset_options_read_calls_for_tests() -> None:
    global _options_read_calls
    _options_read_calls = 0


def _canonical_year_url(bucket: str, prefix: str, year: int) -> str:
    return f"s3://{bucket}/{prefix.strip('/')}/{int(year)}-all.parquet"


def year_shard_urls(
    bucket: str,
    prefix: str,
    year: int,
    con: duckdb.DuckDBPyConnection | None = None,
) -> list[str]:
    """Live shard URLs for one year: manifest first, canonical fallback.

    Prefixes published as immutable versions write ``{year}-v-{runId}.parquet``
    and record the live one in ``{year}.manifest.json``; they never rewrite
    ``{year}-all.parquet``, so that object freezes at the rows AND columns it
    last held. ``sec_daily_ohlc`` canonical sat four days stale and three
    columns short while the manifest shard was current.

    Both reads are exact-key GETs — no ListBucket, which the sandbox creds deny.
    """
    key = (bucket, prefix.strip("/"), int(year))
    cached = _YEAR_SHARD_URL_CACHE.get(key)
    if cached is not None:
        return list(cached)

    manifest_url = f"s3://{bucket}/{prefix.strip('/')}/{int(year)}.manifest.json"
    urls: list[str] = []
    own = con is None
    db = con or connect()
    try:
        rows = db.execute(
            f"SELECT unnest(shards) AS shard FROM read_json_auto('{manifest_url}')"
        ).fetchall()
        urls = [f"s3://{bucket}/{row[0]}" for row in rows if row and row[0]]
    except Exception:
        # No manifest for this year (or unreadable) — canonical is the contract.
        urls = []
    finally:
        if own:
            db.close()

    if not urls:
        urls = [_canonical_year_url(bucket, prefix, year)]
    _YEAR_SHARD_URL_CACHE[key] = tuple(urls)
    return list(urls)


def year_shard_url(
    bucket: str,
    prefix: str,
    year: int,
    con: duckdb.DuckDBPyConnection | None = None,
) -> str:
    """Single live shard URL for one year.

    Retained for callers that need exactly one object (``inspect_table`` schema
    probes, the ``*_year_url`` aliases). Reads that must not miss rows should
    use :func:`year_shard_urls`, since a manifest may name more than one shard.
    """
    return year_shard_urls(bucket, prefix, year, con=con)[0]


def day_shard_url(bucket: str, prefix: str, day: date) -> str:
    return (
        f"s3://{bucket}/{prefix.strip('/')}/"
        f"{day.strftime('%Y-%m')}-{day.isoformat()}.parquet"
    )


def snapshot_url(bucket: str, prefix: str) -> str:
    return f"s3://{bucket}/{prefix.strip('/')}/current.parquet"


# Back-compat path helpers used by aliases / tests.
def ohlc_year_url(
    bucket: str,
    year: int,
    con: duckdb.DuckDBPyConnection | None = None,
) -> str:
    return year_shard_url(bucket, "sec_daily_ohlc", year, con=con)


def options_daily_year_url(
    bucket: str,
    year: int,
    con: duckdb.DuckDBPyConnection | None = None,
) -> str:
    return year_shard_url(bucket, "options_daily", year, con=con)


def crypto_daily_year_url(
    bucket: str,
    year: int,
    con: duckdb.DuckDBPyConnection | None = None,
) -> str:
    return year_shard_url(bucket, "crypto_daily", year, con=con)


def _as_str_list(values: str | Sequence[str], *, label: str) -> list[str]:
    if isinstance(values, str):
        items = [values.strip()]
    else:
        items = [str(v).strip() for v in values]
    out = [v for v in items if v]
    if not out:
        raise ValueError(f"{label} must be a non-empty string or list")
    return out


def _resolve_years(
    years: Sequence[int] | None,
    start: str | None,
    end: str | None,
) -> list[int]:
    if years is not None:
        resolved = sorted({int(y) for y in years})
        if not resolved:
            raise ValueError("years must be non-empty")
        return resolved
    if not start and not end:
        raise ValueError("pass years=... or start=/end= (YYYY-MM-DD) to derive years")
    y0 = int((start or end or "")[:4])
    y1 = int((end or start or "")[:4])
    if y1 < y0:
        y0, y1 = y1, y0
    return list(range(y0, y1 + 1))


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"expected YYYY-MM-DD, got {value!r}") from exc


def _daterange(start: str, end: str) -> list[date]:
    d0 = _parse_day(start)
    d1 = _parse_day(end)
    if d1 < d0:
        d0, d1 = d1, d0
    out: list[date] = []
    cur = d0
    while cur <= d1:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _quote_ident(name: str) -> str:
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"invalid column name: {name!r}")
    return name


def _select_list(columns: Sequence[str] | None, default: Sequence[str] | None) -> str:
    if columns is None:
        if default is None:
            return "*"
        cols = list(default)
    else:
        cols = list(columns)
    if not cols:
        raise ValueError("columns must be non-empty")
    if cols == ["*"] or (len(cols) == 1 and cols[0] == "*"):
        return "*"
    return ", ".join(_quote_ident(c) for c in cols)


def _probe_parquet(con: duckdb.DuckDBPyConnection, url: str) -> bool:
    try:
        con.execute("SELECT 1 FROM read_parquet(?) LIMIT 1", [url]).fetchone()
        return True
    except Exception:
        return False


def _read_union(
    *,
    con: duckdb.DuckDBPyConnection,
    urls: list[str],
    select_sql: str,
    where_sql: str | None,
    params: Sequence[Any],
    label: str,
    row_cap: int | None = None,
    row_cap_label: str | None = None,
) -> Any:
    present = [url for url in urls if _probe_parquet(con, url)]
    if not present:
        preview = ", ".join(urls[:5])
        more = "" if len(urls) <= 5 else f" (+{len(urls) - 5} more)"
        raise RuntimeError(
            f"{label}: no readable shards among: {preview}{more}. "
            "Failed lake read ≠ series missing — check keys/years/dates/env; "
            "do not claim the lake lacks this data.",
        )
    union = " UNION ALL BY NAME ".join(
        f"(SELECT {select_sql} FROM read_parquet(?))" for _ in present
    )
    sql = f"SELECT * FROM ({union}) AS lake_rows"
    if where_sql:
        sql += f" WHERE {where_sql}"
    if row_cap is not None:
        # LIMIT in SQL, not a post-fetch length check: the point is to never
        # materialize an oversized frame in a 4GB sandbox.
        sql += f" LIMIT {int(row_cap) + 1}"
    frame = con.execute(sql, list(present) + list(params)).fetchdf()
    if row_cap is not None and len(frame) > row_cap:
        raise RuntimeError(
            f"{row_cap_label or label} returned more than {row_cap} rows. "
            "Narrow the request rather than retrying: fewer underlyings, a "
            "shorter window, option_type='call'|'put', or "
            "read_options_event_windows(...) instead of a full-year scan."
        )
    return frame


def _dataset(prefix: str) -> LakeDataset | None:
    return LAKE_CATALOG.get(prefix.strip("/"))


def read_year_shards(
    prefix: str,
    *,
    years: Sequence[int] | None = None,
    start: str | None = None,
    end: str | None = None,
    where: str | None = None,
    where_params: Sequence[Any] | None = None,
    columns: Sequence[str] | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
    row_cap: int | None = None,
    row_cap_label: str | None = None,
) -> Any:
    """Read ``{prefix}/{YYYY}-all.parquet`` year rollups with optional SQL WHERE.

    ``where`` is a DuckDB predicate (e.g. ``ticker = ?``) with ``where_params``.
    """
    ds = _dataset(prefix)
    if ds is not None and ds.grain != "year":
        raise ValueError(
            f"prefix {prefix!r} is grain={ds.grain!r}; use the helper matching that grain "
            "(read_day_shards / read_snapshot / connect+SQL for hive/month/flat_date)",
        )
    year_list = _resolve_years(years, start, end)
    bucket = _lake_bucket()
    urls = [
        url
        for y in year_list
        for url in year_shard_urls(bucket, prefix, y, con=con)
    ]
    default_cols = ds.columns if ds is not None else None
    select_sql = _select_list(columns, default_cols)
    own = con is None
    db = con or connect()
    try:
        return _read_union(
            con=db,
            urls=urls,
            select_sql=select_sql,
            where_sql=where,
            params=where_params or (),
            label=f"read_year_shards({prefix})",
            row_cap=row_cap,
            row_cap_label=row_cap_label,
        )
    finally:
        if own:
            db.close()


def read_day_shards(
    prefix: str,
    *,
    start: str,
    end: str,
    where: str | None = None,
    where_params: Sequence[Any] | None = None,
    columns: Sequence[str] | None = None,
    max_days: int = _DEFAULT_MAX_DAY_SHARDS,
    allow_large: bool = False,
    con: duckdb.DuckDBPyConnection | None = None,
) -> Any:
    """Read ``{prefix}/{YYYY-MM}-{YYYY-MM-DD}.parquet`` day shards in a window.

    Enumerates calendar days (weekends/holidays simply miss). Filters in DuckDB —
    does not download whole shards to disk. Caps window length unless
    ``allow_large=True`` (use memory tier large for wide daytrading pulls).
    """
    ds = _dataset(prefix)
    if ds is not None and ds.grain != "day":
        raise ValueError(
            f"prefix {prefix!r} is grain={ds.grain!r}; use the helper matching that grain "
            "(read_year_shards / read_snapshot / connect+SQL for hive/month/flat_date)",
        )
    days = _daterange(start, end)
    if not allow_large and len(days) > max_days:
        raise ValueError(
            f"read_day_shards({prefix}): {len(days)} calendar days exceeds max_days={max_days}. "
            "Narrow the window, or pass allow_large=True (and use memory tier large).",
        )
    bucket = _lake_bucket()
    urls = [day_shard_url(bucket, prefix, d) for d in days]
    default_cols = ds.columns if ds is not None else None
    select_sql = _select_list(columns, default_cols)
    own = con is None
    db = con or connect()
    try:
        return _read_union(
            con=db,
            urls=urls,
            select_sql=select_sql,
            where_sql=where,
            params=where_params or (),
            label=f"read_day_shards({prefix})",
        )
    finally:
        if own:
            db.close()


def read_snapshot(
    prefix: str,
    *,
    where: str | None = None,
    where_params: Sequence[Any] | None = None,
    columns: Sequence[str] | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> Any:
    """Read ``{prefix}/current.parquet`` snapshots (industries, index constituents)."""
    ds = _dataset(prefix)
    if ds is not None and ds.grain != "snapshot":
        raise ValueError(
            f"prefix {prefix!r} is grain={ds.grain!r}; read_snapshot is for snapshot keys only",
        )
    bucket = _lake_bucket()
    url = snapshot_url(bucket, prefix)
    default_cols = ds.columns if ds is not None else None
    select_sql = _select_list(columns, default_cols)
    own = con is None
    db = con or connect()
    try:
        return _read_union(
            con=db,
            urls=[url],
            select_sql=select_sql,
            where_sql=where,
            params=where_params or (),
            label=f"read_snapshot({prefix})",
        )
    finally:
        if own:
            db.close()


_LAKE_TABLES_PATH = "/work/data/lake_tables.json"


def get_manifest() -> dict:
    """Return the map of readable lake TABLES, staged for this job.

    Each entry: ``{name, grain, role, years?|present?, note?}`` where ``role`` is
    ``standard`` or ``backup``. Probe this BEFORE reading the lake so you use a
    table/year that actually exists instead of guessing a key that 404s. Prefer
    ``role == "standard"``; fall to a ``backup`` table only when the standard one
    has a coverage gap (a year you need is absent) or the data looks corrupt.

    ``years`` lists the years with a readable ``{name}/{year}-all.parquet`` (pass
    them to ``read_year_shards``); ``present`` is snapshot readability (for
    ``read_snapshot``). Returns ``{"tables": []}`` when no map was staged (a
    non-lake job); fall back to the dataset docstrings then.
    """
    try:
        with open(_LAKE_TABLES_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        # Not staged (non-lake job) or a corrupt/partial stage — degrade to the
        # dataset docstrings rather than raising into the guest.
        return {"version": 1, "tables": []}


_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_projection(columns: Sequence[str] | None) -> str:
    """Build a projection list from caller-supplied column names.

    Column names cannot be bound as parameters, so they are validated as plain
    identifiers and double-quoted. Joining caller strings straight into the
    SELECT list allowed arbitrary SQL expressions — including subqueries — into
    a query the server then treats as validated.
    """
    if not columns:
        return "*"
    quoted: list[str] = []
    for column in columns:
        name = str(column)
        if not _SQL_IDENTIFIER.match(name):
            raise ValueError(
                f"invalid column name {column!r}: expected a plain identifier"
            )
        quoted.append(f'"{name}"')
    return ", ".join(quoted)


def inspect_table(
    prefix: str,
    *,
    year: int | None = None,
    sample_rows: int = 3,
    con: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, Any]:
    """Inspect one exact canonical lake object without listing the bucket.

    Returns the catalog/manifest metadata, exact key, live parquet schema, and
    at most ``sample_rows`` rows. Year-grain tables require ``year=...``;
    snapshot tables resolve ``current.parquet``. Day/month/hive datasets must
    use their bounded reader because they need an explicit date/partition.
    """
    dataset = _dataset(prefix)
    if dataset is None:
        raise ValueError(
            f"unknown lake table {prefix!r}; inspect LAKE_CATALOG or get_manifest()"
        )
    if not isinstance(sample_rows, int) or not 0 <= sample_rows <= 20:
        raise ValueError("sample_rows must be an integer from 0 through 20")

    bucket = _lake_bucket()
    if dataset.grain == "year":
        if year is None:
            raise ValueError(
                f"inspect_table({prefix!r}) requires year=... for a year-grain table"
            )
        url = year_shard_url(bucket, dataset.prefix, int(year), con=con)
    elif dataset.grain == "snapshot":
        if year is not None:
            raise ValueError(
                f"inspect_table({prefix!r}) is a snapshot; do not pass year"
            )
        url = snapshot_url(bucket, dataset.prefix)
    else:
        raise ValueError(
            f"inspect_table({prefix!r}) does not guess {dataset.grain} partitions; "
            "use the matching bounded reader with explicit dates/partitions"
        )

    manifest_entry = next(
        (
            entry
            for entry in get_manifest().get("tables", [])
            if entry.get("name") == dataset.prefix
        ),
        None,
    )
    result: dict[str, Any] = {
        "name": dataset.prefix,
        "grain": dataset.grain,
        "identity": dataset.identity,
        "notes": dataset.notes,
        "expected_columns": list(dataset.columns),
        "manifest": manifest_entry,
        "key": url,
        "readable": False,
        "schema": [],
        "sample": None,
    }

    if _sdk_lake_enabled() and con is None:
        from nexustrade import lake as nt_lake

        try:
            described = _sdk_lake_client().describe_lake_table(dataset.prefix)
            result["schema"] = described.get("columns") or []
            if sample_rows:
                # Year-grain tables demanded year=... above; sampling across all
                # years would report an unrelated year as readable.
                sql = f"SELECT * FROM lake.{dataset.prefix}"
                params: list[Any] = []
                if dataset.grain == "year":
                    sql += " WHERE EXTRACT(YEAR FROM CAST(date AS DATE)) = ?"
                    params.append(int(year))
                sql += f" LIMIT {int(sample_rows)}"
                frame = nt_lake.sql(sql, params).duckdb_relation().df()
                result["sample"] = frame
                result["readable"] = bool(len(frame))
            result["readable"] = True
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    own = con is None
    db = con or connect()
    try:
        cursor = db.execute(
            "SELECT * FROM read_parquet(?) LIMIT ?",
            [url, sample_rows],
        )
        result["schema"] = [
            {"name": description[0], "type": str(description[1])}
            for description in (cursor.description or [])
        ]
        result["sample"] = cursor.fetchdf()
        result["readable"] = True
        return result
    except Exception as exc:
        result["error"] = (
            f"{type(exc).__name__}: {exc}. This exact key was unreadable; "
            "bucket listing was not attempted."
        )
        return result
    finally:
        if own:
            db.close()


def read_ohlc(
    tickers: str | Sequence[str],
    *,
    years: Sequence[int] | None = None,
    start: str | None = None,
    end: str | None = None,
    columns: Sequence[str] | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> Any:
    """Equity daily OHLC + fundamentals (``sec_daily_ohlc`` year shards).

    When the SDK HTTP lake API is configured, this is a thin ``nt.lake.sql``
    wrapper. Otherwise it falls back to direct Tigris shard reads (deprecated).
    """
    ticker_list = _as_str_list(tickers, label="tickers")
    if _sdk_lake_enabled() and con is None:
        warnings.warn(
            "nexustrade.tigris.read_ohlc is a compatibility wrapper over "
            "nt.lake.sql; prefer nt.lake.sql directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from nexustrade import lake as nt_lake

        projection = _quote_projection(columns)
        where = ["ticker IN (SELECT * FROM UNNEST($1::VARCHAR[]))"]
        params: list[Any] = [ticker_list]
        next_param = 2
        if start:
            where.append(f"CAST(date AS DATE) >= CAST(${next_param} AS DATE)")
            params.append(start)
            next_param += 1
        if end:
            where.append(f"CAST(date AS DATE) <= CAST(${next_param} AS DATE)")
            params.append(end)
            next_param += 1
        if years is not None:
            year_list = sorted({int(y) for y in years})
            where.append(
                f"EXTRACT(YEAR FROM CAST(date AS DATE)) IN "
                f"(SELECT * FROM UNNEST(${next_param}::INTEGER[]))"
            )
            params.append(year_list)
        sql = (
            f"SELECT {projection} FROM lake.sec_daily_ohlc "
            f"WHERE {' AND '.join(where)}"
        )
        result = nt_lake.sql(sql, params)
        # Returns a DataFrame, exactly as the direct-Tigris path always has.
        #
        # An earlier revision returned a lazy DuckDB relation here, so the SAME
        # call returned a different type depending on whether the API env vars
        # happened to be set — silently breaking any caller using pandas
        # attributes or merges. A compatibility wrapper's whole job is to keep
        # its contract; laziness is available, but on the new API
        # (`nt.lake.sql(...).duckdb_relation()`), not by mutating this one.
        return result.duckdb_relation().df()

    where_parts = [f"ticker IN ({', '.join('?' for _ in ticker_list)})"]
    params = list(ticker_list)
    if start:
        where_parts.append("CAST(date AS DATE) >= CAST(? AS DATE)")
        params.append(start)
    if end:
        where_parts.append("CAST(date AS DATE) <= CAST(? AS DATE)")
        params.append(end)
    return read_year_shards(
        "sec_daily_ohlc",
        years=years,
        start=start,
        end=end,
        where=" AND ".join(where_parts),
        where_params=params,
        columns=columns,
        con=con,
    )


def _lake_sql_df(
    table: str,
    where_parts: list,
    params: list,
    columns,
    row_cap: int | None = None,
) -> Any:
    """Run one lake query through the API and return a DataFrame.

    `_select_list`, not `_quote_projection`: the latter rejects `["*"]`, which the
    options/crypto docstrings explicitly tell callers to pass for the wide chain.
    """
    from nexustrade import lake as nt_lake

    projection = _select_list(columns, None)
    sql = f"SELECT {projection} FROM lake.{table}"
    if where_parts:
        sql += f" WHERE {' AND '.join(where_parts)}"
    # The lake API default is 1M rows; the sandbox has ~4GB. Keep the direct-read
    # cap so a wide options window still fails loudly instead of OOMing.
    if row_cap:
        sql += f" LIMIT {int(row_cap) + 1}"
    frame = nt_lake.sql(sql, params).duckdb_relation().df()
    if row_cap and len(frame) > row_cap:
        raise RuntimeError(
            f"lake read returned more than {row_cap} rows; narrow the request "
            "(fewer underlyings, shorter window, or explicit columns)"
        )
    return frame


def _options_where(
    underlying_list: list[str],
    *,
    start: str | None,
    end: str | None,
    option_type: str | None,
    years: Sequence[int] | None,
    use_year_extract: bool,
    event_windows: Sequence[tuple[str, str]] | None = None,
) -> tuple[list[str], list[Any]]:
    where_parts = [f"upper(underlying) IN ({', '.join('?' for _ in underlying_list)})"]
    params: list[Any] = list(underlying_list)
    # OR of per-event windows, mirroring the host's SQL. Filtering in pandas
    # after a contiguous read would pull months of rows to keep days of them.
    if event_windows:
        clauses = []
        for win_start, win_end in event_windows:
            clauses.append(
                "(CAST(timestamp AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE))"
            )
            params.extend([win_start, win_end])
        where_parts.append(f"({' OR '.join(clauses)})")
    if start:
        where_parts.append("CAST(timestamp AS DATE) >= CAST(? AS DATE)")
        params.append(start)
    if end:
        where_parts.append("CAST(timestamp AS DATE) <= CAST(? AS DATE)")
        params.append(end)
    if option_type is not None:
        ot = option_type.strip().lower()
        if ot not in {"call", "put"}:
            raise ValueError("option_type must be 'call', 'put', or None")
        where_parts.append("lower(optionType) = ?")
        params.append(ot)
    # MotherDuck has no year shards, so ``years`` becomes a predicate. It
    # intersects with start/end, matching shard selection on the Tigris path.
    if use_year_extract and years is not None:
        year_list = sorted({int(y) for y in years})
        where_parts.append(
            f"EXTRACT(YEAR FROM CAST(timestamp AS TIMESTAMP)) IN "
            f"({', '.join('?' for _ in year_list)})"
        )
        params.extend(year_list)
    return where_parts, params


def _to_naive_utc(series: Any) -> Any:
    """Drop tz so host (JSON, tz-aware) and Tigris (naive) frames compare."""
    tz = getattr(getattr(series, "dt", None), "tz", None)
    return series.dt.tz_localize(None) if tz is not None else series


_OPTIONS_DATE_COLS = ("timestamp", "expirationDate")
_OPTIONS_NUMERIC_COLS = (
    "strike",
    "implied_volatility",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
)


def _rows_to_frame(rows: list[Any], columns: Sequence[str] | None = None) -> Any:
    """Host rows arrive as JSON, so dtypes must be restored to match Tigris.

    Without this, ``timestamp`` is a str on the host path and datetime64 on the
    Tigris path — the same script then behaves differently depending on which
    path served it.
    """
    import pandas as pd

    # `["*"]` must resolve to the parquet column order, not the host's
    # allowlist order — otherwise positional access and empty-result schemas
    # both diverge from the Tigris path.
    if columns is None:
        frame_cols = None
    elif len(columns) == 1 and columns[0] == "*":
        frame_cols = list(_OPTIONS_COLS)
    else:
        frame_cols = list(columns)
    df = pd.DataFrame(rows, columns=frame_cols)
    for col in _OPTIONS_DATE_COLS:
        if col in df.columns:
            # JSON carries a `Z`, so to_datetime yields tz-aware UTC while the
            # Tigris path is tz-naive — comparing either against a plain
            # Timestamp raises. Normalize to naive UTC to match Tigris.
            df[col] = _to_naive_utc(pd.to_datetime(df[col], utc=True, errors="coerce"))
    for col in _OPTIONS_NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df



def _read_options_tigris(
    underlying_list: list[str],
    *,
    years: Sequence[int] | None,
    start: str | None,
    end: str | None,
    option_type: str | None,
    columns: Sequence[str] | None,
    con: duckdb.DuckDBPyConnection | None,
    event_windows: Sequence[tuple[str, str]] | None = None,
) -> Any:
    where_parts, params = _options_where(
        underlying_list,
        start=start,
        end=end,
        option_type=option_type,
        years=years,
        use_year_extract=False,
        event_windows=event_windows,
    )
    return read_year_shards(
        "options_daily",
        years=years,
        start=start,
        end=end,
        where=" AND ".join(where_parts),
        where_params=params,
        columns=columns,
        con=con,
        row_cap=max_options_lake_rows(),
        row_cap_label="options_daily",
    )


def read_options_daily(
    underlyings: str | Sequence[str],
    *,
    years: Sequence[int] | None = None,
    start: str | None = None,
    end: str | None = None,
    option_type: str | None = None,
    columns: Sequence[str] | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> Any:
    """Daily options + ``implied_volatility`` / Greeks (``options_daily``).

    Prefer **one** call for the full research window (or
    ``read_options_event_windows``) — do not call this inside an event loop.

    Served by the lake query API, which owns engine selection and row limits.
    Default columns are slim (contract + IV + Greeks); pass ``columns=["*"]``
    for the wide chain (OHLCV included).
    """
    _require_options_window(years, start, end, label="read_options_daily")
    _note_options_read("read_options_daily")
    underlying_list = [u.upper() for u in _as_str_list(underlyings, label="underlyings")]
    cols = _OPTIONS_SLIM_COLS if columns is None else columns
    if _sdk_lake_enabled() and con is None:
        where_parts, params = _options_where(
            underlying_list,
            start=start,
            end=end,
            option_type=option_type,
            years=years,
            use_year_extract=True,
        )
        return _lake_sql_df(
            "options_daily",
            where_parts,
            params,
            cols,
            row_cap=max_options_lake_rows(),
        )
    return _read_options_tigris(
        underlying_list,
        years=years,
        start=start,
        end=end,
        option_type=option_type,
        columns=cols,
        con=con,
    )


def read_options_event_windows(
    underlyings: str | Sequence[str],
    event_dates: Sequence[str],
    *,
    pre_days: int = 5,
    post_days: int = 5,
    option_type: str | None = None,
    columns: Sequence[str] | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> Any:
    """Options rows in ±pre/post **calendar days** of each event date.

    Filters to the union of per-event windows in SQL, not the full span between
    min and max. Prefer this over calling ``read_options_daily`` once per event.
    """
    if pre_days < 0 or post_days < 0:
        raise ValueError("pre_days and post_days must be >= 0")
    date_strs: list[str] = []
    dates: list[date] = []
    for raw in event_dates:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"invalid event date: {raw!r}")
        iso = raw.strip()[:10]
        dates.append(date.fromisoformat(iso))
        date_strs.append(iso)
    if not dates:
        raise ValueError("event_dates must be non-empty")
    _note_options_read("read_options_event_windows")
    underlying_list = [u.upper() for u in _as_str_list(underlyings, label="underlyings")]
    cols = _OPTIONS_SLIM_COLS if columns is None else columns
    # The Tigris fallback narrows a contiguous span down to the event windows,
    # which needs the timestamp. Reject up front so both paths behave the same
    # rather than diverging only when MD happens to be busy.
    if list(cols) != ["*"] and "timestamp" not in cols:
        raise ValueError(
            "read_options_event_windows requires 'timestamp' in columns= "
            f"(got {list(cols)}) — it is needed to narrow to the event windows"
        )
    window_start = (min(dates) - timedelta(days=pre_days)).isoformat()
    window_end = (max(dates) + timedelta(days=post_days)).isoformat()
    windows = [
        (
            (event - timedelta(days=pre_days)).isoformat(),
            (event + timedelta(days=post_days)).isoformat(),
        )
        for event in dates
    ]
    if _sdk_lake_enabled() and con is None:
        where_parts, params = _options_where(
            underlying_list,
            start=window_start,
            end=window_end,
            option_type=option_type,
            years=None,
            use_year_extract=False,
            event_windows=windows,
        )
        return _lake_sql_df(
            "options_daily",
            where_parts,
            params,
            cols,
            row_cap=max_options_lake_rows(),
        )
    # start/end pick the year shards; the OR of per-event windows does the
    # narrowing in SQL, mirroring the host. Filtering in pandas instead would
    # pull the whole span into the sandbox just to discard most of it.
    return _read_options_tigris(
        underlying_list,
        years=None,
        start=window_start,
        end=window_end,
        option_type=option_type,
        columns=cols,
        con=con,
        event_windows=windows,
    )


def read_crypto_daily(
    symbols: str | Sequence[str],
    *,
    years: Sequence[int] | None = None,
    start: str | None = None,
    end: str | None = None,
    columns: Sequence[str] | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> Any:
    """Crypto daily OHLC (``crypto_daily``). Canonical pairs like ``BTC-USD``."""
    symbol_list = [s.upper() for s in _as_str_list(symbols, label="symbols")]
    where_parts = [f"upper(symbol) IN ({', '.join('?' for _ in symbol_list)})"]
    params: list[Any] = list(symbol_list)
    if start:
        where_parts.append("CAST(date AS DATE) >= CAST(? AS DATE)")
        params.append(start)
    if end:
        where_parts.append("CAST(date AS DATE) <= CAST(? AS DATE)")
        params.append(end)
    if _sdk_lake_enabled() and con is None:
        if years is not None:
            year_list = sorted({int(y) for y in years})
            where_parts.append(
                f"EXTRACT(YEAR FROM CAST(date AS DATE)) IN "
                f"({', '.join('?' for _ in year_list)})"
            )
            params.extend(year_list)
        dataset = _dataset("crypto_daily")
        return _lake_sql_df(
            "crypto_daily",
            where_parts,
            params,
            columns if columns is not None else (dataset.columns if dataset else None),
        )
    return read_year_shards(
        "crypto_daily",
        years=years,
        start=start,
        end=end,
        where=" AND ".join(where_parts),
        where_params=params,
        columns=columns,
        con=con,
    )


def read_fetch_bytes(object_key: str, bucket: str | None = None) -> bytes:
    """Read a fetch-staged blob from Tigris by object key.

    Tolerant of the two ways LLM-authored code misuses this low-level helper,
    because a prompt warning alone doesn't stop it:
    - a host.fetch RESULT dict → delegate to read_fetch_result (do the right thing)
    - a URL / query-string → raise a clear error instead of the cryptic DuckDB
      "Invalid query parameters" (a `?` in the key is parsed as S3 params).
    Prefer read_fetch_result(result) for host.fetch results.
    """
    if isinstance(object_key, dict):
        data = read_fetch_result(object_key)
        if data is None:
            raise RuntimeError(
                "read_fetch_bytes received a fetch result with ok=False or no objectKey",
            )
        return data
    if not isinstance(object_key, str):
        raise TypeError(
            f"object_key must be a str or a fetch result dict, got {type(object_key)!r}",
        )
    if "://" in object_key or "?" in object_key:
        raise ValueError(
            "read_fetch_bytes takes a bare Tigris object key, not a URL — for a "
            f"host.fetch result use read_fetch_result(result). got: {object_key[:80]!r}",
        )
    b = bucket or _require_env("LAKE_BUCKET")
    con = connect()
    try:
        row = con.execute(
            "SELECT content FROM read_blob(?)",
            [f"s3://{b}/{object_key}"],
        ).fetchone()
    finally:
        con.close()
    if row is None or row[0] is None:
        raise RuntimeError(f"read_blob returned no content for s3://{b}/{object_key}")
    content = row[0]
    if isinstance(content, memoryview):
        return bytes(content)
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    raise RuntimeError(f"unexpected read_blob type: {type(content)!r}")


def read_fetch_result(result: dict[str, Any]) -> bytes | None:
    """Given one host_results row, return fetched bytes or None on failure."""
    if not result.get("ok"):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    object_key = data.get("objectKey")
    if not isinstance(object_key, str) or not object_key.strip():
        return None
    bucket = data.get("bucket")
    bucket_str = bucket if isinstance(bucket, str) and bucket.strip() else None
    return read_fetch_bytes(object_key, bucket_str)
