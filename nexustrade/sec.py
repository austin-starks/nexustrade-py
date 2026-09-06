"""Point-in-time SEC statements and auditable filing-fact candidates."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, Literal

from nexustrade import host

Cadence = Literal["annual", "quarterly"]
FactRole = Literal[
    "pretax_income",
    "income_tax_expense",
    "interest_expense",
    "cash_taxes_paid",
    "cash_interest_paid",
    "research_and_development",
    "stock_based_compensation",
    "diluted_shares",
    "depreciation_and_amortization",
    "capital_expenditures",
    "operating_cash_flow",
    "current_operating_assets",
    "current_operating_liabilities",
]

FACT_ROLES: tuple[FactRole, ...] = (
    "pretax_income",
    "income_tax_expense",
    "interest_expense",
    "cash_taxes_paid",
    "cash_interest_paid",
    "research_and_development",
    "stock_based_compensation",
    "diluted_shares",
    "depreciation_and_amortization",
    "capital_expenditures",
    "operating_cash_flow",
    "current_operating_assets",
    "current_operating_liabilities",
)

_TICKER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9.-]{0,14}$")
_MAX_PERIODS = 40

# The host may expose the same annual filing as both FY and a derived Q4 row.
# These duration fields differ legitimately; its balance/ownership provenance does not.
_STATEMENT_DURATION_FIELDS = frozenset({
    "cadence", "fiscal_period", "period_start", "total_revenue", "gross_profit",
    "operating_income", "net_income", "depreciation_and_amortization", "ebitda",
    "operating_cash_flow", "capital_expenditures", "free_cash_flow", "derived_fields",
})


def _normalized_ticker(ticker: str) -> str:
    value = ticker.strip().upper() if isinstance(ticker, str) else ""
    if not _TICKER_PATTERN.fullmatch(value):
        raise ValueError("ticker must be a valid non-empty ticker")
    return value


def _validated_periods(periods: int) -> int:
    if isinstance(periods, bool) or not isinstance(periods, int):
        raise ValueError("periods must be an integer")
    if periods < 1 or periods > _MAX_PERIODS:
        raise ValueError(f"periods must be between 1 and {_MAX_PERIODS}")
    return periods


def _validated_cadence(cadence: str) -> Cadence:
    if cadence not in ("annual", "quarterly"):
        raise ValueError("cadence must be 'annual' or 'quarterly'")
    return cadence


def _validated_as_of(as_of: str | None) -> str | None:
    if as_of is None:
        return None
    if not isinstance(as_of, str):
        raise ValueError("as_of must be YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(as_of)
    except ValueError as error:
        raise ValueError("as_of must be a real date in YYYY-MM-DD form") from error
    if parsed.isoformat() != as_of:
        raise ValueError("as_of must be YYYY-MM-DD")
    return as_of


def _stable_request_id(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"sec:{digest}"


def _run(
    *,
    action: Literal["statement", "fact_candidates"],
    ticker: str,
    periods: int,
    cadence: Cadence,
    as_of: str | None,
    roles: Sequence[FactRole] | None = None,
    request_id: str | None = None,
    _exit: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "ticker": ticker,
        "periods": periods,
        "cadence": cadence,
    }
    if as_of is not None:
        payload["as_of"] = as_of
    if roles is not None:
        payload["roles"] = list(roles)
    rid = request_id or _stable_request_id(payload)
    result = host.read_result(rid)
    if result is None:
        request: dict[str, Any] = {"id": rid, **payload}
        if "as_of" in request:
            request["asOf"] = request.pop("as_of")
        result = host.run_sec(request)
    # Kept for source compatibility with earlier SDKs. Public SEC calls are now
    # blocking, so neither value changes execution behavior.
    del _exit
    if not result.get("ok"):
        raise RuntimeError(f"sec.{action}({ticker!r}) failed: {result.get('error')}")
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"sec.{action}({ticker!r}) returned an invalid payload")
    return data


def resolved_fact(
    payload: Mapping[str, Any], *, role: FactRole, period_end: str,
    accession: str | None = None,
) -> dict[str, Any]:
    """Select a complete reconciliation with its original filing provenance.

    Pass the frozen fact_candidates response. This does not sum unreconciled NWC
    components or infer absence from a normalized statement null. Incomplete,
    cumulative and ambiguous rows fail with their status so the caller can inspect
    the original evidence and exact filing; they never become numeric zero.
    Keep the returned object in the model, not only its value. A report.ref to
    its value with provenance_path pointing to the whole object can preserve the
    selected candidate IDs, filing identity and resolution status in the report
    handoff. This does not establish a new accounting definition or period.
    """
    if role not in FACT_ROLES:
        raise ValueError(f"unsupported SEC fact role: {role!r}")
    rows = [r for r in payload.get("reconciliation", []) if isinstance(r, Mapping)
            and r.get("role") == role and r.get("period_end") == period_end
            and (accession is None or r.get("accession") == accession)]
    if len(rows) != 1:
        raise ValueError(f"{role} at {period_end}: expected one reconciliation, found {len(rows)}; select accession explicitly")
    row = rows[0]
    complete = ((row.get("status"), row.get("confidence")) in {
        ("direct", "direct_filing_fact"),
        ("components", "derived_from_complete_components"),
    })
    value = row.get("value")
    if not complete or isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{role} at {period_end}: unresolved {row.get('status')}/{row.get('confidence')}: {row.get('note', '')}")
    ids = row.get("selected_candidate_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("resolved fact requires unique selected candidate IDs")
    candidates = []
    for candidate_id in ids:
        matches = [c for c in payload.get("candidates", []) if isinstance(c, Mapping) and c.get("id") == candidate_id]
        if len(matches) != 1:
            raise ValueError(f"missing or duplicate selected candidate: {candidate_id}")
        candidate = matches[0]
        if any(candidate.get(k) != row.get(k) for k in ("role", "period_end", "accession")):
            raise ValueError(f"selected candidate identity mismatch: {candidate_id}")
        candidates.append(deepcopy(dict(candidate)))
    return {**deepcopy(dict(row)), "candidates": candidates}


def statement(
    *,
    ticker: str,
    periods: int = 10,
    cadence: Cadence = "annual",
    as_of: str | None = None,
    request_id: str | None = None,
    _exit: bool = True,
) -> dict[str, Any]:
    """Return the latest normalized SEC statement periods as of a cutoff.

    Rows are ordered newest first. Later amendments win only when they were
    public by ``as_of``. Every row retains its filing and archive provenance.
    """
    return _run(
        action="statement",
        ticker=_normalized_ticker(ticker),
        periods=_validated_periods(periods),
        cadence=_validated_cadence(cadence),
        as_of=_validated_as_of(as_of),
        request_id=request_id,
        _exit=_exit,
    )


def latest_statement(
    *payloads: Mapping[str, Any], as_of: str,
    required_fields: Sequence[str] = (),
) -> dict[str, Any]:
    """Select the newest supplied statement snapshot available by a cutoff.

    Combine annual and quarterly ``statement`` responses for one issuer/ticker.
    Select period end first, then amendment availability; retain all provenance.
    For matching FY/derived-Q4 views of one filing, return the annual view.
    Required missing fields on the newest row raise instead of silently falling
    back to an older annual balance. This does not adjust ownership claims or
    mix balances from different dates, and cannot discover an unfetched filing.
    """
    if as_of is None:
        raise ValueError("as_of is required")
    day = dt.date.fromisoformat(_validated_as_of(as_of))
    cutoff = dt.datetime.combine(day, dt.time.max, tzinfo=dt.timezone.utc)
    if isinstance(required_fields, str) or not all(isinstance(k, str) and k for k in required_fields):
        raise ValueError("required_fields must contain field names")
    candidates: list[tuple[dt.date, dt.datetime, Mapping[str, Any]]] = []
    identities: set[tuple[str, str]] = set()
    for payload in payloads:
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError("latest_statement requires statement responses with rows")
        for row in rows:
            if not isinstance(row, Mapping) or not row.get("cik") or not isinstance(row.get("ticker"), str):
                raise ValueError("statement row requires issuer and ticker identity")
            identities.add((str(row["cik"]), row["ticker"]))
            period_end = _validated_as_of(row.get("period_end"))
            available_at = row.get("available_at")
            if period_end is None or not isinstance(available_at, str):
                raise ValueError("statement row requires period_end and available_at")
            try:
                available = dt.datetime.fromisoformat(available_at.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("invalid statement available_at") from error
            if available.tzinfo is None:
                raise ValueError("statement available_at must include its timezone")
            period = dt.date.fromisoformat(period_end)
            if period <= day and available <= cutoff:
                candidates.append((period, available, row))
    if len(identities) > 1:
        raise ValueError("latest_statement cannot combine different issuer/ticker identities")
    if not candidates:
        raise ValueError("no supplied statement was available by as_of")
    newest = max((period, available) for period, available, _ in candidates)
    selected = [row for period, available, row in candidates if (period, available) == newest]
    if any(row != selected[0] for row in selected[1:]):
        by_cadence: dict[str, Mapping[str, Any]] = {}
        for candidate in selected:
            cadence = candidate.get("cadence")
            if cadence not in ("annual", "quarterly") or (
                cadence in by_cadence and candidate != by_cadence[cadence]
            ):
                raise ValueError("conflicting latest statements; resolve the filing evidence")
            by_cadence[cadence] = candidate
        snapshots = [{k: v for k, v in candidate.items() if k not in _STATEMENT_DURATION_FIELDS}
                     for candidate in by_cadence.values()]
        if (set(by_cadence) != {"annual", "quarterly"} or not selected[0].get("accession") or
            any(snapshot != snapshots[0] for snapshot in snapshots[1:])):
            raise ValueError("conflicting latest statements; resolve the filing evidence")
        selected = [by_cadence["annual"]]
    row = selected[0]
    for key in required_fields:
        value = row.get(key)
        if value is None or value == "" or isinstance(value, (float, int)) and not math.isfinite(value):
            raise ValueError(f"latest statement is missing required field: {key}; reconcile the current filing")
    return deepcopy(dict(row))


def fact_candidates(
    *,
    ticker: str,
    roles: Sequence[FactRole],
    periods: int = 10,
    cadence: Cadence = "annual",
    as_of: str | None = None,
    request_id: str | None = None,
    _exit: bool = True,
) -> dict[str, Any]:
    """Return auditable SEC facts for accounting roles and their reconciliation.

    Direct facts, split D&A, and operating working-capital components remain
    distinguishable. A component set is never mislabeled as a reported total.
    """
    normalized_roles: list[FactRole] = []
    for role in roles:
        if role not in FACT_ROLES:
            raise ValueError(f"unsupported SEC fact role: {role!r}")
        if role not in normalized_roles:
            normalized_roles.append(role)
    if not normalized_roles:
        raise ValueError("roles must contain at least one SEC fact role")
    return _run(
        action="fact_candidates",
        ticker=_normalized_ticker(ticker),
        periods=_validated_periods(periods),
        cadence=_validated_cadence(cadence),
        as_of=_validated_as_of(as_of),
        roles=normalized_roles,
        request_id=request_id,
        _exit=_exit,
    )


__all__ = [
    "Cadence",
    "FACT_ROLES",
    "FactRole",
    "fact_candidates",
    "latest_statement",
    "resolved_fact",
    "statement",
]
