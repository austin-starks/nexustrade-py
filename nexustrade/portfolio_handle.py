"""Hand-written Portfolio handle — save / deploy / backtest.

Lives next to generated ``portfolio.py`` (builders). ``nt.portfolio(...)``
returns this class. It subclasses ``dict`` so ``portfolio["name"]``,
``json.dumps(portfolio)``, and ``isinstance(portfolio, dict)`` keep working.
``id`` is an attribute, not a dict key, so it never leaks into request bodies.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Optional, TypedDict, cast

__all__ = [
    "DeployResult",
    "Portfolio",
    "PortfolioIndustryFilter",
    "PortfolioList",
    "PortfolioPolicy",
    "PortfolioStockEligibility",
    "PortfolioAutomatedApproval",
]


class PortfolioIndustryFilter(TypedDict):
    """Read-only industry eligibility snapshot returned by the API."""

    mode: Literal["ALL", "INCLUDE_ONLY"]
    match: Literal["ANY", "ALL"]
    industries: list[str]


class PortfolioStockEligibility(TypedDict):
    """Dynamic stock and option-underlying eligibility rules."""

    minimumMarketCapUsd: int
    maximumMarketCapUsd: int | None
    industryFilter: PortfolioIndustryFilter
    # INCLUDE keeps names with no known market cap (ETFs, unsized filers); the
    # politician copy bots use it.
    missingMarketCapBehavior: Literal["EXCLUDE", "INCLUDE"]
    # ONE_PER_COMPANY keeps one share class per company in a selection; ALL_CLASSES allows
    # pairs such as GOOG/GOOGL.
    shareClassBehavior: Literal["ONE_PER_COMPANY", "ALL_CLASSES"]
    missingIndustryBehavior: Literal["EXCLUDE_WHEN_FILTER_SET"]
    appliesTo: Literal["DYNAMIC_STOCK_UNIVERSES"]


class PortfolioAutomatedApproval(TypedDict):
    """Read-only automation configuration returned with a portfolio."""

    enabled: bool
    maxAutomatedTradesPerDay: int
    countingUnit: Literal["TRADE_ACTION"]
    dailyWindow: Literal["AMERICA_NEW_YORK_CALENDAR_DAY"]


class PortfolioPolicy(TypedDict):
    """Server trading policy. Authoring sends its stockEligibility and never its automation."""

    schemaVersion: Literal[2]
    revision: int
    stockEligibility: PortfolioStockEligibility
    automatedApproval: PortfolioAutomatedApproval


_AUTOMATION_POLICY_KEYS = ("automatedApproval", "automationAcknowledged")
_AUTHORED_ELIGIBILITY_KEYS = (
    "minimumMarketCapUsd",
    "maximumMarketCapUsd",
    "industryFilter",
    "missingMarketCapBehavior",
    "shareClassBehavior",
)


def _authored_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a policy an author may send: stock eligibility, never automation.

    A fetched server policy (it carries ``schemaVersion``) is reduced to its
    authorable eligibility fields, so a copy keeps its eligibility. Anything
    else must be exactly ``{"stockEligibility": {...}}``.
    """
    if "schemaVersion" not in policy and any(
        key in policy for key in _AUTOMATION_POLICY_KEYS
    ):
        raise ValueError(
            "Automated trading can only be changed by the portfolio owner in the "
            "NexusTrade UI; an authored policy may set only stockEligibility."
        )
    stock = policy.get("stockEligibility")
    if not isinstance(stock, Mapping):
        raise ValueError("An authored policy must be {'stockEligibility': {...}}.")
    if "schemaVersion" in policy:
        return {
            "stockEligibility": {
                key: stock[key] for key in _AUTHORED_ELIGIBILITY_KEYS if key in stock
            }
        }
    if set(policy) != {"stockEligibility"}:
        raise ValueError(
            "An authored policy must be {'stockEligibility': {...}} and nothing else."
        )
    return {"stockEligibility": dict(stock)}


class DeployResult(dict):
    """Outcome of ``Portfolio.deploy()``.

    ``portfolio_id`` is the *real* paper/live id (what Rust trades). It is
    different from the draft ``Portfolio.id`` returned by ``save()``.
    """

    @property
    def portfolio_id(self) -> str:
        return str(self["portfolioId"])

    @property
    def chat_portfolio_id(self) -> str | None:
        value = self.get("chatPortfolioId")
        return str(value) if value else None

    @property
    def name(self) -> str:
        return str(self.get("name") or "")

    @property
    def outcome(self) -> str:
        return str(self.get("outcome") or "")


class PortfolioList(dict):
    """Paginated ``list_portfolios`` response with ``portfolios`` as handles."""

    @property
    def portfolios(self) -> list["Portfolio"]:
        rows = self.get("portfolios")
        return rows if isinstance(rows, list) else []


class Portfolio(dict):
    """Authored or fetched portfolio. Same class either way."""

    __slots__ = ("id", "_client")

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        id: str | None = None,
        client: Any = None,
    ) -> None:
        payload = dict(data or {})
        # Prefer an explicit id; otherwise absorb wire ids without keeping them
        # as dict keys (they must not round-trip into create/backtest bodies).
        wire_id = payload.pop("portfolioId", None) or payload.pop("id", None)
        if isinstance(payload.get("policy"), Mapping):
            # Refuse an authored automation field at construction, not at save.
            _authored_policy(payload["policy"])
        super().__init__(payload)
        object.__setattr__(self, "id", id or (str(wire_id) if wire_id else None))
        object.__setattr__(self, "_client", client)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in Portfolio.__slots__:
            object.__setattr__(self, name, value)
            return
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def __repr__(self) -> str:
        return f"Portfolio({dict(self)!r})"

    @property
    def policy(self) -> PortfolioPolicy | None:
        value = self.get("policy")
        return cast(PortfolioPolicy, value) if isinstance(value, Mapping) else None

    @property
    def authored_policy(self) -> dict[str, Any] | None:
        """Stock eligibility this portfolio will send; automated trading is never sent."""
        value = self.get("policy")
        return _authored_policy(value) if isinstance(value, Mapping) else None

    def set_stock_eligibility(self, stock_eligibility: Mapping[str, Any]) -> "Portfolio":
        """Replace the stock eligibility this portfolio will send. Omitted fields take the defaults."""
        policy = {"stockEligibility": dict(stock_eligibility)}
        _authored_policy(policy)
        self["policy"] = policy
        return self

    def _authoring_payload(self) -> dict[str, Any]:
        payload = dict(self)
        payload.pop("policy", None)
        authored = self.authored_policy
        if authored is not None:
            payload["policy"] = authored
        return payload

    def _resolve_client(self, client: Any = None) -> Any:
        if client is not None:
            return client
        existing = object.__getattribute__(self, "_client")
        if existing is not None:
            return existing
        from nexustrade.client import NexusTradeClient

        return NexusTradeClient.from_environment()

    def save(
        self,
        *,
        idempotency_key: str,
        client: Any = None,
    ) -> "Portfolio":
        """Persist this portfolio as a chat draft. Sets ``.id`` to the ChatPortfolio id."""
        resolved = self._resolve_client(client)
        result = resolved.create_portfolio(
            self._authoring_payload(),
            idempotency_key=idempotency_key,
        )
        portfolio_id = result.get("portfolioId")
        if not isinstance(portfolio_id, str) or not portfolio_id:
            from nexustrade.client import NexusTradeApiError, _NO_HTTP_STATUS

            raise NexusTradeApiError(
                _NO_HTTP_STATUS,
                "invalid_response",
                "Portfolio save response is missing portfolioId.",
            )
        self.id = portfolio_id
        self._client = resolved
        return self

    def deploy(
        self,
        *,
        frequency: Optional[str] = None,
        client: Any = None,
    ) -> DeployResult:
        """Mint/activate the real paper portfolio. Returns a different id than ``save()``."""
        if not self.id:
            raise ValueError("Portfolio must be saved before deploy().")
        resolved = self._resolve_client(client)
        result = resolved.deploy(self.id, frequency=frequency)
        return DeployResult(result)

    def undeploy(self, *, client: Any = None) -> dict[str, Any]:
        """Deactivate this portfolio's deployment(s)."""
        if not self.id:
            raise ValueError("Portfolio must be saved before undeploy().")
        resolved = self._resolve_client(client)
        return resolved.undeploy(self.id)

    def backtest(
        self,
        *,
        start_date: str,
        end_date: str,
        idempotency_key: str,
        baseline_symbol: Optional[str] = None,
        interval: Optional[str] = None,
        initial_value: Optional[float] = None,
        generate_events: Optional[bool] = None,
        fee_config: Optional[Mapping[str, Any]] = None,
        client: Any = None,
    ) -> dict[str, Any]:
        """Submit a backtest. Uses ``portfolioId`` once saved; otherwise sends the body."""
        resolved = self._resolve_client(client)
        if self.id:
            body: dict[str, Any] = {
                "portfolioId": self.id,
                "startDate": start_date,
                "endDate": end_date,
            }
        else:
            body = {
                "portfolio": self._authoring_payload(),
                "startDate": start_date,
                "endDate": end_date,
            }
        if baseline_symbol is not None:
            body["baseline"] = baseline_symbol
        if interval is not None:
            body["interval"] = interval
        if initial_value is not None:
            body["initialValue"] = initial_value
        if generate_events is not None:
            body["generateEvents"] = generate_events
        if fee_config is not None:
            body["feeConfig"] = dict(fee_config)
        return resolved.create_backtest(body, idempotency_key=idempotency_key)
