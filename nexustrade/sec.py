"""Point-in-time SEC statements and auditable filing-fact candidates."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from nexustrade import host

Cadence = Literal["annual", "quarterly"]
DimensionalFilter = Literal["all", "only", "none"]
SecAction = Literal[
    "statement",
    "fact_candidates",
    "fact_instances",
    "filed_concepts",
    "dimensioned_concepts",
    "business_breakdowns",
]
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
_MAX_CONCEPTS = 100
_MAX_FORMS = 20
_MAX_FILINGS = 4
_MAX_ROWS = 500
_CONCEPT_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")
_FORM_PATTERN = re.compile(r"^[A-Z0-9-]+(?:/A)?$")

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


def _required_as_of(as_of: str | None) -> str:
    value = _validated_as_of(as_of)
    if value is None:
        raise ValueError("as_of is required for SEC Notes queries")
    return value


def _validated_string_list(
    values: Sequence[str] | None,
    *,
    field: str,
    maximum: int,
    pattern: re.Pattern[str],
    required: bool,
) -> list[str] | None:
    if values is None:
        if required:
            raise ValueError(f"{field} must contain at least one value")
        return None
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be a sequence of strings")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ValueError(f"{field} contains an invalid value")
        if value not in result:
            result.append(value)
    if not result or len(result) > maximum:
        raise ValueError(f"{field} must contain 1 through {maximum} values")
    return result


def _validated_positive_integer(value: int | None, *, field: str, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise ValueError(f"{field} must be an integer from 1 through {maximum}")
    return value


def _stable_request_id(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"sec:{digest}"


def _run_payload(
    *,
    action: SecAction,
    ticker: str,
    payload: dict[str, Any],
    request_id: str | None,
    _exit: bool,
) -> dict[str, Any]:
    stable_payload = {"action": action, "ticker": ticker, **payload}
    rid = request_id or _stable_request_id(stable_payload)
    result = host.read_result(rid)
    if result is None:
        request: dict[str, Any] = {"id": rid, **stable_payload}
        field_names = {
            "as_of": "asOf",
            "period_end_from": "periodEndFrom",
            "period_end_to": "periodEndTo",
            "filing_period_from": "filingPeriodFrom",
            "filing_period_to": "filingPeriodTo",
            "max_filings": "maxFilings",
            "name_contains": "nameContains",
        }
        for source, target in field_names.items():
            if source in request:
                request[target] = request.pop(source)
        result = host.run_sec(request)
    del _exit
    if not result.get("ok"):
        raise RuntimeError(f"sec.{action}({ticker!r}) failed: {result.get('error')}")
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"sec.{action}({ticker!r}) returned an invalid payload")
    return data


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
    payload: dict[str, Any] = {"periods": periods, "cadence": cadence}
    if as_of is not None:
        payload["as_of"] = as_of
    if roles is not None:
        payload["roles"] = list(roles)
    return _run_payload(
        action=action,
        ticker=ticker,
        payload=payload,
        request_id=request_id,
        _exit=_exit,
    )


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


class FactSelectionError(ValueError):
    """All unresolved named selections, suitable for local recovery diagnostics."""

    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        self.diagnostics = deepcopy(diagnostics)
        super().__init__("SEC fact selection failed: " + json.dumps(diagnostics, sort_keys=True))


def _validate_fact_selector(name: str, selector: Mapping[str, Any]) -> None:
    if not isinstance(name, str) or not name.strip() or not isinstance(selector, Mapping):
        raise ValueError("every selection needs a nonempty name and selector mapping")
    required = {"tag", "accession", "period_end", "unit", "quarters", "dimensions"}
    if set(selector) - required - {"fact_id"} or required - set(selector):
        raise ValueError(f"{name}: selector needs exactly {sorted(required)} and optional fact_id")
    for field in ("tag", "accession", "period_end", "unit"):
        if not isinstance(selector[field], str) or not selector[field].strip():
            raise ValueError(f"{name}.{field} must be a nonempty string")
    _required_as_of(selector["period_end"])
    quarters, dimensions = selector["quarters"], selector["dimensions"]
    if isinstance(quarters, bool) or not isinstance(quarters, int) or quarters < 0:
        raise ValueError(f"{name}.quarters must be a nonnegative integer")
    if dimensions is not None and (not isinstance(dimensions, str) or not dimensions):
        raise ValueError(f"{name}.dimensions must be None or an exact nonempty raw string")
    fact_id = selector.get("fact_id")
    if fact_id is not None and (not isinstance(fact_id, str) or not fact_id):
        raise ValueError(f"{name}.fact_id must be an exact nonempty returned ID")


def fact_availability(
    payload: Mapping[str, Any], selections: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Inspect named exact selections before calculation, without asserting absence.

    Uses the same selectors and evidence validation as ``select_facts``. Each
    result describes ONLY the supplied frozen query: selected, not_queried,
    incomplete_query, source_not_retrieved, not_found_in_query, context_mismatch,
    ambiguous or invalid.
    None of these establishes that an issuer did not disclose an economic item.
    Discover actual tags with ``filed_concepts`` and inspect the original filing
    when a query cannot resolve a required input. No network call or arithmetic
    occurs, and an unresolved selection never supplies a usable fact or zero.
    """
    if not isinstance(payload, Mapping) or not isinstance(selections, Mapping) or not selections:
        raise ValueError("supply a frozen Notes response and nonempty named selectors")
    coverage = {key: deepcopy(payload.get(key)) for key in (
        "source_id", "queryId", "datasetSnapshotId", "normalized_query", "status",
        "truncated", "filingPlanTruncated", "selectedArchiveIds", "eligibleFilings", "limitations")}
    rows = payload.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("Notes response requires fact rows")
    result = {}
    for name, selector in selections.items():
        _validate_fact_selector(name, selector)
        inspection = {"selector": deepcopy(dict(selector)), "query_coverage": deepcopy(coverage),
                      "source_absence_established": False}
        if payload.get("status") == "source_not_covered":
            result[name] = {**inspection, "status": "source_not_retrieved", "fact": None,
                            "reason": "The lake query does not cover the source; inspect the original filing."}
            continue
        if payload.get("status") in ("source_metadata_incomplete", "query_limit_reached", "truncated_dimension_metadata"):
            result[name] = {**inspection, "status": "incomplete_query", "fact": None,
                            "reason": "Source metadata or query coverage is incomplete; resolve coverage before selection."}
            continue
        if payload.get("truncated") is not False or payload.get("filingPlanTruncated") is not False:
            result[name] = {**inspection, "status": "incomplete_query", "fact": None,
                            "reason": "Query or filing coverage is incomplete; narrow and retrieve before selection."}
            continue
        try:
            fact = select_facts(payload, {name: selector})[name]
        except FactSelectionError as error:
            diagnostic = error.diagnostics[0]
            reason = diagnostic["reason"]
            statuses = {"concept_not_requested": "not_queried", "ambiguous_context": "ambiguous",
                        "fact_id_mismatch": "context_mismatch", "invalid_source_value_or_id": "invalid"}
            # Returned tags/contexts are discovery evidence, never an alias map.
            contexts = [{key: deepcopy(row.get(key)) for key in (
                "tag", "adsh", "ddate", "uom", "qtrs", "dimensions_raw", "fact_id")}
                for row in rows if row.get("tag") == selector.get("tag")]
            status = statuses.get(reason, "context_mismatch" if contexts else "not_found_in_query")
            result[name] = {**inspection, "status": status, "fact": None,
                            "diagnostic": diagnostic, "returned_tag_contexts": contexts,
                            "returned_concepts": sorted({row["tag"] for row in rows if isinstance(row.get("tag"), str)})}
        else:
            result[name] = {**inspection, "status": "selected", "fact": fact}
    return result


def select_facts(
    payload: Mapping[str, Any], selections: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Bind a complete named batch to exact retained Notes rows, without copying IDs.

    Each selector requires ``tag``, ``accession``, ISO ``period_end``, ``unit``,
    integer ``quarters`` and ``dimensions`` (None for consolidated facts or an
    exact raw dimension string). Optional ``fact_id`` narrows an otherwise
    ambiguous reviewed context. No accounting classification or fuzzy matching
    occurs. All failures are returned in FactSelectionError.diagnostics before
    any partial result can be used. A missing row is never numeric zero.

    Results retain the original row, raw value, fact ID and source URL, adding the
    response's source_id and explicit provenance. This local helper preserves
    host evidence; it cannot authenticate a fabricated input payload itself.
    """
    if not isinstance(payload, Mapping) or not isinstance(selections, Mapping) or not selections:
        raise ValueError("supply a frozen Notes response and nonempty named selectors")
    if (payload.get("truncated") is not False or payload.get("filingPlanTruncated") is not False
            or payload.get("status") in ("source_not_covered", "source_metadata_incomplete",
                                         "query_limit_reached", "truncated_dimension_metadata")):
        raise ValueError("Incomplete SEC Notes coverage: narrow and retrieve the exact filing/concepts")
    rows = payload.get("rows")
    source_id = payload.get("source_id")
    query = payload.get("normalized_query")
    if (not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows)
            or not isinstance(source_id, str) or not source_id.strip()
            or not isinstance(query, Mapping)):
        raise ValueError("Notes response requires rows, source_id and normalized_query")
    concepts = query.get("concepts")
    if concepts is not None and (not isinstance(concepts, list) or
                                not all(isinstance(tag, str) for tag in concepts)):
        raise ValueError("normalized_query.concepts must be a list or null")
    resolved, errors = {}, []
    for name, selector in selections.items():
        _validate_fact_selector(name, selector)
        period = _required_as_of(selector["period_end"]).replace("-", "")
        quarters, dimensions = selector["quarters"], selector["dimensions"]
        fact_id = selector.get("fact_id")
        diagnostic = {"name": name, "selector": dict(selector)}
        if concepts is not None and selector["tag"] not in concepts:
            errors.append({**diagnostic, "reason": "concept_not_requested",
                           "requested_concepts": concepts})
            continue
        candidates = [row for row in rows if row.get("tag") == selector["tag"]
                      and row.get("adsh") == selector["accession"] and row.get("ddate") == period]
        matches = [row for row in candidates if row.get("uom") == selector["unit"]
                   and str(row.get("qtrs")) == str(quarters)
                   and (row.get("dimensions_raw") or None) == dimensions]
        before_id = matches
        if fact_id is not None:
            matches = [row for row in matches if row.get("fact_id") == fact_id]
        if len(matches) != 1:
            reason = ("fact_id_mismatch" if before_id and fact_id is not None and not matches
                      else "ambiguous_context" if len(matches) > 1 else "no_matching_context")
            errors.append({**diagnostic, "reason": reason,
                           "available_contexts": [{field: row.get(field) for field in
                               ("fact_id", "uom", "qtrs", "dimensions_raw", "taxonomy")}
                               for row in candidates]})
            continue
        row = matches[0]
        try:
            if not isinstance(row.get("value_raw"), str):
                raise ValueError("Notes value_raw must retain its decimal string")
            value = Decimal(row["value_raw"])
            valid = value.is_finite()
        except (KeyError, InvalidOperation, TypeError, ValueError):
            valid = False
        if not valid or not isinstance(row.get("fact_id"), str) or not row["fact_id"]:
            errors.append({**diagnostic, "reason": "invalid_source_value_or_id"})
            continue
        selected = deepcopy(dict(row))
        selected["source_id"] = source_id
        selected["provenance"] = {"sourceIds": [source_id], "fact_id": row["fact_id"],
                                  "accession": row["adsh"], "period_end": selector["period_end"],
                                  "unit": row["uom"], "source_url": row.get("source_url")}
        resolved[name] = selected
    if errors:
        raise FactSelectionError(errors)
    return resolved


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


def _notes_payload(
    *,
    as_of: str | None,
    period_end_from: str | None,
    period_end_to: str | None,
    filing_period_from: str | None,
    filing_period_to: str | None,
    forms: Sequence[str] | None,
    max_filings: int | None,
    limit: int | None,
) -> dict[str, Any]:
    normalized_from = _validated_as_of(period_end_from)
    normalized_to = _validated_as_of(period_end_to)
    if normalized_from is not None and normalized_to is not None and normalized_from > normalized_to:
        raise ValueError("period_end_from cannot follow period_end_to")
    normalized_filing_from = _validated_as_of(filing_period_from)
    normalized_filing_to = _validated_as_of(filing_period_to)
    if normalized_filing_from is not None and normalized_filing_to is not None and normalized_filing_from > normalized_filing_to:
        raise ValueError("filing_period_from cannot follow filing_period_to")
    normalized_forms = _validated_string_list(
        forms,
        field="forms",
        maximum=_MAX_FORMS,
        pattern=_FORM_PATTERN,
        required=False,
    )
    payload: dict[str, Any] = {"as_of": _required_as_of(as_of)}
    if normalized_from is not None:
        payload["period_end_from"] = normalized_from
    if normalized_to is not None:
        payload["period_end_to"] = normalized_to
    if normalized_filing_from is not None:
        payload["filing_period_from"] = normalized_filing_from
    if normalized_filing_to is not None:
        payload["filing_period_to"] = normalized_filing_to
    if normalized_forms is not None:
        payload["forms"] = normalized_forms
    normalized_max_filings = _validated_positive_integer(
        max_filings,
        field="max_filings",
        maximum=_MAX_FILINGS,
    )
    if normalized_max_filings is not None:
        payload["max_filings"] = normalized_max_filings
    normalized_limit = _validated_positive_integer(limit, field="limit", maximum=_MAX_ROWS)
    if normalized_limit is not None:
        payload["limit"] = normalized_limit
    return payload


def fact_instances(
    *,
    ticker: str,
    as_of: str,
    concepts: Sequence[str] | None = None,
    period_end_from: str | None = None,
    period_end_to: str | None = None,
    filing_period_from: str | None = None,
    filing_period_to: str | None = None,
    forms: Sequence[str] | None = None,
    dimensional: DimensionalFilter = "all",
    max_filings: int | None = _MAX_FILINGS,
    limit: int | None = None,
    request_id: str | None = None,
    _exit: bool = True,
) -> dict[str, Any]:
    """Return exact numeric filing facts from the pinned SEC Notes snapshot.

    Rows retain fact IDs, accession, concept, period, unit, raw dimensions,
    availability, immutable snapshot identity, and the exact SEC filing URL.
    No web fallback or semantic segment classification occurs.
    """
    if dimensional not in ("all", "only", "none"):
        raise ValueError("dimensional must be 'all', 'only', or 'none'")
    payload = _notes_payload(
        as_of=as_of,
        period_end_from=period_end_from,
        period_end_to=period_end_to,
        filing_period_from=filing_period_from,
        filing_period_to=filing_period_to,
        forms=forms,
        max_filings=max_filings,
        limit=limit,
    )
    normalized_concepts = _validated_string_list(
        concepts,
        field="concepts",
        maximum=_MAX_CONCEPTS,
        pattern=_CONCEPT_PATTERN,
        required=False,
    )
    if normalized_concepts is not None:
        payload["concepts"] = normalized_concepts
    payload["dimensional"] = dimensional
    return _run_payload(
        action="fact_instances",
        ticker=_normalized_ticker(ticker),
        payload=payload,
        request_id=request_id,
        _exit=_exit,
    )


def filed_concepts(
    *,
    ticker: str,
    as_of: str,
    name_contains: str | None = None,
    period_end_from: str | None = None,
    period_end_to: str | None = None,
    filing_period_from: str | None = None,
    filing_period_to: str | None = None,
    forms: Sequence[str] | None = None,
    max_filings: int | None = 1,
    limit: int | None = None,
    request_id: str | None = None,
    _exit: bool = True,
) -> dict[str, Any]:
    """Find exact numeric filing tags, including facts without dimensions.

    Search is over filed tag names and labels, not prose extraction or semantic
    classification. The response retains filing and snapshot identity. Query one
    filing window when researching one filing, then use an exact returned tag
    with ``fact_instances`` to inspect its raw values and units.
    """
    payload = _notes_payload(
        as_of=as_of,
        period_end_from=period_end_from,
        period_end_to=period_end_to,
        filing_period_from=filing_period_from,
        filing_period_to=filing_period_to,
        forms=forms,
        max_filings=max_filings,
        limit=limit,
    )
    if name_contains is not None:
        if (not isinstance(name_contains, str) or
                not 1 <= len(name_contains.strip()) <= 128 or
                any(ord(char) < 32 or ord(char) == 127 for char in name_contains)):
            raise ValueError("name_contains must be 1 through 128 printable characters")
        payload["name_contains"] = name_contains.strip()
    return _run_payload(
        action="filed_concepts",
        ticker=_normalized_ticker(ticker),
        payload=payload,
        request_id=request_id,
        _exit=_exit,
    )


def dimensioned_concepts(
    *,
    ticker: str,
    as_of: str,
    period_end_from: str | None = None,
    period_end_to: str | None = None,
    filing_period_from: str | None = None,
    filing_period_to: str | None = None,
    forms: Sequence[str] | None = None,
    max_filings: int | None = _MAX_FILINGS,
    limit: int | None = None,
    request_id: str | None = None,
    _exit: bool = True,
) -> dict[str, Any]:
    """Discover exact filer tags that carry dimensional facts.

    Discovery reports the filed grain and labels without asserting that any
    axis is a reportable segment.
    """
    return _run_payload(
        action="dimensioned_concepts",
        ticker=_normalized_ticker(ticker),
        payload=_notes_payload(
            as_of=as_of,
            period_end_from=period_end_from,
            period_end_to=period_end_to,
            filing_period_from=filing_period_from,
            filing_period_to=filing_period_to,
            forms=forms,
            max_filings=max_filings,
            limit=limit,
        ),
        request_id=request_id,
        _exit=_exit,
    )


def business_breakdowns(
    *,
    ticker: str,
    as_of: str,
    concepts: Sequence[str],
    period_end_from: str | None = None,
    period_end_to: str | None = None,
    filing_period_from: str | None = None,
    filing_period_to: str | None = None,
    forms: Sequence[str] | None = None,
    max_filings: int | None = _MAX_FILINGS,
    limit: int | None = None,
    request_id: str | None = None,
    _exit: bool = True,
) -> dict[str, Any]:
    """Return filed dimensional facts for exact discovered concepts.

    A successful row set has status ``unreconciled`` until executable analysis
    selects a non-overlapping disclosure set and reconciles it to a matching
    consolidated fact. The SDK never labels every SEC dimension as a segment.
    """
    payload = _notes_payload(
        as_of=as_of,
        period_end_from=period_end_from,
        period_end_to=period_end_to,
        filing_period_from=filing_period_from,
        filing_period_to=filing_period_to,
        forms=forms,
        max_filings=max_filings,
        limit=limit,
    )
    payload["concepts"] = _validated_string_list(
        concepts,
        field="concepts",
        maximum=_MAX_CONCEPTS,
        pattern=_CONCEPT_PATTERN,
        required=True,
    )
    return _run_payload(
        action="business_breakdowns",
        ticker=_normalized_ticker(ticker),
        payload=payload,
        request_id=request_id,
        _exit=_exit,
    )


__all__ = [
    "Cadence",
    "DimensionalFilter",
    "FACT_ROLES",
    "FactRole",
    "FactSelectionError",
    "business_breakdowns",
    "dimensioned_concepts",
    "fact_candidates",
    "fact_instances",
    "filed_concepts",
    "latest_statement",
    "resolved_fact",
    "select_facts",
    "statement",
]
