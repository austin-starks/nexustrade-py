"""Point-in-time SEC statements and auditable filing-fact candidates."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from nexustrade import host

Cadence = Literal["annual", "quarterly"]
FactRole = Literal[
    "depreciation_and_amortization",
    "capital_expenditures",
    "operating_cash_flow",
    "current_operating_assets",
    "current_operating_liabilities",
]

FACT_ROLES: tuple[FactRole, ...] = (
    "depreciation_and_amortization",
    "capital_expenditures",
    "operating_cash_flow",
    "current_operating_assets",
    "current_operating_liabilities",
)

_TICKER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9.-]{0,14}$")
_MAX_PERIODS = 40


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
        host.queue_sec(
            rid,
            action=action,
            ticker=ticker,
            periods=periods,
            cadence=cadence,
            as_of=as_of,
            roles=roles,
        )
        host.flush_requests()
        if _exit:
            raise SystemExit(0)
        return {}
    if not result.get("ok"):
        raise RuntimeError(f"sec.{action}({ticker!r}) failed: {result.get('error')}")
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"sec.{action}({ticker!r}) returned an invalid payload")
    return data


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
    "statement",
]
