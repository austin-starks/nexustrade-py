# AGENTS.md — NexusTrade Python SDK

Instructions for coding agents (Claude Code, Cursor, Codex, and friends) writing
NexusTrade strategies with this package. Humans: [README.md](README.md) is the
friendlier read.

## What this package is

A typed client plus ~170 **generated** builders for authoring trading
strategies. The builders are generated from the same indicator specification the
NexusTrade engine executes, so a book assembled from them is structurally valid
before it ever leaves the process.

```
author a portfolio  →  submit a job  →  poll until terminal  →  read result
```

## Setup

```bash
pip install nexustrade
export NEXUSTRADE_API_KEY=sk-...
export NEXUSTRADE_API_BASE_URL=https://nexustrade.io/api/v1
```

```python
import nexustrade as nt
client = nt.NexusTradeClient.from_environment()
```

Keys come from https://nexustrade.io/developers. Never hardcode one into a file
you write; read it from the environment.

## Rules that matter

**1. Use the builders. Never hand-write the JSON.**

```python
# Right — validated shape, correct wire names
nt.buy(nt.stock_asset("SPY"), 100)

# Wrong — silently diverges from the engine's schema
{"type": "Buy", "targetAsset": {"symbol": "SPY"}, "amount": 100}
```

If you cannot find a builder, list them (`nt.__all__`) rather than inventing a
dict. A hand-written payload that the API accepts can still mean something
different from what you intended.

**2. Every mutation needs an idempotency key, and it must be deterministic.**

Jobs cost money. A retry with the *same* key returns the original operation; a
retry with a new key launches a second paid job.

```python
# Right — same logical run reuses the key across retries
client.create_backtest(handle, idempotency_key="momentum-2024-v1")

# Wrong — every retry is a new billable job
client.create_backtest(handle, idempotency_key=f"run-{time.time()}")
```

Reusing a key with a *different* payload is a `409 idempotency_conflict`. Version
the key when the request changes: `momentum-2024-v2`.

**3. `create_*` does not wait. Poll.**

```python
operation = client.create_backtest(handle, idempotency_key="k")
# operation["result"] is ABSENT here — the job has not run yet.
finished = client.wait_for_backtest(operation["id"])
print(finished["result"])
```

A timeout does not cancel the job. Call the waiter again with the same id;
do not resubmit.

**4. Batch when you have several.**

```python
operations = client.create_backtests([h1, h2, h3], idempotency_key="sweep-v1")
results = client.wait_for_backtests(operations)
```

One request, one key, one rate-limit slot — instead of three of each.

**5. Percent semantics.** `buy(asset, 100)` is **100% of portfolio**, not 100
shares. Deployment and allocation parameters are percentages unless a builder
says otherwise.

**6. Credentials come from the environment, or a `.env` file.** Both
`NEXUSTRADE_API_KEY` and `NEXUSTRADE_API_BASE_URL` are read from the process
environment first, then from a `.env` at or above the working directory. Never
hardcode a key into a file you write, and never print one. Exported values win
over the file, so a `.env` cannot silently override a deployment's real config.

**7. The base install is stdlib-only.** `nt.lake.*` needs `pip install
'nexustrade[lake]'`; `nt.spec_curve` and friends need `[stats]`; PDF inspection,
OCR, and schema-bound extraction need `[documents]`. Missing extras raise an
`AttributeError` that names the extra to install — read it rather than guessing.

The same public modules are installed in NexusTrade compute. Use
`nt.inspect_document` on representative pages, then `nt.extract_pdfs` or
`nt.extract_rows` with an explicit schema. Inspect the structured rows and repeat
extraction with a corrected schema when evidence is missing or ambiguous. Regex
is not a substitute for OCR plus schema-bound extraction and must not classify
model-produced asset identities.

**8. Your own data belongs in ONE series.** `create_custom_indicator` mints a
new series every time it is called with a fresh idempotency key. Recurring
collection must `append_custom_indicator_points` onto the id it created the
first time — a new series per run splits the history into fragments no strategy
can read. Persist the id; never re-create by name.

```python
# Right — one series, appended forever
client.append_custom_indicator_points(series_id, todays_points,
                                  idempotency_key=f"mentions-{today}")

# Wrong — a new, disconnected series every run
client.create_custom_indicator({"name": "Mentions", "points": todays_points},
                           idempotency_key=f"mentions-{today}")
```

Point batches are unlimited in size; the SDK sends them inline or uploads them.
Do not chunk by hand.

Declare `point_kind="observation"` for point-in-time samples so date-only rows
retain same-day UTC availability on both inline and large-upload paths. Use
`period_aggregate` plus `aggregate_period` for closed periods, or `disclosed`
with per-row publication times.

For daily market prices, prefer `nt.lake.query("sec_daily_ohlc", ...)` or
`nt.tigris.read_ohlc(...)`. `daily_ohlc` is a legacy/vendor table and may lag
the canonical daily series.

## Recipes

<details open>
<summary><b>Buy and hold</b></summary>

```python
import nexustrade as nt

book = nt.portfolio("Buy and hold SPY", [
    nt.strategy("Buy", nt.always(), nt.buy(nt.stock_asset("SPY"), 100)),
])
```
</details>

<details>
<summary><b>Condition on an indicator</b></summary>

Indicators support Python comparison operators; the result is a `Condition`.

```python
oversold = nt.RSI(nt.stock_asset("AAPL"), 14) < 30

book = nt.portfolio("Dip buyer", [
    nt.strategy("Buy the dip", oversold, nt.buy(nt.stock_asset("AAPL"), 25)),
    nt.strategy(
        "Take profit",
        nt.PositionPercentChange(nt.stock_asset("AAPL")) > 10,
        nt.sell(nt.stock_asset("AAPL"), 100),
    ),
])
```

Combine with `nt.multi`, `nt.at_least`, `nt.at_most`, `nt.exactly`.
</details>

<details>
<summary><b>Rank and rotate a universe</b></summary>

`CANDIDATE` is the placeholder for "each name being evaluated". Use it inside a
pipeline; use a concrete asset outside one.

```python
book = nt.portfolio("Momentum", [
    nt.strategy("Rotate", nt.always(), nt.dynamic_rebalance(
        universe_config=nt.universe("SP500"),
        pipeline=[
            nt.filter(nt.Price(nt.CANDIDATE) > nt.SMA(nt.CANDIDATE, 200)),
            nt.select_top(nt.RSI(nt.CANDIDATE, 14), 10),
        ],
        weight_indicator=nt.RSI(nt.CANDIDATE, 14),
        limit=10,
        deployment_percent=80,
    )),
], initial_value=100_000)
```

`deployment_percent=80` invests 80% of the portfolio across the selection and
leaves the rest in cash. It is a **total** cap, not a per-name one.
</details>

<details>
<summary><b>Backtest, optimize, walk forward</b></summary>

```python
bt = nt.backtest(book, start_date="2024-01-01", end_date="2024-12-31")

opt = nt.optimization(book, start_date="2022-01-01", end_date="2024-12-31")

wf = nt.walk_forward(book, global_start_date="2022-01-01",
                     global_end_date="2024-12-31", fold_count=4)
```

Note walk-forward uses `global_start_date` / `global_end_date` / `fold_count`,
not `start_date` / `end_date`. Each handle goes to its matching
`create_*` + `wait_for_*` pair.
</details>

<details>
<summary><b>Query the data lake</b></summary>

```python
result = nt.lake.sql(
    "SELECT ticker, date, closingPrice FROM lake.daily_ohlc WHERE ticker = ?",
    ["AAPL"],
    max_rows=10_000,
)
frame = result.to_pandas()
```

When the right table or column set is unknown, prefer a one-shot discovery pass:

```python
ask = nt.lake.ask("average daily volume for SPY in 2024")
print(ask.sql)  # log the generated SQL
frame = ask.result().to_pandas()  # materialize the delegated query
```

Always parameterize with `?` rather than interpolating into the SQL string.
Requires the `[lake]` extra.
</details>

<details>
<summary><b>Run an agent</b></summary>

Agents are the one job kind that is NOT fire-and-poll. Three states —
`pending_plan_approval`, `pending_action_approval`, `awaiting_user_input` — are
ones the run cannot leave on its own, so the caller is the approver.

```python
run = client.create_agent("Find momentum names in the S&P 500",
                      idempotency_key="momentum-scan-v1")

for event in run:
    print(event.text)
    if event.needs_approval:
        run.approve()          # or run.reject()
    elif event.needs_input:
        run.say("focus on semis")

print(run.status)
```

Iterating blocks. If you never answer a blocked run, iteration raises
`agent_awaiting_input` rather than spinning silently — the run keeps going
server-side, so reattach with `client.attach_agent(run.id)`.

Approving can place orders, so it needs the `trade` scope; everything else
needs `write`. Agent runs are unavailable to `run_compute` sandbox code.
</details>

## Errors

All failures raise `NexusTradeApiError` with a stable `.status`, `.code`, and
`.message`. Branch on `.code`, never on message text.

| Code | What to do |
| --- | --- |
| `invalid_token` | Key missing/expired, or an OAuth JWT was used. Only `sk-` keys work here. |
| `insufficient_scope` | The key lacks `read` / `write` / `lake`. Do not retry. |
| `invalid_portfolio` | The book is malformed — fix the builders, do not retry as-is. |
| `idempotency_conflict` | Same key, different payload. Version the key. |
| `idempotency_in_progress` | Same key, first call still running. Wait and re-read; never resubmit. |
| `rate_limit_exceeded` | Back off and retry. |
| `operation_timeout` | Job still running. Re-poll the same id; never resubmit. |

`status == 0` means no HTTP status applies: the request never reached the API
(`transport_error`), or the reply failed an envelope check.

## What is and is not here

**Deploying a portfolio IS supported.** `book.save(...)` persists it and
`book.deploy(...)` starts paper trading it; `client.deploy(portfolio_id)` does
the same from an id. `save` and `deploy` return *different* ids — deploying
mints a portfolio rather than converting the draft.

**`deploy` is not always paper.** A portfolio this SDK creates is always paper:
the portfolio spec has no deployment field, so there is no way to ask for a
live one. But `deploy(portfolio_id)` accepts the id of a portfolio that is
*already deployed*, and then it reactivates that portfolio as whatever it
already is. Hand it the id of a paused **live** portfolio — `list_portfolios`
will return one under `include_live=True` — and it resumes live trading against
the connected brokerage. `undeploy` is the same in reverse. Read
`deploymentType` on the response to know which one you got, and treat any id
you did not create in this session as possibly live.

**The screener IS here now.** `nt.nl.screen_stocks("...")` turns a plain-language
question into validated `lake.*` SQL, runs it, and returns the rows *and the
statement*. Prefer it over hand-writing SQL for stock selection: the server
picks tables from the same catalog the validator enforces, so it cannot name a
column that does not exist.

Drop to `nt.lake.sql(...)` when the NL screen returns `outcome ==
"GENERATION_FAILED"`, or when the question is not a stock screen. Note what is
*not* a reason to fall back: `EMPTY` is an answer (every filter ran, nothing
cleared them all), and `CLARIFICATION` means the question was ambiguous — answer
it rather than guessing SQL, because you do not know what to write either.

Always report `screen.sql` alongside the numbers. It is model-generated, so it
is the only way anyone can check the result. This method spends LLM credits;
`nt.lake.sql` does not.

Check `screen.used_fallback_tables` before you trust which tables were read. The
screen narrows the catalog with a table-selector round first; when that round
fails it falls back to the whole stored index, and the SQL is then written
against a much broader context. A `True` here means "verify `screen.tables` is
what you expected" — the rows can look perfectly reasonable and come from the
wrong table. Attributes are snake_case: the camelCase spellings in the JSON
envelope (`usedFallbackTables`, `rowCount`, `asOfDate`) are the wire format and
raise `AttributeError` on the object.

The symbol column is whatever the generated SQL named it. Probe `screen.rows[0]`
for `ticker` / `symbol` / `asset` / `targetAsset` rather than indexing a fixed
key, which raises `KeyError` on a screen that aliased the column or returned an
aggregate.

Not in this SDK. Do not attempt to reach them through it:

- **Submitting a live order** — impossible from anywhere, not just here. Live
  orders are staged `PENDING_USER_APPROVAL` and a human approves them in the
  UI; the brokerage boundary refuses an unapproved live order regardless of
  caller. `create_orders` stages them, it does not send them.
- **Minting a live portfolio, and connecting a brokerage** — both happen in the
  web app. No route under the SDK prefix creates a live deployment; see above
  for reactivating a live portfolio that already exists.

The complete method list — every client and handle method, including the ones
without a worked example above — is the **Complete method reference** table in
[README.md](README.md#complete-method-reference). A test fails if any public
method is missing from it, so it is exhaustive by construction rather than by
maintenance.

## Verifying your work

```python
# Assemble the book and inspect the JSON before spending money on a backtest.
import json
print(json.dumps(book, indent=2))
```

If you are editing this repository rather than consuming it:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -t .
```
