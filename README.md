# NexusTrade Python SDK

Typed portfolio authoring, backtesting, and optimization for
[NexusTrade](https://nexustrade.io).

```bash
pip install nexustrade
```

The base install is stdlib-only. Optional analytics helpers:

```bash
pip install 'nexustrade[stats]'
```

Lake SQL and local Parquet/DuckDB helpers:

```bash
pip install 'nexustrade[lake]'
```

## Quickstart

```python
from nexustrade import (
    NexusTradeClient,
    always,
    backtest,
    buy,
    portfolio,
    stock_asset,
    strategy,
)

nt = NexusTradeClient(
    api_key="sk-...",
    base_url="https://nexustrade.io/api/v1",
)

book = portfolio(
    "Example",
    [
        strategy(
            "Buy SPY",
            always(),
            buy(stock_asset("SPY"), 100),
        )
    ],
)
saved = nt.create_portfolio(book, idempotency_key="example-v1")

operation = nt.create_backtest(
    backtest(
        book,
        start_date="2024-01-01",
        end_date="2024-12-31",
    ),
    idempotency_key="example-backtest-v1",
)
result = nt.get_backtest(operation["id"])
```

## Jobs are asynchronous — you poll

Backtests, optimizations, and walk-forward studies run on the NexusTrade
engine, not in your process. `create_*` enqueues the job and returns
immediately; it does **not** block until results exist. There are no webhooks
today, so you poll `get_*` until the operation reaches a terminal state.

Both calls return the same operation envelope:

```python
{
  "id": "op_...",
  "kind": "backtest",            # backtest | optimization | walk_forward
  "status": "queued",            # queued | running | completed | failed | cancelled
  "result": {...},               # present only once terminal
  "error": {"code": ..., "message": ..., "retryable": ...},  # on failure
}
```

`result` is absent while the job is `queued` or `running`. The client polls for
you, on a deterministic backoff, until the operation is terminal:

```python
finished = nt.wait_for_backtest(operation["id"])
print(finished["result"])
```

`wait_for_backtest`, `wait_for_optimization`, and `wait_for_walk_forward` all
take the same options:

| Option | Default | Meaning |
| --- | --- | --- |
| `timeout_seconds` | `900` | Give up waiting (the job keeps running) |
| `poll_interval_seconds` | `2` | First interval; backs off 1.5x |
| `max_poll_interval_seconds` | `15` | Interval ceiling |
| `raise_on_failure` | `True` | Raise on `failed`/`cancelled` instead of returning |

A failed operation raises `NexusTradeApiError` carrying the API's own error
code; a timeout raises `operation_timeout` and does **not** cancel the job —
call the waiter again with the same id rather than resubmitting. Pass
`raise_on_failure=False` to inspect the terminal envelope yourself.

For a batch, `wait_for_backtests(operations)` waits on each in submission
order. And `wait_for_operation(fetch, id)` is the same poller exposed directly,
for any operation kind.

`create_backtests` submits a batch in one call and returns one operation per
backtest — poll each `id` independently. Prefer it over a loop of
`create_backtest` when you have several: it is one request, one idempotency
key, and one rate-limit slot.

**Idempotency keys make retries free.** If a `create_*` call fails at the
transport layer, retrying with the *same* key returns the original operation
rather than launching a second paid job.

## What the client covers

| Method | Purpose |
| --- | --- |
| `create_portfolio` | Persist an authored portfolio |
| `create_backtest` / `create_backtests` | Submit one or many backtests |
| `get_backtest` | Read a backtest operation |
| `create_optimization` / `get_optimization` | Submit and read an optimization |
| `create_walk_forward` / `get_walk_forward` | Submit and read a walk-forward study |
| `create_lake_query` / `get_lake_query` / `cancel_lake_query` | Submit, inspect, or cancel durable SQL |
| `get_lake_catalog` / `describe_lake_table` | Discover server-resolved lake tables |
| `get_lake_query_manifest` / `download_lake_query_part` | Inspect and stream bounded Parquet results |

Every builder in `nexustrade.portfolio` is generated from the same indicator
specification the NexusTrade engine runs, so an authored book is valid by
construction rather than by convention.

With the `lake` extra, the high-level API keeps arbitrary SQL while making
large results explicit:

```python
import nexustrade as nt

result = nt.lake.sql(
    "SELECT ticker, date, closingPrice FROM lake.daily_ohlc WHERE ticker = ?",
    ["AAPL"],
    max_rows=10_000,
)
frame = result.to_pandas()  # memory-bounded; use iter_batches() for large results
```

`lake.*` is resolved by the server. NexusTrade chooses a compatible backing
engine for the referenced tables; where both sources exist, MotherDuck is
primary and the authorized Tigris-backed table is the fallback. Callers do not
change SQL when the backing engine changes.

## Authentication

**Get a key at [nexustrade.io/developers](https://nexustrade.io/developers)**
(also under Profile → API Keys). Keys begin with `sk-` and are shown once at
creation, so store it immediately.

```python
nt = NexusTradeClient(
    api_key="sk-...",
    base_url="https://nexustrade.io/api/v1",
)
```

Or set `NEXUSTRADE_API_KEY` and `NEXUSTRADE_API_BASE_URL` and call
`NexusTradeClient.from_environment()`. Inside NexusTrade `run_compute`, both are
injected for you automatically.

The client sends `Authorization: Bearer sk-...` over HTTPS. Plain HTTP is
rejected except on loopback, and the client refuses to follow a cross-origin
redirect so the credential cannot be replayed to another host. It also refuses
to follow a redirect on any non-GET request, so a redirect can never
re-submit a paid job.

### Scopes

Give the key the scopes the calls need:

| Scope | Needed for |
| --- | --- |
| `read` | `get_backtest`, `get_optimization`, `get_walk_forward` |
| `write` | `create_portfolio`, `create_backtest(s)`, `create_optimization`, `create_walk_forward` |
| `lake` | Lake catalog, SQL query lifecycle, manifests, and result parts |

A key missing the scope gets `403 insufficient_scope`.

### OAuth is not supported here

NexusTrade's OAuth flow exists for the MCP server, not for this API. The SDK
endpoints accept **only** `sk-` API keys — an OAuth bearer JWT is rejected with
`401 invalid_token`. Use an API key.

## Timeouts

`HttpTransport(timeout_seconds=...)` (default 30) is urllib's per-socket-operation
timeout, so a slow-but-progressing response is not cut off mid-stream. The
TypeScript SDK's equivalent is a total wall-clock deadline. Neither bounds how
long a *job* takes — that is what the polling loop above is for.

## Idempotency

Mutation calls require an idempotency key. Reusing the same key with the same
request returns the original resource instead of launching another paid job.

## Errors

Failures raise `NexusTradeApiError` with a stable `status`, `code`, and
`message`, decoded from the API's error envelope:

```json
{"error": {"code": "invalid_request", "message": "..."}}
```

```python
from nexustrade import NexusTradeApiError

try:
    nt.create_backtest(handle, idempotency_key="run-1")
except NexusTradeApiError as error:
    if error.code == "rate_limit_exceeded":
        ...
    raise
```

Codes you are most likely to see:

| Status | Code | Meaning |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed, or expired key (or an OAuth JWT) |
| 403 | `insufficient_scope` | Key lacks the required `read`, `write`, or `lake` scope |
| 400 | `invalid_request`, `invalid_portfolio` | Malformed input |
| 400 | `invalid_idempotency_key` | Key must match `[A-Za-z0-9._:-]{1,160}` |
| 409 | `idempotency_conflict` | Key reused with a different payload |
| 404 | `not_found`, `operation_not_found` | Unknown or not-yours resource |
| 429 | `rate_limit_exceeded` | Back off and retry |

`status` is `0` when no HTTP status describes the failure: `transport_error`
(the request never reached the API — DNS, TLS, timeout), `unsafe_redirect`, or
an `invalid_response` envelope check on an otherwise-successful reply.

## Scope

Portfolio drafting, backtesting, optimization, walk-forward studies, and
arbitrary read-only SQL over the NexusTrade market-data lake. The public SDK
surface is versioned under `/api/v1/nexustrade`; the SDK base URL is therefore
`https://nexustrade.io/api/v1`. The screener and live trading remain outside
this SDK surface.

## License

MIT
