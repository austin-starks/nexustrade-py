"""Public SDK/API helpers plus the durable host-call compatibility bridge."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

HOST_REQUESTS_PATH = "/work/host_requests.jsonl"
HOST_RESULTS_PATH = "/work/host_results.jsonl"
_HOST_ACTIVITY_FILE_ENV = "NEXUSTRADE_HOST_ACTIVITY_FILE"

_pending_requests: list[dict[str, Any]] = []


def _touch_host_activity() -> None:
    """Refresh the parent runner's liveness proof around a brokered host call.

    OpenCode does not stream a shell tool's child stdout until that tool exits.
    A script can therefore be productively issuing multiple bounded gateway
    calls while its outer JSON event stream remains unchanged. The runner owns
    the path and treats only its modification time as advisory progress.
    """
    path = os.environ.get(_HOST_ACTIVITY_FILE_ENV, "").strip()
    if not path:
        return
    try:
        with open(path, "ab"):
            pass
        os.utime(path, None)
    except OSError:
        # Liveness reporting must never turn a valid SDK call into a failure.
        pass


# Keep gateway fan-out below the server's per-host limits. The gateway itself
# owns retries and pacing; a 16-wide client burst used to reach several gateway
# machines at once and defeat each process-local limiter.
def _gateway_fetch_concurrency() -> int:
    raw = os.environ.get("SANDBOX_FETCH_CONCURRENCY", "4")
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


_GATEWAY_FETCH_CONCURRENCY = _gateway_fetch_concurrency()


def _sdk_api_enabled() -> bool:
    return bool(
        os.environ.get("NEXUSTRADE_API_BASE_URL")
        and os.environ.get("NEXUSTRADE_API_KEY")
    )


def _sdk_client() -> Any:
    # Lazy so direct host.py unit tests do not import the package's heavy data
    # dependencies. Production imports through /opt/nexustrade.
    from nexustrade.client import NexusTradeClient

    return NexusTradeClient.from_environment()


def _stable_search_id(query: str, prefer_machine_readable: bool) -> str:
    """Ids are the broker cache key — must be a pure function of the query."""
    digest = hashlib.sha256(
        f"{query}\0{int(prefer_machine_readable)}".encode("utf-8")
    ).hexdigest()[:16]
    return f"search:{digest}"


def _hydrate_spilled_row(row: dict[str, Any]) -> dict[str, Any]:
    """Replace spilled Tigris refs with the logical payload (transparent to callers)."""
    if not row.get("ok"):
        return row
    data = row.get("data")
    if not isinstance(data, dict) or data.get("spilled") is not True:
        return row
    object_key = data.get("objectKey")
    if not isinstance(object_key, str) or not object_key.strip():
        return row
    # Lazy import — lake env is only required when a spilled row is present.
    from nexustrade.tigris import read_fetch_bytes

    bucket = data.get("bucket")
    bucket_str = bucket if isinstance(bucket, str) and bucket.strip() else None
    raw = read_fetch_bytes(object_key, bucket_str)
    payload = json.loads(raw.decode("utf-8"))
    return {**row, "data": payload}


def read_results() -> dict[str, dict[str, Any]]:
    """Parse host_results.jsonl keyed by request id.

    Fat payloads may be Tigris refs (`data.spilled=True`); those are hydrated
    so callers still see logical `data.rows` / candidates / etc.
    """
    if not os.path.exists(HOST_RESULTS_PATH):
        return {}
    out: dict[str, dict[str, Any]] = {}
    with open(HOST_RESULTS_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            req_id = row.get("id")
            if isinstance(req_id, str) and req_id:
                out[req_id] = _hydrate_spilled_row(row)
    return out


def read_result(request_id: str) -> dict[str, Any] | None:
    return read_results().get(request_id)


def _fetch_request_url(spec: str | dict[str, Any]) -> str:
    if isinstance(spec, str):
        return spec
    url = spec.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("fetch request spec requires a non-empty url")
    return url


def _recorded_fetch_url(row: dict[str, Any]) -> str | None:
    direct = row.get("url")
    if isinstance(direct, str) and direct.strip():
        return direct
    data = row.get("data")
    if isinstance(data, dict):
        nested = data.get("url")
        if isinstance(nested, str) and nested.strip():
            return nested
    return None


def _assert_fetch_id_matches_request(
    request_id: str,
    spec: str | dict[str, Any],
    row: dict[str, Any],
) -> None:
    requested_url = _fetch_request_url(spec).strip()
    recorded_url = _recorded_fetch_url(row)
    if recorded_url is None:
        raise ValueError(
            f"fetch id {request_id!r} already has a result whose request URL "
            "cannot be verified; use a new deterministic versioned id"
        )
    if recorded_url.strip() != requested_url:
        raise ValueError(
            f"fetch id {request_id!r} is already bound to {recorded_url!r}, "
            f"not {requested_url!r}; preserve the original specification or use "
            "a new deterministic versioned id"
        )


_FETCH_SPEC_FIELDS = {
    "url",
    "headers",
    "source_receipt",
    "method",
    "body",
    "content_type",
    "boundary_capability",
}


def _normalize_fetch_spec(
    spec: str | dict[str, Any],
) -> str | dict[str, Any]:
    """Validate one public ``host.fetch`` request before either transport.

    The gateway and broker must accept the same public shape. In particular, an
    unknown convenience field such as ``form`` must not be forwarded to a typed
    server route that silently drops it and turns the request into an empty POST.
    """
    if isinstance(spec, str):
        if not spec.strip():
            raise ValueError("fetch request URL must be a non-empty string")
        return spec
    if not isinstance(spec, dict):
        raise ValueError("fetch request must be a URL string or request dict")

    unknown = sorted(set(spec) - _FETCH_SPEC_FIELDS)
    if unknown:
        fields = ", ".join(repr(field) for field in unknown)
        form_help = (
            " For an HTML form, URL-encode its fields into `body`, set "
            "`content_type='application/x-www-form-urlencoded'`, and pass "
            "`source_receipt` from the form-page fetch."
            if "form" in unknown
            else ""
        )
        raise ValueError(f"unsupported fetch request field(s): {fields}.{form_help}")

    normalized = dict(spec)
    normalized.pop("boundary_capability", None)
    url = normalized.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("fetch request spec requires a non-empty url")
    normalized["url"] = url.strip()

    method = normalized.get("method", "GET")
    if not isinstance(method, str):
        raise ValueError("fetch request method must be GET or POST")
    method = method.upper()
    if method not in ("GET", "POST"):
        raise ValueError("fetch request method must be GET or POST")
    normalized["method"] = method

    headers = normalized.get("headers")
    if headers is not None and (
        not isinstance(headers, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in headers.items()
        )
    ):
        raise ValueError("fetch request headers must map strings to strings")
    body = normalized.get("body")
    if body is not None and not isinstance(body, str):
        raise ValueError("fetch request body must be a string")
    content_type = normalized.get("content_type")
    if content_type is not None and not isinstance(content_type, str):
        raise ValueError("fetch request content_type must be a string")
    source_receipt = normalized.get("source_receipt")
    if source_receipt is not None and (
        not isinstance(source_receipt, str) or not source_receipt.strip()
    ):
        raise ValueError("fetch request source_receipt must be a non-empty string")
    if method == "GET" and body is not None:
        raise ValueError("fetch GET requests cannot include a body")
    if method == "POST" and not source_receipt:
        raise ValueError(
            "fetch POST requires source_receipt from a prior fetch in this job "
            "(GET the form/API page first, then POST with that data.receipt)"
        )
    return normalized


def queue_fetch(
    request_id: str,
    url: str,
    headers: dict[str, str] | None = None,
    source_receipt: str | None = None,
    method: str = "GET",
    body: str | None = None,
    content_type: str | None = None,
    **_deprecated: Any,
) -> None:
    """Queue a bounded HTTP request through the host broker.

    GET is SSRF-guarded and may roam public URLs. POST is SSRF-guarded and confined
    to the origin of a prior fetch in this job — pass source_receipt from that
    fetch's data.receipt (GET the form/API page first). Optional source_receipt on
    GET preserves cookies/lineage. Build a POST body yourself (parse the form or
    read the API docs, then urlencode it, or pass JSON with
    content_type="application/json").
    """
    spec = _normalize_fetch_spec(
        {
            "url": url,
            "headers": headers,
            "source_receipt": source_receipt,
            "method": method,
            "body": body,
            "content_type": content_type,
            **_deprecated,
        }
    )
    assert isinstance(spec, dict)
    req: dict[str, Any] = {
        "id": request_id,
        "kind": "fetch",
        "url": spec["url"],
        "method": spec["method"],
    }
    if spec.get("headers"):
        req["headers"] = spec["headers"]
    if spec.get("body") is not None:
        req["body"] = spec["body"]
    if spec.get("content_type"):
        req["contentType"] = spec["content_type"]
    if spec.get("source_receipt"):
        req["sourceReceipt"] = spec["source_receipt"]
    _pending_requests.append(req)


def queue_read_indicator(request_id: str, indicator_id: str) -> None:
    """
    Queue an owner-scoped read of one of YOUR OWN CustomIndicators.

    The host resolves the id, verifies you own it, and returns its accepted points.
    Indicator parquets are tenant-private, so this is brokered by the host rather
    than read from the lake bucket directly. Prefer `signal.read_rows(...)`.
    """
    _pending_requests.append(
        {"id": request_id, "kind": "read_indicator", "indicatorId": indicator_id}
    )


def queue_search(
    request_id: str,
    query: str,
    prefer_machine_readable: bool = True,
) -> None:
    """Queue a Discover Sources search through the host broker (no sandbox egress).

    Use when provided fetch URLs are dead/wrong and you need a different real
    source. Do NOT invent URLs from memory — search, then host.fetch the live
    candidates. Prefer the re-run-safe `search(...)` helper over this low-level
    queue + flush + read_results assembly.
    """
    q = query.strip() if isinstance(query, str) else ""
    if not q:
        raise ValueError("queue_search requires a non-empty query")
    _pending_requests.append(
        {
            "id": request_id,
            "kind": "search",
            "query": q,
            "preferMachineReadable": bool(prefer_machine_readable),
        }
    )


def queue_sec(
    request_id: str,
    *,
    action: str,
    ticker: str,
    periods: int,
    cadence: str,
    as_of: str | None = None,
    roles: Sequence[str] | None = None,
) -> None:
    """Queue a point-in-time SEC statement or filing-fact candidate request.

    Prefer ``nexustrade.sec.statement`` and ``nexustrade.sec.fact_candidates``;
    this low-level helper exists for the shared host-call transport.
    """
    request: dict[str, Any] = {
        "id": request_id,
        "kind": "sec",
        "action": action,
        "ticker": ticker,
        "periods": periods,
        "cadence": cadence,
    }
    if as_of is not None:
        request["asOf"] = as_of
    if roles is not None:
        request["roles"] = list(roles)
    _pending_requests.append(request)


def flush_requests() -> bool:
    """Write queued host requests. Returns True if any were written."""
    if not _pending_requests:
        return False
    with open(HOST_REQUESTS_PATH, "w", encoding="utf-8") as handle:
        for req in _pending_requests:
            handle.write(json.dumps(req) + "\n")
    _pending_requests.clear()
    return True


def _record_host_result(row: dict[str, Any]) -> None:
    """Persist a gateway row so a later re-run reads it instead of refetching."""
    try:
        with open(HOST_RESULTS_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError:
        # Cache miss on re-run is wasteful, not wrong. Never fail the fetch.
        pass


def _gateway_fetch_many(
    missing: dict[str, str | dict[str, Any]],
    timeout_sec: int = 300,
) -> dict[str, dict[str, Any]] | None:
    """Fetch each id through the gateway. None means 'no gateway, use the broker'.

    Returns the same row shape as the broker (body stored to Tigris, receipt
    sealed), so callers keep using read_fetch_result unchanged.
    """
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not api_key:
        return None
    # queue_fetch takes snake_case; the route takes camelCase. Forwarding the
    # spec verbatim silently dropped source_receipt, so every documented POST
    # fetch failed server-side with "requires sourceReceipt".
    key_map = {
        "source_receipt": "sourceReceipt",
        "content_type": "contentType",
    }
    def _one(rid: str, spec: Any) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"id": rid}
        if isinstance(spec, str):
            payload["url"] = spec
        else:
            for key, value in spec.items():
                payload[key_map.get(key, key)] = value
        req = urllib.request.Request(
            f"{base}/host-fetch",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _touch_host_activity()
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                row = json.loads(resp.read().decode("utf-8"))
            _touch_host_activity()
        except urllib.error.HTTPError as exc:
            _touch_host_activity()
            if exc.code == 404:
                return None  # server predates the route — broker handles it
            # ok=False is TERMINAL per the fetch contract, and _record_host_result
            # would persist it. A transient 429/502 must not permanently poison
            # this id — degrade to the broker, which retries properly.
            return None
        except urllib.error.URLError:
            _touch_host_activity()
            # Socket/DNS failure. The broker path survived these; propagating
            # here would kill the whole script.
            return None
        if not isinstance(row, dict):
            return None
        row.setdefault("id", rid)
        return row

    # The broker ran these host-side at FETCH_CONCURRENCY, outside the exec
    # clock. Serially inside the exec, a wide batch can blow the exec timeout.
    items = list(missing.items())
    rows: list[dict[str, Any] | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=_GATEWAY_FETCH_CONCURRENCY) as pool:
        futures = {
            pool.submit(_one, rid, spec): index
            for index, (rid, spec) in enumerate(items)
        }
        for future in as_completed(futures):
            rows[futures[future]] = future.result()
    # Any miss means the gateway could not serve this batch; hand the WHOLE batch
    # to the broker rather than half-filling it.
    if any(row is None for row in rows):
        return None
    out: dict[str, dict[str, Any]] = {}
    for (rid, _spec), row in zip(items, rows):
        assert row is not None
        _record_host_result(row)
        out[rid] = _hydrate_spilled_row(row)
    return out


def fetch(
    requests: dict[str, str | dict[str, Any]],
    _exit: bool = True,
) -> dict[str, dict[str, Any]]:
    """Fetch URLs and return the results. BLOCKS until the host answers.

        inv = fetch({"inventory": url})["inventory"]
        pdfs = fetch({f"pdf_{d}": u for d, u in docs.items()})
        signal.write_rows(parse(pdfs))

    Nothing exits and nothing is re-run. Values are a URL, or a dict of
    queue_fetch kwargs (method/body/headers/source_receipt) minus request_id:

        fetch({"submit": {"url": u, "method": "POST", "body": b,
                          "source_receipt": inv["data"]["receipt"]}})

    Ids are the result cache key: reuse the SAME id for the same resource so a
    later call returns what was already fetched instead of paying for it twice.

    Two different outcomes, two different responses:

    - ok=False  -> the host TRIED and the fetch failed. Terminal: do NOT re-request
      that id, the host will not fetch it twice. Inspect the error and move on.
    - NO ROW AT ALL for an id you asked for -> the host never fulfilled it. That is
      a platform fault, not your bug. Re-request it with the SAME id and print the
      missing ids so it is visible.

    ------------------------------------------------------------------
    BROKER FALLBACK — applies ONLY with no sandbox gateway (local dev).
    Ignore this section unless a call actually exits your process.
    ------------------------------------------------------------------
    Without a gateway this queues the request and exits so the host can fulfill
    it and re-run the script from the top. In that mode ids must be a pure
    function of the resource, because the script recomputes them on every round:
    `int(time.time())`, `uuid4()`, a random suffix, or loop position
    (`f"p{i}"` over enumerate) all produce a NEW key for data the host already
    stored, so the batch never drains and nothing announces it.
    """
    normalized_requests = {
        rid: _normalize_fetch_spec(spec) for rid, spec in requests.items()
    }
    results = read_results()
    for rid, spec in normalized_requests.items():
        existing = results.get(rid)
        if existing is not None:
            _assert_fetch_id_matches_request(rid, spec, existing)
    missing = {
        rid: spec for rid, spec in normalized_requests.items() if rid not in results
    }
    if not missing:
        return {rid: results[rid] for rid in normalized_requests}

    gateway = _gateway_fetch_many(missing)
    if gateway is not None:
        results.update(gateway)
        return {rid: results[rid] for rid in normalized_requests if rid in results}

    for rid, spec in missing.items():
        if isinstance(spec, str):
            queue_fetch(rid, spec)
        else:
            queue_fetch(rid, **spec)
    flush_requests()
    if _exit:
        # The host fulfills what we queued and re-runs this script from the top;
        # this call then returns the data instead of queueing again.
        raise SystemExit(0)
    return {rid: results[rid] for rid in normalized_requests if rid in results}



BACKTESTS_RECORD_PATH = "/work/backtests.json"

_PORTFOLIO_EXAMPLE = (
    'pf.portfolio("My book", [pf.strategy("Rebalance", pf.always(), '
    'pf.dynamic_rebalance(universe=pf.universe("SP500"), pipeline=[], '
    'weight_indicator=pf.Value(1), limit=10, deployment_percent=100))])'
)


def _preflight_portfolio(portfolio: Any) -> None:
    """Reject the malformed portfolio shapes locally, before a paid host round trip.

    Catches the category the operator historically brute-forced (plural ``actions``,
    ``targetAction``, missing ``action.type``, Buy/Sell without ``amount``) and points
    at the exact failing path plus a canonical builder example. This validates shape;
    it does not repair arbitrary dicts. The deep per-field contract still lives on the
    server — this only turns the common structural errors into one in-process message.
    """
    if not isinstance(portfolio, dict):
        raise ValueError(
            f"portfolio must be a dict built with pf.portfolio(...); got "
            f"{type(portfolio).__name__}. Example: {_PORTFOLIO_EXAMPLE}"
        )
    strategies = portfolio.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise ValueError(
            "portfolio.strategies must be a non-empty list of pf.strategy(...) dicts. "
            f"Example: {_PORTFOLIO_EXAMPLE}"
        )
    for index, strat in enumerate(strategies):
        path = f"portfolio.strategies[{index}]"
        if not isinstance(strat, dict):
            raise ValueError(
                f"{path} must be a dict from pf.strategy(...); got {type(strat).__name__}."
            )
        if "actions" in strat:
            raise ValueError(
                f"{path}.actions is not a field — each strategy has ONE singular "
                f"{path}.action (from pf.buy / pf.sell / pf.dynamic_rebalance). "
                f"Example: {_PORTFOLIO_EXAMPLE}"
            )
        if "targetAction" in strat:
            raise ValueError(
                f"{path}.targetAction is not a field — use the singular {path}.action. "
                f"Example: {_PORTFOLIO_EXAMPLE}"
            )
        action = strat.get("action")
        if not isinstance(action, dict):
            got = "missing" if action is None else type(action).__name__
            raise ValueError(
                f"{path}.action must be a singular action dict ({got}). Build it with "
                f"pf.buy(...), pf.sell(...), or pf.dynamic_rebalance(...). "
                f"Example: {_PORTFOLIO_EXAMPLE}"
            )
        action_type = action.get("type")
        if not isinstance(action_type, str) or not action_type:
            keys = sorted(action.keys())
            raise ValueError(
                f"{path}.action.type must be a string (Buy, Sell, DynamicRebalance, "
                f"RebalanceOption, OpenOption, CloseOption, Alert, LaunchAgent). "
                f"Received action keys {keys} — note the field is `type`, not `kind`. "
                f"Example: {_PORTFOLIO_EXAMPLE}"
            )
        if action_type in ("Buy", "Sell"):
            amount = action.get("amount")
            if (
                not isinstance(amount, dict)
                or not isinstance(amount.get("amount"), (int, float))
                or not isinstance(amount.get("type"), str)
            ):
                raise ValueError(
                    f'{path}.action ({action_type}) requires '
                    'amount={"type": <allocation>, "amount": <number>}. '
                    "Build it with pf.buy(target, amount) or pf.sell(target, amount)."
                )


def _normalize_portfolio_job_handle(
    item: dict[str, Any],
    expected_tool: str,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"{expected_tool} input must be a generated SDK handle")
    if item.get("tool") != expected_tool:
        raise ValueError(
            f"expected a {expected_tool} handle; got tool={item.get('tool')!r}"
        )
    portfolio = item.get("portfolio")
    args = item.get("args") or {}
    if not isinstance(portfolio, dict):
        raise ValueError(f"{expected_tool} handle requires an inline portfolio")
    if not isinstance(args, dict):
        raise ValueError(f"{expected_tool} handle args must be an object")
    _preflight_portfolio(portfolio)
    return {"tool": expected_tool, "portfolio": portfolio, "args": args}


def _stable_portfolio_job_id(handle: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(handle, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    prefix = (
        "opt"
        if handle["tool"] == "optimize_portfolio"
        else "wf"
    )
    return f"{prefix}:{digest}"


def queue_portfolio_job(request_id: str, handle: dict[str, Any]) -> None:
    tool = handle.get("tool")
    if tool not in ("optimize_portfolio", "run_walk_forward_study"):
        raise ValueError(f"unsupported portfolio job tool: {tool!r}")
    normalized = _normalize_portfolio_job_handle(handle, tool)
    _pending_requests.append(
        {
            "id": request_id,
            "kind": "portfolio_job",
            "tool": normalized["tool"],
            "portfolio": normalized["portfolio"],
            "args": normalized["args"],
        }
    )


def queue_portfolio_job_read(
    request_id: str,
    tool: str,
    external_id: str,
) -> None:
    if tool not in ("optimize_portfolio", "run_walk_forward_study"):
        raise ValueError(f"unsupported portfolio job tool: {tool}")
    if not isinstance(external_id, str) or not external_id.strip():
        raise ValueError("portfolio job read requires a non-empty id")
    _pending_requests.append(
        {
            "id": request_id,
            "kind": "portfolio_job",
            "tool": tool,
            "externalId": external_id.strip(),
        }
    )


def _run_portfolio_job_handle(
    item: dict[str, Any],
    expected_tool: str,
    *,
    _exit: bool,
) -> dict[str, Any]:
    handle = _normalize_portfolio_job_handle(item, expected_tool)
    request_id = _stable_portfolio_job_id(handle)
    result = read_result(request_id)
    if result is None:
        queue_portfolio_job(request_id, handle)
        flush_requests()
        if _exit:
            raise SystemExit(0)
        return {}
    if not result.get("ok"):
        raise RuntimeError(f"{expected_tool} failed: {result.get('error')}")
    data = result.get("data")
    payload = data if isinstance(data, dict) else {}
    _record_portfolio_job(expected_tool, payload)
    return payload


STUDIES_RECORD_PATH = "/work/walk_forwards.json"
OPTIMIZATIONS_RECORD_PATH = "/work/optimizations.json"


def _record_portfolio_job(expected_tool: str, data: dict[str, Any]) -> None:
    """Record a launched study/optimization id so the report can render its results.

    Mirrors ``_record_ran_backtests``: the host-call broker returns each job's id, which
    the finish path reads (owner-scoped) to render the per-fold OOS table / leaderboard.
    """
    if expected_tool == "run_walk_forward_study":
        job_id = data.get("studyId")
        path = STUDIES_RECORD_PATH
        key = "studyId"
    elif expected_tool == "optimize_portfolio":
        job_id = data.get("optimizationId")
        path = OPTIMIZATIONS_RECORD_PATH
        key = "optimizationId"
    else:
        return
    if not isinstance(job_id, str) or not job_id:
        return
    existing: list[Any] = []
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, list):
            existing = loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        existing = []
    by_id: dict[str, dict[str, Any]] = {}
    for rec in existing:
        if isinstance(rec, dict) and isinstance(rec.get(key), str):
            by_id[rec[key]] = rec
    by_id[job_id] = {**by_id.get(job_id, {}), key: job_id, "status": data.get("status")}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(list(by_id.values()), handle)


def _legacy_portfolio_job_result(
    operation: dict[str, Any],
    id_field: str,
) -> dict[str, Any]:
    """Keep existing run_compute result shapes while using the generic API."""
    result = operation.get("result")
    if isinstance(result, dict):
        return result
    operation_id = operation.get("id")
    status = operation.get("status")
    status_map = {
        "queued": "PENDING",
        "running": "RUNNING",
        "completed": "COMPLETE",
        "failed": "ERROR",
        "cancelled": "CANCELLED",
    }
    return {
        id_field: operation_id,
        "status": status_map.get(status, status),
        **(
            {"error": operation["error"]}
            if isinstance(operation.get("error"), dict)
            else {}
        ),
    }


def submit_optimization(
    item: dict[str, Any],
    *,
    _exit: bool = True,
) -> dict[str, Any]:
    """Launch an ``optimization(...)`` SDK handle and return its async status."""
    if _sdk_api_enabled():
        handle = _normalize_portfolio_job_handle(item, "optimize_portfolio")
        operation = _sdk_client().create_optimization(
            handle,
            idempotency_key=_stable_portfolio_job_id(handle),
        )
        return _legacy_portfolio_job_result(operation, "optimizationId")
    return _run_portfolio_job_handle(
        item,
        "optimize_portfolio",
        _exit=_exit,
    )


def submit_walk_forward(
    item: dict[str, Any],
    *,
    _exit: bool = True,
) -> dict[str, Any]:
    """Launch/preview a ``walk_forward(...)`` SDK handle and return its status."""
    if _sdk_api_enabled():
        handle = _normalize_portfolio_job_handle(
            item,
            "run_walk_forward_study",
        )
        operation = _sdk_client().create_walk_forward(
            handle,
            idempotency_key=_stable_portfolio_job_id(handle),
        )
        return _legacy_portfolio_job_result(operation, "studyId")
    return _run_portfolio_job_handle(
        item,
        "run_walk_forward_study",
        _exit=_exit,
    )


def _poll_portfolio_job(
    tool: str,
    external_id: str,
    refresh_key: str,
    *,
    _exit: bool,
) -> dict[str, Any]:
    if not isinstance(refresh_key, str) or not refresh_key.strip():
        raise ValueError("portfolio job poll requires a non-empty refresh_key")
    digest = hashlib.sha256(
        f"{tool}\0{external_id}\0{refresh_key.strip()}".encode("utf-8")
    ).hexdigest()[:16]
    request_id = f"pjpoll:{digest}"
    result = read_result(request_id)
    if result is None:
        queue_portfolio_job_read(request_id, tool, external_id)
        flush_requests()
        if _exit:
            raise SystemExit(0)
        return {}
    if not result.get("ok"):
        raise RuntimeError(f"portfolio job poll failed: {result.get('error')}")
    data = result.get("data")
    return data if isinstance(data, dict) else {}


def poll_optimization(
    optimization_id: str,
    *,
    refresh_key: str,
    _exit: bool = True,
) -> dict[str, Any]:
    """Owner-scoped optimization status/result read; never launches a new job."""
    if _sdk_api_enabled():
        operation = _sdk_client().get_optimization(optimization_id)
        return _legacy_portfolio_job_result(operation, "optimizationId")
    return _poll_portfolio_job(
        "optimize_portfolio",
        optimization_id,
        refresh_key,
        _exit=_exit,
    )


def poll_walk_forward(
    study_id: str,
    *,
    refresh_key: str,
    _exit: bool = True,
) -> dict[str, Any]:
    """Owner-scoped walk-forward status/result read; never launches a new job."""
    if _sdk_api_enabled():
        operation = _sdk_client().get_walk_forward(study_id)
        return _legacy_portfolio_job_result(operation, "studyId")
    return _poll_portfolio_job(
        "run_walk_forward_study",
        study_id,
        refresh_key,
        _exit=_exit,
    )


def _backtest_arg(
    args: dict[str, Any],
    snake_case: str,
    camel_case: str,
) -> Any:
    """Read canonical generated-SDK args, with camelCase compatibility."""
    return args.get(snake_case, args.get(camel_case))


def _normalize_backtest_spec(item: dict[str, Any]) -> dict[str, Any]:
    """Translate the canonical SDK ``backtest()`` handle into host-request fields.

    ``backtest(portfolio, start_date=..., end_date=...)`` returns
    ``{"tool": "backtest_portfolio", "portfolio": <spec>, "args": {...}}``. Accept
    that handle (or, for a saved portfolio, ``{"portfolio_id": id, ...}``) — do NOT
    invent a separate flat contract.
    """
    if not isinstance(item, dict):
        raise ValueError("backtest input must be a backtest() handle or spec dict")
    if "tool" in item or "args" in item:
        if item.get("tool") != "backtest_portfolio":
            raise ValueError(
                f"submit_backtests expects backtest() handles; got tool={item.get('tool')!r}"
            )
        args = item.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError("backtest() handle args must be an object")
        portfolio = item.get("portfolio")
        return {
            "portfolio": portfolio,
            "start_date": _backtest_arg(args, "start_date", "startDate"),
            "end_date": _backtest_arg(args, "end_date", "endDate"),
            "baseline": _backtest_arg(args, "baseline_symbol", "baselineSymbol"),
            "interval": args.get("interval"),
            "initial_value": _backtest_arg(args, "initial_value", "initialValue"),
            "fee_config": _backtest_arg(args, "fee_config", "feeConfig"),
            "generate_events": _backtest_arg(
                args, "generate_events", "generateEvents"
            ),
            "name": args.get("name")
            or (portfolio.get("name") if isinstance(portfolio, dict) else None),
        }
    return item


def _stable_backtest_id(spec: dict[str, Any]) -> str:
    key = {
        "portfolio_id": spec.get("portfolio_id"),
        "portfolio": spec.get("portfolio"),
        "start_date": spec.get("start_date"),
        "end_date": spec.get("end_date"),
        "baseline": spec.get("baseline"),
        "interval": spec.get("interval"),
        "initial_value": spec.get("initial_value"),
        "fee_config": spec.get("fee_config"),
        "generate_events": bool(spec.get("generate_events")),
    }
    digest = hashlib.sha256(
        json.dumps(key, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"bt:{digest}"


def queue_backtest(request_id: str, spec: dict[str, Any]) -> None:
    """Queue a brokered backtest (worker submits + awaits it on the Rust fleet)."""
    start_date = spec.get("start_date")
    end_date = spec.get("end_date")
    if not start_date or not end_date:
        raise ValueError("backtest requires start_date and end_date")
    portfolio_id = spec.get("portfolio_id")
    portfolio = spec.get("portfolio")
    if not portfolio_id and portfolio is None:
        raise ValueError("backtest requires portfolio or portfolio_id")
    if portfolio_id and portfolio is not None:
        raise ValueError("backtest takes portfolio OR portfolio_id, not both")
    if portfolio is not None:
        _preflight_portfolio(portfolio)
    req: dict[str, Any] = {
        "id": request_id,
        "kind": "backtest",
        "startDate": start_date,
        "endDate": end_date,
    }
    if portfolio_id:
        req["portfolioId"] = portfolio_id
    if portfolio is not None:
        req["portfolio"] = portfolio
    if spec.get("name"):
        req["name"] = spec["name"]
    if spec.get("baseline"):
        req["baseline"] = spec["baseline"]
    if spec.get("interval"):
        req["interval"] = spec["interval"]
    if spec.get("initial_value") is not None:
        req["initialValue"] = spec["initial_value"]
    if spec.get("fee_config") is not None:
        req["feeConfig"] = spec["fee_config"]
    if spec.get("generate_events"):
        req["generateEvents"] = True
    _pending_requests.append(req)


def queue_backtest_poll(request_id: str, backtest_id: str) -> None:
    """Queue one owner-scoped refresh of an existing backtest."""
    if not isinstance(backtest_id, str) or not backtest_id.strip():
        raise ValueError("queue_backtest_poll requires a non-empty backtest_id")
    _pending_requests.append(
        {
            "id": request_id,
            "kind": "backtest",
            "pollBacktestId": backtest_id.strip(),
        }
    )


def _record_ran_backtests(results: list[dict[str, Any]]) -> None:
    """Merge completed backtest ids into the report supplement manifest."""
    fresh = [
        {"backtestId": d["backtestId"], "status": d.get("status")}
        for d in results
        if (
            isinstance(d, dict)
            and d.get("status") == "COMPLETE"
            and isinstance(d.get("backtestId"), str)
            and d["backtestId"]
        )
    ]
    if not fresh:
        return
    existing: list[Any] = []
    try:
        with open(BACKTESTS_RECORD_PATH, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, list):
            existing = loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        existing = []
    by_id: dict[str, dict[str, Any]] = {}
    for rec in existing:
        if isinstance(rec, dict) and isinstance(rec.get("backtestId"), str):
            by_id[rec["backtestId"]] = rec
    for rec in fresh:
        backtest_id = rec["backtestId"]
        by_id[backtest_id] = {**by_id.get(backtest_id, {}), **rec}
    with open(BACKTESTS_RECORD_PATH, "w", encoding="utf-8") as handle:
        json.dump(list(by_id.values()), handle)


def submit_backtests(
    backtests: list[dict[str, Any]], *, _exit: bool = True
) -> list[dict[str, Any]]:
    """Submit N backtests on the Rust fleet and return their results, in order.

    Each item is the canonical SDK handle from ``backtest(portfolio, ...)`` (or,
    for a saved portfolio,
    ``{"portfolio_id": id, "start_date":…, "end_date":…}``).

    ONE call = one host round covering all N: submitted, then awaited together under
    a shared deadline host-side. Unlike ``fetch``/``search``, this one IS brokered:
    it queues the missing (deduped) ids, flushes, and EXITS this process; the host
    fulfills them and re-runs the script, and the call then returns every result.
    That exit is normal here — do not treat it as a failure. Duplicate identical specs collapse to one paid run. Each
    result is the host ``data``::

        {backtestId, status, wasCreated, statistics?, error?}

    A non-COMPLETE ``status`` is a live handle: refresh it later with
    ``poll_backtest(backtestId, refresh_key="phase-2")``. Completed ids are also
    recorded to /work/backtests.json so the report renders their charts.
    """
    if not backtests:
        return []
    specs = [_normalize_backtest_spec(item) for item in backtests]
    ids = [_stable_backtest_id(spec) for spec in specs]
    id_to_spec = dict(zip(ids, specs))
    unique_ids = list(dict.fromkeys(ids))  # dedup, preserve order

    if _sdk_api_enabled():
        unique_specs = [id_to_spec[rid] for rid in unique_ids]
        api_inputs = [
            {
                **({"portfolioId": spec["portfolio_id"]} if spec.get("portfolio_id") else {}),
                **({"portfolio": spec["portfolio"]} if spec.get("portfolio") is not None else {}),
                **({"name": spec["name"]} if spec.get("name") else {}),
                "startDate": spec.get("start_date"),
                "endDate": spec.get("end_date"),
                **({"baseline": spec["baseline"]} if spec.get("baseline") else {}),
                **({"interval": spec["interval"]} if spec.get("interval") else {}),
                **(
                    {"initialValue": spec["initial_value"]}
                    if spec.get("initial_value") is not None
                    else {}
                ),
                **({"feeConfig": spec["fee_config"]} if spec.get("fee_config") is not None else {}),
                **({"generateEvents": True} if spec.get("generate_events") else {}),
            }
            for spec in unique_specs
        ]
        operations = _sdk_client().create_backtests(
            api_inputs,
            idempotency_key="batch:" + hashlib.sha256(
                json.dumps(unique_specs, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()[:24],
        )
        if len(operations) != len(unique_ids):
            raise RuntimeError("NexusTrade API returned the wrong number of backtests")
        by_request = dict(zip(unique_ids, operations))
        pending = [
            operation
            for operation in operations
            if operation.get("status") in ("queued", "running")
        ]
        if pending:
            for operation in pending:
                backtest_id = operation.get("id")
                if not isinstance(backtest_id, str) or not backtest_id:
                    raise RuntimeError("pending backtest operation is missing id")
                queue_backtest_poll(f"apiwait:{backtest_id}", backtest_id)
            flush_requests()
            if _exit:
                raise SystemExit(0)
            return []
        out: list[dict[str, Any]] = []
        for rid in ids:
            operation = by_request[rid]
            result = operation.get("result")
            data = result if isinstance(result, dict) else {
                "backtestId": operation.get("id"),
                "status": operation.get("status"),
            }
            out.append(data)
        _record_ran_backtests(out)
        return out

    results = read_results()
    missing = [rid for rid in unique_ids if rid not in results]
    if missing:
        for rid in missing:
            queue_backtest(rid, id_to_spec[rid])
        flush_requests()
        if _exit:
            raise SystemExit(0)
        return []

    out: list[dict[str, Any]] = []
    for rid in ids:  # original order; duplicates resolve to the same result
        result = results[rid]
        if not result.get("ok"):
            raise RuntimeError(f"backtest failed: {result.get('error')}")
        data = result.get("data")
        out.append(data if isinstance(data, dict) else {})
    _record_ran_backtests(out)
    return out


def submit_backtest(item: dict[str, Any], *, _exit: bool = True) -> dict[str, Any]:
    """Submit one backtest and return its result. Sugar over ``submit_backtests``::

        handle = backtest(portfolio, start_date="2024-01-01", end_date="2024-12-31")
        result = submit_backtest(handle)
    """
    return submit_backtests([item], _exit=_exit)[0]


def _stable_backtest_poll_id(backtest_id: str, refresh_key: str) -> str:
    digest = hashlib.sha256(
        f"{backtest_id}\0{refresh_key}".encode("utf-8")
    ).hexdigest()[:16]
    return f"btpoll:{digest}"


def poll_backtests(
    backtest_ids: list[str],
    *,
    refresh_key: str,
    _exit: bool = True,
) -> list[dict[str, Any]]:
    """Refresh existing backtests once, returning results in input order.

    ``refresh_key`` is part of the broker cache key. Keep it stable across the
    automatic script re-run for this refresh, then use a new semantic key for a
    later refresh (for example ``"after-research"`` then ``"before-report"``).
    """
    if not isinstance(refresh_key, str) or not refresh_key.strip():
        raise ValueError("poll_backtests requires a non-empty refresh_key")
    normalized_ids = [
        backtest_id.strip()
        for backtest_id in backtest_ids
        if isinstance(backtest_id, str) and backtest_id.strip()
    ]
    if len(normalized_ids) != len(backtest_ids):
        raise ValueError("poll_backtests requires non-empty string backtest ids")
    if not normalized_ids:
        return []

    request_ids = [
        _stable_backtest_poll_id(backtest_id, refresh_key.strip())
        for backtest_id in normalized_ids
    ]
    request_to_backtest = dict(zip(request_ids, normalized_ids))
    unique_request_ids = list(dict.fromkeys(request_ids))
    results = read_results()
    missing = [
        request_id
        for request_id in unique_request_ids
        if request_id not in results
    ]
    if missing:
        for request_id in missing:
            queue_backtest_poll(request_id, request_to_backtest[request_id])
        flush_requests()
        if _exit:
            raise SystemExit(0)
        return []

    out: list[dict[str, Any]] = []
    for request_id in request_ids:
        result = results[request_id]
        if not result.get("ok"):
            raise RuntimeError(f"backtest poll failed: {result.get('error')}")
        data = result.get("data")
        out.append(data if isinstance(data, dict) else {})
    _record_ran_backtests(out)
    return out


def poll_backtest(
    backtest_id: str,
    *,
    refresh_key: str,
    _exit: bool = True,
) -> dict[str, Any]:
    """Refresh one existing backtest without creating or charging for a new run."""
    return poll_backtests(
        [backtest_id],
        refresh_key=refresh_key,
        _exit=_exit,
    )[0]


def _gateway_search(
    query: str,
    prefer_machine_readable: bool,
    timeout_sec: int = 180,
) -> dict[str, Any] | None:
    """Discover Sources via the gateway. None means 'no gateway, use the broker'.

    Submit returns immediately (HTTP 202) when work must run; this helper polls
    GET /search/:id with short requests so Cloudflare's origin timeout is never
    held open for the full Discover Sources job. A 404 on POST means the server
    predates this route — fall back rather than fail during a rollout.
    """
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not api_key:
        return None
    search_id = _stable_search_id(query, prefer_machine_readable)
    body = json.dumps(
        {
            "id": search_id,
            "query": query,
            "preferMachineReadable": bool(prefer_machine_readable),
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    submit_req = urllib.request.Request(
        f"{base}/search",
        data=body,
        headers=headers,
        method="POST",
    )
    poll_path = f"{base}/search/{urllib.parse.quote(search_id, safe='')}"
    poll_interval_sec = 2.0
    request_timeout_sec = 30

    def _read_json(resp: Any) -> dict[str, Any]:
        payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("gateway search returned non-object response")
        return payload

    def _error_detail(exc: urllib.error.HTTPError) -> str:
        return exc.read().decode("utf-8", errors="replace")

    _touch_host_activity()
    try:
        with urllib.request.urlopen(submit_req, timeout=request_timeout_sec) as resp:
            _touch_host_activity()
            if resp.status == 200:
                return _read_json(resp)
            if resp.status != 202:
                raise RuntimeError(
                    f"search({query!r}) unexpected HTTP {resp.status}"
                )
    except urllib.error.HTTPError as exc:
        _touch_host_activity()
        if exc.code == 404:
            return None
        if exc.code == 502:
            detail = _error_detail(exc)
            raise RuntimeError(
                f"search({query!r}) HTTP {exc.code}: {detail}"
            ) from exc
        detail = _error_detail(exc)
        raise RuntimeError(f"search({query!r}) HTTP {exc.code}: {detail}") from exc

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(poll_interval_sec)
        poll_req = urllib.request.Request(
            poll_path,
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        _touch_host_activity()
        try:
            with urllib.request.urlopen(
                poll_req, timeout=request_timeout_sec
            ) as poll_resp:
                _touch_host_activity()
                if poll_resp.status == 200:
                    return _read_json(poll_resp)
                if poll_resp.status == 202:
                    continue
                raise RuntimeError(
                    f"search({query!r}) poll unexpected HTTP {poll_resp.status}"
                )
        except urllib.error.HTTPError as exc:
            _touch_host_activity()
            if exc.code == 404:
                # Lost the claim (budget release / stale reclaim) — resubmit once.
                _touch_host_activity()
                with urllib.request.urlopen(
                    submit_req, timeout=request_timeout_sec
                ) as retry_resp:
                    _touch_host_activity()
                    if retry_resp.status == 200:
                        return _read_json(retry_resp)
                    if retry_resp.status != 202:
                        raise RuntimeError(
                            f"search({query!r}) resubmit HTTP {retry_resp.status}"
                        )
                continue
            if exc.code == 502:
                detail = _error_detail(exc)
                raise RuntimeError(
                    f"search({query!r}) HTTP {exc.code}: {detail}"
                ) from exc
            detail = _error_detail(exc)
            raise RuntimeError(
                f"search({query!r}) poll HTTP {exc.code}: {detail}"
            ) from exc

    # Not ready is not failure. The caller already treats None as "no gateway answer, use
    # the broker", queues the same stable id, and lets the next host round collect it. Raising
    # here killed the whole exec step instead, and a slow Discover Sources took two steps and
    # six minutes off a real run before the operator rewrote its query to something the
    # gateway happened to answer faster.
    return None


def search(
    query: str,
    *,
    request_id: str | None = None,
    prefer_machine_readable: bool = True,
    _exit: bool = True,
) -> dict[str, Any]:
    """Discover candidate URLs. Blocks and returns on the call via the gateway;
    falls back to the host broker (queue + exit + re-run) when none is present.

    When provided Discover/agent URLs are dead or wrong, call this instead of
    guessing URLs from memory. The worker runs Discover Sources (with liveness
    probe) and returns candidates; then fetch the live ones:

        res = host.search("Hormuz daily tanker transits CSV/JSON")
        urls = [c["url"] for c in res["candidates"] if c.get("live")]
        pages = host.fetch({f"src_{i}": u for i, u in enumerate(urls[:3])})

    IDS ARE THE CACHE KEY. The default id is a hash of (query, prefer flag) so
    the same search returns cached results on re-run. Pass an explicit
    request_id only when you need a stable alias; never use time/uuid/loop index.

    Returns the host `data` payload: {"candidates": [...], "query": "...", ...}.
    """
    q = query.strip() if isinstance(query, str) else ""
    if not q:
        raise ValueError("search requires a non-empty query")
    rid = request_id or _stable_search_id(q, prefer_machine_readable)
    # Cache FIRST. Backtests and optimizations still exit and re-run the script
    # from the top, so an uncached gateway search above one is re-billed every
    # host round.
    result = read_result(rid)
    if result is None:
        gateway = _gateway_search(q, prefer_machine_readable)
        if gateway is not None:
            _record_host_result({"id": rid, "ok": True, "data": gateway})
            return gateway
    if result is None:
        queue_search(rid, q, prefer_machine_readable=prefer_machine_readable)
        flush_requests()
        if _exit:
            raise SystemExit(0)
        return {}
    if not result.get("ok"):
        raise RuntimeError(f"search({q!r}) failed: {result.get('error')}")
    data = result.get("data")
    if not isinstance(data, dict):
        return {"candidates": []}
    return data


def gateway_fetch_json(url: str, timeout_sec: int = 120) -> dict[str, Any]:
    """
    Small JSON GET via the sandbox LLM gateway /fetch (SSRF-guarded).
    Use host broker (queue_fetch) for large blobs stored in Tigris.
    """
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not api_key:
        raise RuntimeError("gateway_fetch_json requires OPENAI_BASE_URL and OPENAI_API_KEY")
    body = json.dumps({"url": url}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/fetch",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    _touch_host_activity()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        _touch_host_activity()
    except urllib.error.HTTPError as exc:
        _touch_host_activity()
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"gateway fetch HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("gateway fetch returned non-object response")
    return payload


DEFAULT_GATEWAY_LLM_MODEL = "openai/gpt-5.6-luna"
_GATEWAY_CHAT_MAX_ATTEMPTS = 4
_GATEWAY_CHAT_RETRY_BASE_SECONDS = 0.25
# Cloudflare's 52x edge failures are transport outcomes, not evidence that the
# exact model request is invalid. Keep 526 excluded because an invalid origin
# certificate is configuration, while 520-525 and 527 can clear on a later
# bounded attempt just like an ordinary gateway timeout.
_GATEWAY_CHAT_TRANSIENT_HTTP_STATUSES = {
    408,
    425,
    500,
    502,
    503,
    504,
    520,
    521,
    522,
    523,
    524,
    525,
    527,
}


class GatewayChatError(RuntimeError):
    """Base error for gateway failures that semantic callers must not retry."""


class GatewayChatRequestError(GatewayChatError):
    """The gateway permanently rejected this exact request."""


class GatewayChatTransportError(GatewayChatError):
    """Transient transport retries were exhausted at their owning boundary."""


def _gateway_credentials() -> tuple[str, str]:
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not api_key:
        raise RuntimeError("gateway chat requires OPENAI_BASE_URL and OPENAI_API_KEY")
    return base, api_key


def _default_gateway_model() -> str:
    configured = os.environ.get("SANDBOX_LLM_MODEL", DEFAULT_GATEWAY_LLM_MODEL).strip()
    return configured or DEFAULT_GATEWAY_LLM_MODEL


def gateway_image_url_part(data: bytes, mime_type: str | None = None) -> dict[str, Any]:
    """Build an OpenAI ``image_url`` content part from raw image bytes."""
    mime = (mime_type or "image/png").strip() or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{encoded}"},
    }


def gateway_file_part(
    data: bytes,
    *,
    filename: str,
    mime_type: str,
) -> dict[str, Any]:
    """Build an OpenRouter-compatible ``file`` + base64 data-URL content part."""
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "file",
        "file": {
            "filename": filename,
            "file_data": f"data:{mime_type};base64,{encoded}",
        },
    }


def gateway_multimodal_messages(
    prompt: str,
    attachments: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """User message with text plus image_url and/or file attachment parts."""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(attachments)
    return [{"role": "user", "content": content}]


def _build_chat_messages(
    messages: list[dict[str, Any]] | Mapping[str, Any] | str | None,
    *,
    prompt: str | None,
    system: str | None,
) -> list[dict[str, Any]]:
    if isinstance(messages, Mapping):
        if prompt is not None:
            raise ValueError("pass positional prompt payload or prompt=, not both")
        prompt = json.dumps(messages, separators=(",", ":"), ensure_ascii=False)
        messages = None
    if isinstance(messages, str):
        if prompt is not None:
            raise ValueError("pass positional prompt text or prompt=, not both")
        prompt = messages
        messages = None
    if messages is not None:
        if not messages:
            raise ValueError("messages must be non-empty when provided")
        if system is not None and str(system).strip():
            return [{"role": "system", "content": system}, *messages]
        return messages
    if prompt is None or not str(prompt).strip():
        raise ValueError("gateway_chat requires messages or a non-empty prompt")
    built: list[dict[str, Any]] = []
    if system is not None and str(system).strip():
        built.append({"role": "system", "content": system})
    built.append({"role": "user", "content": prompt})
    return built


def _parse_json_content(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    return json.loads(text)


def gateway_chat(
    messages: list[dict[str, Any]] | Mapping[str, Any] | str | None = None,
    *,
    prompt: str | None = None,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0,
    response_format: dict[str, Any] | None = None,
    timeout_sec: int = 180,
    **extra: Any,
) -> dict[str, Any]:
    """
    OpenAI-compatible chat completion via the sandbox gateway (/chat/completions).
    A positional mapping is serialized as the user prompt; a positional list is
    treated as an already-formed OpenAI messages array.
    Returns the raw OpenAI response object (choices, usage, model, …).
    """
    built_messages = _build_chat_messages(messages, prompt=prompt, system=system)
    base, api_key = _gateway_credentials()
    body: dict[str, Any] = {
        "model": model or _default_gateway_model(),
        "temperature": temperature,
        "messages": built_messages,
    }
    if response_format is not None:
        body["response_format"] = response_format
    body.update(extra)
    encoded_body = json.dumps(body).encode("utf-8")
    payload: Any = None
    for attempt in range(_GATEWAY_CHAT_MAX_ATTEMPTS):
        _touch_host_activity()
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=encoded_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            _touch_host_activity()
            break
        except urllib.error.HTTPError as exc:
            _touch_host_activity()
            detail = exc.read().decode("utf-8", errors="replace")
            if (
                exc.code not in _GATEWAY_CHAT_TRANSIENT_HTTP_STATUSES
                or attempt + 1 == _GATEWAY_CHAT_MAX_ATTEMPTS
            ):
                error_type = (
                    GatewayChatTransportError
                    if exc.code in _GATEWAY_CHAT_TRANSIENT_HTTP_STATUSES
                    else GatewayChatRequestError
                )
                raise error_type(f"gateway chat HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            _touch_host_activity()
            if attempt + 1 == _GATEWAY_CHAT_MAX_ATTEMPTS:
                raise GatewayChatTransportError(
                    f"gateway chat transport failed after "
                    f"{_GATEWAY_CHAT_MAX_ATTEMPTS} attempts: {exc}"
                ) from exc

        retry_base = _GATEWAY_CHAT_RETRY_BASE_SECONDS * (2**attempt)
        time.sleep(random.uniform(retry_base * 0.5, retry_base * 1.5))
    if not isinstance(payload, dict):
        raise RuntimeError("gateway chat returned non-object response")
    return payload


def gateway_chat_text(
    messages: list[dict[str, Any]] | Mapping[str, Any] | str | None = None,
    *,
    prompt: str | None = None,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0,
    response_format: dict[str, Any] | None = None,
    timeout_sec: int = 180,
    **extra: Any,
) -> str:
    """Chat completion returning the assistant message content string."""
    payload = gateway_chat(
        messages,
        prompt=prompt,
        system=system,
        model=model,
        temperature=temperature,
        response_format=response_format,
        timeout_sec=timeout_sec,
        **extra,
    )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("gateway chat response missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise RuntimeError("gateway chat response missing message content")
    return content


def gateway_chat_json(
    messages: list[dict[str, Any]] | Mapping[str, Any] | str | None = None,
    *,
    prompt: str | None = None,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0,
    json_schema: dict[str, Any] | None = None,
    schema_name: str = "response",
    strict: bool = True,
    timeout_sec: int = 180,
    **extra: Any,
) -> Any:
    """
    Structured output via response_format. Pass json_schema for strict JSON Schema;
    otherwise uses json_object mode and parses the assistant content as JSON.
    """
    if json_schema is not None:
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": strict,
                "schema": json_schema,
            },
        }
    else:
        response_format = {"type": "json_object"}
    content = gateway_chat_text(
        messages,
        prompt=prompt,
        system=system,
        model=model,
        temperature=temperature,
        response_format=response_format,
        timeout_sec=timeout_sec,
        **extra,
    )
    return _parse_json_content(content)


def gateway_semantic_verify(
    payload: Mapping[str, Any],
    *,
    timeout_sec: int = 300,
) -> dict[str, Any]:
    """Run the host-owned semantic citation verifier.

    The host resolves every cited RFC 6901 pointer, expands the complete
    same-record evidence inventory, pins the stored verifier prompt to native
    Luna, and rejects changed ids, cardinality, or cross-record evidence.
    """
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("semantic verifier payload must be a non-empty mapping")
    base, api_key = _gateway_credentials()
    encoded_body = json.dumps(dict(payload)).encode("utf-8")
    response_payload: Any = None
    for attempt in range(_GATEWAY_CHAT_MAX_ATTEMPTS):
        _touch_host_activity()
        req = urllib.request.Request(
            f"{base}/semantic/verify",
            data=encoded_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                response_payload = json.loads(resp.read().decode("utf-8"))
            _touch_host_activity()
            break
        except urllib.error.HTTPError as exc:
            _touch_host_activity()
            detail = exc.read().decode("utf-8", errors="replace")
            if (
                exc.code not in _GATEWAY_CHAT_TRANSIENT_HTTP_STATUSES
                or attempt + 1 == _GATEWAY_CHAT_MAX_ATTEMPTS
            ):
                error_type = (
                    GatewayChatTransportError
                    if exc.code in _GATEWAY_CHAT_TRANSIENT_HTTP_STATUSES
                    else GatewayChatRequestError
                )
                raise error_type(
                    f"gateway semantic verifier HTTP {exc.code}: {detail}"
                ) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            _touch_host_activity()
            if attempt + 1 == _GATEWAY_CHAT_MAX_ATTEMPTS:
                raise GatewayChatTransportError(
                    "gateway semantic verifier transport failed after "
                    f"{_GATEWAY_CHAT_MAX_ATTEMPTS} attempts: {exc}"
                ) from exc
        retry_base = _GATEWAY_CHAT_RETRY_BASE_SECONDS * (2**attempt)
        time.sleep(random.uniform(retry_base * 0.5, retry_base * 1.5))
    if not isinstance(response_payload, dict):
        raise RuntimeError("gateway semantic verifier returned non-object response")
    result = response_payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("gateway semantic verifier response omitted result")
    return result
