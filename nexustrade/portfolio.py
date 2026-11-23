# GENERATED from the NexusTrade builder spec — do not edit by hand.

"""NexusTrade strategy authoring. Generated — do not edit by hand."""

from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from nexustrade.portfolio_handle import Portfolio

__all__: List[str] = [
    "CANDIDATE",
    "PORTFOLIO_WIDE",
    "Indicator",
    "Condition",
    "Portfolio",
    "at_least",
    "at_most",
    "fewer_than",
    "exactly",
    "multi",
    "andThen",
    "always",
    "stock_asset",
    "crypto_asset",
    "filter",
    "select_top",
    "select_percentile",
    "universe",
    "buy",
    "sell",
    "short",
    "cover",
    "alert",
    "launch_agent",
    "update_watchlist",
    "update_institutional_watchlist",
    "update_insider_watchlist",
    "update_screener_watchlist",
    "screen_column",
    "screen_price",
    "screen_bar_volume",
    "screen_bar_dollar_volume",
    "screen_disclosure",
    "screen_compare",
    "screen_all",
    "screen_any",
    "screen_top",
    "screen_percentile",
    "watchlist_universe",
    "dynamic_rebalance",
    "mean_variance_allocation",
    "risk_parity_allocation",
    "maximum_diversification_allocation",
    "volatility_target",
    "rebalance_allocation_drift",
    "rebalance_planned_turnover",
    "rebalance_estimated_cost",
    "rebalance_expected_benefit",
    "rebalance_net_benefit",
    "leg",
    "options_builder",
    "against_parent",
    "open_option",
    "structure_template",
    "sleeve",
    "rebalance_option",
    "pnl_trigger",
    "dte_trigger",
    "days_held_trigger",
    "greeks_trigger",
    "close_option",
    "limit_order",
    "current_limit",
    "unit_price_limit",
    "maximum_net_debit",
    "minimum_net_credit",
    "good_for_day",
    "good_for_minutes",
    "session_flatten",
    "strategy",
    "portfolio",
]


class _Candidate:
    """Bind to the current pipeline candidate (emits NO targetAsset).

    The JSON path expresses this by *omitting* a field. Here it is a value, so
    the intent is written down instead of inferred from an absence.
    """

    def __repr__(self) -> str:
        return "CANDIDATE"


class _PortfolioWide:
    """Explicit empty targetAssets: [] — portfolio-wide scope.

    Correct for rebalance / DynamicRebalance cadence. Wrong for per-ticker DCA.
    """

    def __repr__(self) -> str:
        return "PORTFOLIO_WIDE"


CANDIDATE = _Candidate()
PORTFOLIO_WIDE = _PortfolioWide()


def _asset(a: Union[str, Dict[str, Any], _Candidate, _PortfolioWide]) -> Optional[Dict[str, Any]]:
    if isinstance(a, (_Candidate, _PortfolioWide)):
        return None
    if isinstance(a, dict):
        ticker = a.get("symbol") or a.get("name") or ""
        out = dict(a)
        if ticker:
            out.setdefault("symbol", ticker)
            out.setdefault("name", ticker)
        return out
    if not isinstance(a, str) or not a.strip():
        raise TypeError(
            f"expected a ticker string, an asset dict, CANDIDATE, or PORTFOLIO_WIDE; got {a!r}"
        )
    # FormGroup.targetAssets reads name; targetAsset reads symbol — set both.
    return {"type": "Stock", "symbol": a, "name": a}


def _set_asset(d: Dict[str, Any], key: str, a: Any) -> None:
    if isinstance(a, _PortfolioWide):
        return
    resolved = _asset(a)
    if resolved is not None:
        d[key] = resolved


def _set_assets(d: Dict[str, Any], key: str, items: Any) -> None:
    if items is None:
        return
    if isinstance(items, _PortfolioWide) or items == []:
        d[key] = []
        return
    if isinstance(items, (str, dict, _Candidate)):
        items = [items]
    if any(isinstance(x, _PortfolioWide) for x in items):
        d[key] = []
        return
    d[key] = [a for a in (_asset(x) for x in items) if a is not None]


def _enum(value: Any, allowed: Sequence[str], name: str) -> Any:
    """Fail in-process on a bad enum. The compiler does this for the TS SDK."""
    if value is not None and value not in allowed:
        raise ValueError(
            f"{name}={value!r} is not valid. Allowed: {', '.join(map(str, allowed))}"
        )
    return value


def _require(value: Any, name: str) -> Any:
    if value is None:
        raise TypeError(f"{name} is required")
    return value


_COMPARATORS = {
    "gt": "greaterThan",
    "lt": "lessThan",
    "ge": "greaterThanOrEqual",
    "le": "lessThanOrEqual",
    "eq": "equal",
    "ne": "notEqual",
}


class Indicator:
    """One IIndicator. Comparisons build conditions; arithmetic builds compounds."""

    __slots__ = ("d",)

    def __init__(self, d: Dict[str, Any]) -> None:
        self.d = d

    def to_dict(self) -> Dict[str, Any]:
        return self.d

    def __repr__(self) -> str:
        return f"Indicator({self.d.get('type')})"

    def _cmp(self, other: Any, op: str) -> "Condition":
        rhs = other if isinstance(other, Indicator) else Value(other)
        return Condition(
            {"type": "Base", "lhs": self.d, "comparison": _COMPARATORS[op], "rhs": rhs.d}
        )

    def __gt__(self, o: Any) -> "Condition":
        return self._cmp(o, "gt")

    def __lt__(self, o: Any) -> "Condition":
        return self._cmp(o, "lt")

    def __ge__(self, o: Any) -> "Condition":
        return self._cmp(o, "ge")

    def __le__(self, o: Any) -> "Condition":
        return self._cmp(o, "le")

    def __eq__(self, o: Any) -> "Condition":  # type: ignore[override]
        return self._cmp(o, "eq")

    def __ne__(self, o: Any) -> "Condition":  # type: ignore[override]
        return self._cmp(o, "ne")

    __hash__ = None  # type: ignore[assignment]

    def _bin(self, other: Any, kind: str) -> "Indicator":
        rhs = other if isinstance(other, Indicator) else Value(other)
        return Indicator({"type": kind, "indicators": [self.d, rhs.d]})

    def __add__(self, o: Any) -> "Indicator":
        return self._bin(o, "Plus")

    def __sub__(self, o: Any) -> "Indicator":
        return self._bin(o, "Minus")

    def __mul__(self, o: Any) -> "Indicator":
        return self._bin(o, "Multiply")

    def __truediv__(self, o: Any) -> "Indicator":
        return self._bin(o, "Divide")


class Condition:
    """A condition tree. Combine with & and |."""

    __slots__ = ("d",)

    def __init__(self, d: Dict[str, Any]) -> None:
        self.d = d

    def to_dict(self) -> Dict[str, Any]:
        return self.d

    def __repr__(self) -> str:
        return f"Condition({self.d.get('type')})"

    def __and__(self, o: "Condition") -> "Condition":
        return Condition({"type": "And", "conditions": [self.d, o.d]})

    def __or__(self, o: "Condition") -> "Condition":
        return Condition({"type": "Or", "conditions": [self.d, o.d]})


def at_least(count: int, *conditions: Condition) -> Condition:
    """Multi: true when at least `count` of `conditions` hold."""
    return Condition(
        {
            "type": "Multi",
            "comparison": "greaterThanOrEqual",
            "value": count,
            "conditions": [c.d for c in conditions],
        }
    )


def at_most(count: int, *conditions: Condition) -> Condition:
    """Multi: true when at most `count` of `conditions` hold."""
    return Condition(
        {
            "type": "Multi",
            "comparison": "lessThanOrEqual",
            "value": count,
            "conditions": [c.d for c in conditions],
        }
    )


def fewer_than(count: int, *conditions: Condition) -> Condition:
    """Multi: true when fewer than `count` of `conditions` hold."""
    return Condition(
        {
            "type": "Multi",
            "comparison": "lessThan",
            "value": count,
            "conditions": [c.d for c in conditions],
        }
    )


def exactly(count: int, *conditions: Condition) -> Condition:
    """Multi: true when exactly `count` of `conditions` hold."""
    return Condition(
        {
            "type": "Multi",
            "comparison": "equal",
            "value": count,
            "conditions": [c.d for c in conditions],
        }
    )


def multi(count: int, comparison: str, *conditions: Condition) -> Condition:
    """Multi with an explicit Comparator string (greaterThanOrEqual, lessThan, …)."""
    return Condition(
        {
            "type": "Multi",
            "comparison": comparison,
            "value": count,
            "conditions": [c.d for c in conditions],
        }
    )


def andThen(
    length: Union[int, dict],
    interval: Union[Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"], Condition],
    *conditions: Condition,
) -> Condition:
    """A SEQUENCE, not a simultaneity.

    `andThen(30, "Minute", a, b)` is true at tick t when b is true at t and a
    was true at a STRICTLY EARLIER tick in the preceding 30 minutes. Use `&`
    when both should hold at once. With three or more steps the window applies
    per transition, so each step must occur within it of the step before.

    Emits wire type "Then", the way at_least/at_most/exactly emit "Multi". The
    name avoids `then` so the TypeScript half can share it: a module namespace
    with a callable `then` export is assimilated as a thenable.

    length: how far back the previous step may have fired
    interval: Day, Hour or Minute — REQUIRED, because a silent Day default
        would kill an intraday setup
    """
    if isinstance(length, dict):
        return Condition({"type": "Then", **length,
            "conditions": [_condition_dict(c) for c in (interval, *conditions)]})
    return Condition(
        {
            "type": "Then",
            "conditions": [_condition_dict(c) for c in conditions],
            "window": {
                "length": length,
                "interval": _enum(interval, ["Day", "Hour", "Minute", "TradingDay", "TradingMinute"], "interval"),
            },
        }
    )


def always() -> Condition:
    """Always-true gate for strategies whose cadence lives in the pipeline."""
    return Value(1) > Value(0)


def _compact(value: Dict[str, Any]) -> Dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _indicator_dict(value: Indicator) -> Dict[str, Any]:
    if not isinstance(value, Indicator):
        raise TypeError(f"expected Indicator, got {value!r}")
    return value.d


def _deployment_budget(value: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("total_budget must be a dict")
    if value.get("type") not in ("percent of portfolio", "dollars"):
        raise ValueError("total_budget.type must be percent of portfolio or dollars")
    return {**value, "amount": _indicator_dict(value.get("amount"))}


def _condition_dict(value: Condition) -> Dict[str, Any]:
    if not isinstance(value, Condition):
        raise TypeError(f"expected Condition, got {value!r}")
    return value.d


def stock_asset(symbol: str) -> Dict[str, Any]:
    """Build a concrete equity/ETF asset."""
    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("stock_asset requires a symbol")
    return {"type": "Stock", "symbol": ticker, "name": ticker}


_CRYPTO_ALIASES = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "DOGE": "DOGE-USD",
    "ADA": "ADA-USD",
    "XRP": "XRP-USD",
    "AVAX": "AVAX-USD",
    "LINK": "LINK-USD",
    "DOT": "DOT-USD",
    "MATIC": "MATIC-USD",
}


def crypto_asset(symbol: str) -> Dict[str, Any]:
    """Build a concrete cryptocurrency asset (BTC becomes BTC-USD)."""
    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("crypto_asset requires a symbol")
    normalized = _CRYPTO_ALIASES.get(
        ticker,
        ticker if ticker.endswith("-USD") else f"{ticker}-USD",
    )
    return {
        "type": "Cryptocurrency",
        "symbol": normalized,
        "name": normalized,
    }


def filter(condition: Condition) -> Dict[str, Any]:
    return {"type": "Filter", "condition": _condition_dict(condition)}


def select_top(
    metric: Indicator,
    limit: int,
    direction: Literal["Highest", "Lowest"] = "Highest",
) -> Dict[str, Any]:
    return {
        "type": "SelectTop",
        "metric": _indicator_dict(metric),
        "limit": limit,
        "direction": _enum(direction, ["Highest", "Lowest"], "direction"),
    }


def select_percentile(
    metric: Indicator,
    percentile: float,
    direction: Literal["Highest", "Lowest"] = "Highest",
) -> Dict[str, Any]:
    return {
        "type": "SelectPercentile",
        "metric": _indicator_dict(metric),
        "percentile": percentile,
        "direction": _enum(direction, ["Highest", "Lowest"], "direction"),
    }


def universe(
    source: Literal[
        "ALL_US_STOCKS",
        "SP500",
        "NASDAQ100",
        "DJIA",
        "CRYPTO",
        "SPECIFIC_ASSETS",
    ],
    assets: Optional[Sequence[Union[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    _enum(
        source,
        [
            "ALL_US_STOCKS",
            "SP500",
            "NASDAQ100",
            "DJIA",
            "CRYPTO",
            "SPECIFIC_ASSETS",
        ],
        "source",
    )
    if source == "SPECIFIC_ASSETS":
        if not assets:
            raise ValueError("SPECIFIC_ASSETS requires at least one asset")
        return {"source": source, "assets": [_asset(item) for item in assets]}
    if assets is not None:
        raise ValueError(f"{source} does not accept explicit assets")
    return {"source": source}


def buy(
    target: Union[str, Dict[str, Any]],
    amount: float,
    allocation_type: str = "percent of portfolio",
    *, execution_priority: Optional[int] = None, funding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    options = {}
    if execution_priority is not None: options["executionPriority"] = execution_priority
    if funding is not None:
        options["funding"] = {key: value.d if isinstance(value, (Condition, Indicator)) else value
                              for key, value in funding.items()}
    return {
        **options,
        "type": "Buy",
        "targetAsset": _asset(target),
        "amount": {"type": allocation_type, "amount": amount},
    }


def sell(
    target: Union[str, Dict[str, Any]],
    amount: float,
    allocation_type: str = "percent of portfolio",
    *, execution_priority: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        **({"executionPriority": execution_priority} if execution_priority is not None else {}),
        "type": "Sell",
        "targetAsset": _asset(target),
        "amount": {"type": allocation_type, "amount": amount},
    }


def short(
    target: Union[str, Dict[str, Any]],
    amount: float,
    allocation_type: str = "number of assets",
    *, execution_priority: Optional[int] = None,
) -> Dict[str, Any]:
    """Sell-to-open one stock. Never sized as percent of current positions; refused while long."""
    return {
        **({"executionPriority": execution_priority} if execution_priority is not None else {}),
        "type": "Short",
        "targetAsset": _asset(target),
        "amount": {"type": allocation_type, "amount": amount},
    }


def cover(
    target: Union[str, Dict[str, Any]],
    amount: float = 100,
    allocation_type: str = "percent of current positions",
    *, execution_priority: Optional[int] = None,
) -> Dict[str, Any]:
    """Buy back an open stock short; capped at the short, never flips long."""
    return {
        **({"executionPriority": execution_priority} if execution_priority is not None else {}),
        "type": "Cover",
        "targetAsset": _asset(target),
        "amount": {"type": allocation_type, "amount": amount},
    }


def sell_holdings(condition: Condition, amount: float = 100,
                  allocation_type: str = "percent of current positions", *, execution_priority: Optional[int] = None) -> Dict[str, Any]:
    action = sell(stock_asset("__PLACEHOLDER__"), amount, allocation_type, execution_priority=execution_priority)
    action["holdingCondition"] = condition.d
    return action


def alert(message: str) -> Dict[str, Any]:
    return {"type": "Alert", "message": message}


def launch_agent(
    *,
    planning_model: str,
    execution_model: str,
    initial_message: str,
    max_iterations: int,
    include_market_data: bool,
    continue_existing: bool,
    skip_planning: bool,
    cooldown_minutes: Optional[int] = None,
    watchlist_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "type": "LaunchAgent",
            "planningModel": planning_model,
            "executionModel": execution_model,
            "initialMessage": initial_message,
            "maxIterations": max_iterations,
            "includeMarketData": include_market_data,
            "continueExisting": continue_existing,
            "skipPlanning": skip_planning,
            "cooldownMinutes": cooldown_minutes,
            "watchlistId": watchlist_id,
        }
    )


def watchlist_universe(watchlist_key: str) -> Dict[str, Any]:
    """The latest revision of the list an update_watchlist producer in this portfolio maintains."""
    if not watchlist_key or not watchlist_key.strip():
        raise ValueError("watchlist_universe requires a watchlist_key")
    return {"source": "WATCHLIST", "watchlistKey": watchlist_key}


def update_watchlist(
    *,
    watchlist_key: str,
    member_id: str,
    output: Optional[Dict[str, Any]] = None,
    instrument: Optional[Literal["Equity", "Option", "All"]] = None,
    amount_basis: Optional[Literal["LowerBound", "Midpoint", "UpperBound"]] = None,
    chamber: Optional[Literal["All", "House", "Senate"]] = None,
) -> Dict[str, Any]:
    """Keep a portfolio-scoped list set to every security where a member of
    Congress still holds disclosed purchases. Refreshes every tick, places no
    orders and takes no condition. Live and paper wait for durable publication."""
    return {
        "type": "UpdateWatchlist",
        "watchlistKey": watchlist_key,
        **({"output": output} if output is not None else {}),
        "source": _compact(
            {
                "type": "PoliticalRemainingPurchases",
                "memberId": member_id,
                "instrument": instrument,
                "amountBasis": amount_basis,
                "chamber": chamber,
            }
        ),
    }


def update_institutional_watchlist(
    *, watchlist_key: str, manager_cik: str, window_days: Optional[int] = None,
    output: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Follow an exact manager's latest public complete effective equity book."""
    return {"type": "UpdateWatchlist", "watchlistKey": watchlist_key,
            **({"output": output} if output is not None else {}),
            "source": _compact({"type": "InstitutionalHoldings", "managerCik": manager_cik,
                                "windowDays": window_days})}


def update_insider_watchlist(
    *, watchlist_key: str, window_days: int,
    role: Optional[Literal["Any", "Officer", "Director", "TenPercentOwner", "OfficerOrDirector", "Ceo"]] = None,
    owner_cik: Optional[str] = None, issuer_cik: Optional[str] = None,
    include_awards: Optional[bool] = None,
    output: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Select qualifying equity purchases (and, with include_awards, issuer awards) within a public-availability window."""
    return {"type": "UpdateWatchlist", "watchlistKey": watchlist_key,
            **({"output": output} if output is not None else {}),
            "source": _compact({"type": "InsiderPurchases", "windowDays": window_days,
                                "role": role, "ownerCik": owner_cik, "issuerCik": issuer_cik,
                                "includeAwards": include_awards})}


def mean_variance_allocation(
    *,
    lookback_periods: int = 252,
    minimum_observations: int = 60,
    risk_aversion: float = 4.0,
    expected_return_shrinkage: float = 0.5,
    covariance_shrinkage: float = 0.25,
    turnover_penalty: float = 1.0,
    estimated_transaction_cost_bps: float = 10.0,
) -> Dict[str, Any]:
    return {
        "type": "MeanVariance",
        "lookbackPeriods": lookback_periods,
        "minimumObservations": minimum_observations,
        "riskAversion": risk_aversion,
        "expectedReturnShrinkage": expected_return_shrinkage,
        "covarianceShrinkage": covariance_shrinkage,
        "turnoverPenalty": turnover_penalty,
        "estimatedTransactionCostBps": estimated_transaction_cost_bps,
    }


def risk_parity_allocation(
    *,
    lookback_periods: int = 252,
    minimum_observations: int = 60,
    covariance_shrinkage: float = 0.25,
    turnover_penalty: float = 1.0,
    estimated_transaction_cost_bps: float = 10.0,
) -> Dict[str, Any]:
    return {
        "type": "RiskParity",
        "lookbackPeriods": lookback_periods,
        "minimumObservations": minimum_observations,
        "covarianceShrinkage": covariance_shrinkage,
        "turnoverPenalty": turnover_penalty,
        "estimatedTransactionCostBps": estimated_transaction_cost_bps,
    }


def maximum_diversification_allocation(
    *,
    lookback_periods: int = 252,
    minimum_observations: int = 60,
    covariance_shrinkage: float = 0.25,
    turnover_penalty: float = 1.0,
    estimated_transaction_cost_bps: float = 10.0,
) -> Dict[str, Any]:
    return {
        "type": "MaximumDiversification",
        "lookbackPeriods": lookback_periods,
        "minimumObservations": minimum_observations,
        "covarianceShrinkage": covariance_shrinkage,
        "turnoverPenalty": turnover_penalty,
        "estimatedTransactionCostBps": estimated_transaction_cost_bps,
    }


def volatility_target(
    *,
    lookback_periods: int = 60,
    minimum_observations: int = 20,
    covariance_shrinkage: float = 0.25,
    target_annualized_volatility_percent: float = 12.0,
    estimated_transaction_cost_bps: float = 10.0,
) -> Dict[str, Any]:
    return {
        "type": "VolatilityTarget",
        "lookbackPeriods": lookback_periods,
        "minimumObservations": minimum_observations,
        "covarianceShrinkage": covariance_shrinkage,
        "targetAnnualizedVolatilityPercent": target_annualized_volatility_percent,
        "estimatedTransactionCostBps": estimated_transaction_cost_bps,
    }


def _rebalance_decision_metric(metric: str) -> Indicator:
    return Indicator({"type": "RebalanceDecisionMetric", "metric": metric})


def rebalance_allocation_drift() -> Indicator:
    return _rebalance_decision_metric("allocationDrift")


def rebalance_planned_turnover() -> Indicator:
    return _rebalance_decision_metric("plannedTurnover")


def rebalance_estimated_cost() -> Indicator:
    return _rebalance_decision_metric("estimatedCost")


def rebalance_expected_benefit() -> Indicator:
    return _rebalance_decision_metric("expectedBenefit")


def rebalance_net_benefit() -> Indicator:
    return _rebalance_decision_metric("netBenefit")



def update_screener_watchlist(*, watchlist_key: str, columns: list[dict[str, Any]],
                             filter: dict[str, Any], refresh_minutes: int,
                             selection: dict[str, Any] | None = None,
                             output: dict[str, Any] | None = None) -> dict[str, Any]:
    """Saved PIT scalar screen. Bar liquidity is known only after its bar is public."""
    return _compact({"type": "UpdateWatchlist", "watchlistKey": watchlist_key, "output": output,
        "source": _compact({"type": "Screener", "columns": columns, "filter": filter,
                            "refreshMinutes": refresh_minutes, "selection": selection})})


def screen_column(key: str, metric: dict[str, Any]) -> dict[str, Any]:
    return {"key": key, "metric": metric}


def screen_price() -> dict[str, Any]:
    return {"type": "ObservedPrice"}


def screen_bar_volume() -> dict[str, Any]:
    """Certified completed historical OHLC volume; live quote/trade volume is unknown."""
    return {"type": "BarVolume"}


def screen_bar_dollar_volume() -> dict[str, Any]:
    return {"type": "BarDollarVolume"}


def screen_disclosure(source: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Disclosure", "source": source}


def screen_compare(field: str, op: str, value: float) -> dict[str, Any]:
    return {"type": "Compare", "field": field, "op": op, "value": value}


def screen_all(*rules: dict[str, Any]) -> dict[str, Any]:
    return {"type": "All", "rules": list(rules)}


def screen_any(*rules: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Any", "rules": list(rules)}


def screen_top(field: str, limit: int, direction: str = "Descending") -> dict[str, Any]:
    return {"type": "Top", "field": field, "direction": direction, "limit": limit}


def screen_percentile(field: str, percentile: float, direction: str = "Descending") -> dict[str, Any]:
    return {"type": "Percentile", "field": field, "direction": direction, "percentile": percentile}


def dynamic_rebalance(
    universe: Optional[Dict[str, Any]] = None,
    pipeline: Optional[Sequence[Dict[str, Any]]] = None,
    weight_indicator: Optional[Indicator] = None,
    *,
    universe_config: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    deployment_percent: Optional[Indicator] = None,
    reserve_option_budget: Optional[Dict[str, Any]] = None,
    per_name_allocation: Optional[Dict[str, Any]] = None,
    can_sell: Optional[Condition] = None,
    allow_shorts: Optional[bool] = None,
    allocation_policy: Optional[Dict[str, Any]] = None,
    exposure_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if universe is not None and universe_config is not None:
        raise ValueError(
            "dynamic_rebalance accepts universe or universe_config, not both"
        )
    resolved_universe = universe if universe is not None else universe_config
    if resolved_universe is None:
        raise ValueError("dynamic_rebalance requires universe")
    if pipeline is None:
        raise ValueError("dynamic_rebalance requires pipeline")
    if weight_indicator is None:
        raise ValueError("dynamic_rebalance requires weight_indicator")
    return _compact(
        {
            "type": "DynamicRebalance",
            "universe": resolved_universe,
            "pipeline": list(pipeline),
            "weightIndicator": _indicator_dict(weight_indicator),
            "limit": limit,
            "deploymentPercent": (
                _indicator_dict(deployment_percent)
                if deployment_percent is not None else None
            ),
            "reserveOptionBudget": (
                _deployment_budget(reserve_option_budget)
                if reserve_option_budget is not None else None
            ),
            "perNameAllocation": per_name_allocation,
            "canSell": (
                _condition_dict(can_sell)
                if can_sell is not None
                else None
            ),
            "allowShorts": allow_shorts,
            "allocationPolicy": allocation_policy,
            "exposurePolicy": exposure_policy,
        }
    )


def leg(
    *,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    min_days_to_expiration: int,
    max_days_to_expiration: int,
    distance: float,
    distance_type: Literal["percent", "dollars", "delta"] = "percent",
    preference: str = "nearest",
    fallback_distance: Optional[float] = None,
    fallback_distance_type: Optional[Literal["percent", "dollars"]] = None,
    fallback_strike_selector: Optional[Dict[str, Any]] = None,
    strike_range: Optional[Dict[str, Any]] = None,
    expiration_range: Optional[Dict[str, Any]] = None,
    ratio: Optional[float] = None,
    greek_filter: Optional[Dict[str, Any]] = None,
    liquidity_filter: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if distance_type == "delta" and (
        fallback_strike_selector is None
        and (
            fallback_distance is None
            or fallback_distance_type is None
        )
    ):
        raise ValueError(
            "delta legs require fallback_distance plus "
            "fallback_distance_type or fallback_strike_selector"
        )
    fallback = fallback_strike_selector
    if fallback is None and fallback_distance is not None:
        fallback = {
            "distanceType": fallback_distance_type or "percent",
            "distance": fallback_distance,
        }
    return _compact(
        {
            "optionType": option_type,
            "direction": direction,
            "expirationSelector": {
                "minDaysToExpiration": min_days_to_expiration,
                "maxDaysToExpiration": max_days_to_expiration,
                "preference": preference,
            },
            "strikeSelector": {
                "distanceType": distance_type,
                "distance": distance,
            },
            "fallbackStrikeSelector": fallback,
            "strikeRange": strike_range,
            "expirationRange": expiration_range,
            "ratio": ratio,
            "greekFilter": greek_filter,
            "liquidityFilter": liquidity_filter,
        }
    )


def options_builder(
    *,
    legs: Sequence[Dict[str, Any]],
    underlying_symbol: Optional[str] = None,
    dynamic_underlying: Optional[Dict[str, Any]] = None,
    spread_type: Optional[str] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "legs": list(legs),
            "underlyingSymbol": underlying_symbol,
            "dynamicUnderlying": dynamic_underlying,
            "spreadType": spread_type,
        }
    )


def against_parent(**config: Any) -> Dict[str, Any]:
    mapping = {
        "min_parent_pnl_percent": "minParentPnlPercent",
        "max_parent_pnl_percent": "maxParentPnlPercent",
        "coverage_ratio": "coverageRatio",
        "require_short_strike_above_parent_strike":
            "requireShortStrikeAboveParentStrike",
        "strike_offset_from_parent": "strikeOffsetFromParent",
    }
    return _compact(
        {mapping.get(key, key): value for key, value in config.items()}
    )


def open_option(
    *,
    builder: Dict[str, Any],
    allocation: Optional[Dict[str, Any]] = None,
    against: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "type": "OpenOption",
            "builder": builder,
            "allocation": allocation,
            "against": against,
        }
    )


def structure_template(
    *,
    legs: Sequence[Dict[str, Any]],
    name: Optional[str] = None,
    eligibility: Optional[Condition] = None,
    spread_type: Optional[str] = None,
    width_constraint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Structure template for RebalanceOption.

    width_constraint sets a minimum realised strike width, checked AFTER
    contracts resolve: {"minWidthPercent": 4} or {"minWidthDollars": 40}.
    Each leg snaps independently to the nearest listed strike, so the authored
    offsets and the width you actually get differ by up to a full grid step in
    either direction. minWidthPercent is a whole-number percent of spot.
    """
    return _compact(
        {
            "legs": list(legs),
            "name": name,
            "eligibility": (
                _condition_dict(eligibility)
                if eligibility is not None
                else None
            ),
            "spreadType": spread_type,
            "widthConstraint": width_constraint,
        }
    )


def sleeve(
    *,
    name: str,
    universe_config: Optional[Dict[str, Any]] = None,
    pipeline: Optional[Sequence[Dict[str, Any]]] = None,
    weight_indicator: Optional[Indicator] = None,
    limit: Optional[int] = None,
    per_name_allocation: Optional[Dict[str, Any]] = None,
    structure_templates: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "name": name,
            "universe": universe_config,
            "pipeline": list(pipeline) if pipeline is not None else None,
            "weightIndicator": (
                _indicator_dict(weight_indicator)
                if weight_indicator is not None
                else None
            ),
            "limit": limit,
            "perNameAllocation": per_name_allocation,
            "structureTemplates": (
                list(structure_templates)
                if structure_templates is not None
                else None
            ),
        }
    )


def rebalance_option(
    *,
    universe_config: Dict[str, Any],
    pipeline: Sequence[Dict[str, Any]],
    weight_indicator: Indicator,
    structure_templates: Sequence[Dict[str, Any]],
    limit: Optional[int] = None,
    total_budget: Optional[Dict[str, Any]] = None,
    per_name_allocation: Optional[Dict[str, Any]] = None,
    sizing_mode: Optional[Literal["fixedPerName", "proportionalToWeight", "proportionalToWeightWholeContracts"]] = None,
    position_scope: Optional[str] = None,
    sleeves: Optional[Sequence[Dict[str, Any]]] = None,
    allocation_policy: Optional[Dict[str, Any]] = None,
    exposure_policy: Optional[Dict[str, Any]] = None,
    renew_before_days: Optional[int] = None,
    revisit_disclosed_targets: Optional[bool] = None,
) -> Dict[str, Any]:
    _enum(sizing_mode, ["fixedPerName", "proportionalToWeight", "proportionalToWeightWholeContracts"], "sizing_mode")
    # Disclosure maintenance requires a NewPoliticalDisclosure Option condition
    # with exactly one memberId. These are not generic DTE-roll controls.
    if renew_before_days is not None and (type(renew_before_days) is not int or not 1 <= renew_before_days <= 90):
        raise ValueError("renew_before_days must be an integer from 1 to 90")
    if revisit_disclosed_targets is not None and type(revisit_disclosed_targets) is not bool:
        raise ValueError("revisit_disclosed_targets must be boolean")
    return _compact(
        {
            "type": "RebalanceOption",
            "universe": universe_config,
            "pipeline": list(pipeline),
            "weightIndicator": _indicator_dict(weight_indicator),
            "structureTemplates": list(structure_templates),
            "limit": limit,
            "totalBudget": (
                _deployment_budget(total_budget)
                if total_budget is not None else None
            ),
            "perNameAllocation": per_name_allocation,
            "sizingMode": sizing_mode,
            "positionScope": position_scope,
            "sleeves": list(sleeves) if sleeves is not None else None,
            "allocationPolicy": allocation_policy,
            "exposurePolicy": exposure_policy,
            "renewBeforeDays": renew_before_days,
            "revisitDisclosedTargets": revisit_disclosed_targets,
        }
    )


def pnl_trigger(
    *,
    min_pnl_percent: Optional[float] = None,
    max_pnl_percent: Optional[float] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "type": "pnl",
            "minPnlPercent": min_pnl_percent,
            "maxPnlPercent": max_pnl_percent,
        }
    )


def dte_trigger(
    *,
    min_dte: Optional[int] = None,
    max_dte: Optional[int] = None,
) -> Dict[str, Any]:
    return _compact(
        {"type": "dte", "minDte": min_dte, "maxDte": max_dte}
    )


def days_held_trigger(
    *,
    min_days_held: Optional[int] = None,
    max_days_held: Optional[int] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "type": "daysHeld",
            "minDaysHeld": min_days_held,
            "maxDaysHeld": max_days_held,
        }
    )


def greeks_trigger(**bounds: Optional[float]) -> Dict[str, Any]:
    mapping = {
        "min_spread_delta": "minSpreadDelta",
        "max_spread_delta": "maxSpreadDelta",
        "min_iv": "minIv",
        "max_iv": "maxIv",
        "min_spread_theta": "minSpreadTheta",
        "max_spread_theta": "maxSpreadTheta",
    }
    return _compact(
        {
            "type": "greeks",
            **{
                mapping.get(key, key): value
                for key, value in bounds.items()
            },
        }
    )


def close_option(
    *,
    underlyings: Optional[Sequence[str]] = None,
    option_type: Optional[str] = None,
    direction: Optional[str] = None,
    spread_type: Optional[str] = None,
    close_scope: Optional[str] = None,
    triggers: Optional[Sequence[Dict[str, Any]]] = None,
    quantity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "type": "CloseOption",
            "underlyings": (
                list(underlyings) if underlyings is not None else None
            ),
            "optionType": option_type,
            "direction": direction,
            "spreadType": spread_type,
            "closeScope": close_scope,
            "triggers": list(triggers) if triggers is not None else None,
            "quantity": quantity,
        }
    )


def limit_order(
    *,
    price: Dict[str, Any],
    working_time: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "type": "Limit",
            "price": price,
            "workingTime": working_time,
        }
    )


def current_limit() -> Dict[str, Any]:
    return {"type": "QuoteRelative", "reference": "Current"}


def unit_price_limit(amount: float) -> Dict[str, Any]:
    return {"type": "UnitPrice", "amount": amount}


def _net_limit_amount(amount: Union[float, "Indicator"]) -> Any:
    return amount.to_dict() if isinstance(amount, Indicator) else amount


def maximum_net_debit(amount: Union[float, "Indicator"]) -> Dict[str, Any]:
    """Dollars per share, or an indicator evaluated when the strategy fires."""
    return {"type": "MaximumNetDebit", "amount": _net_limit_amount(amount)}


def minimum_net_credit(amount: Union[float, "Indicator"]) -> Dict[str, Any]:
    """Dollars per share, or an indicator evaluated when the strategy fires."""
    return {"type": "MinimumNetCredit", "amount": _net_limit_amount(amount)}


def good_for_day() -> Dict[str, Any]:
    return {"type": "Day"}


def good_for_minutes(minutes: float) -> Dict[str, Any]:
    return {"type": "Minutes", "minutes": minutes}


def strategy(
    name: str,
    condition: Condition,
    action: Dict[str, Any],
    *,
    active: Optional[bool] = None,
    order_execution: Optional[Dict[str, Any]] = None,
    pending_order_policy: Optional[Literal["Wait", "WaitForTarget", "CancelConflictingBuys"]] = None,
) -> Dict[str, Any]:
    return _compact(
        {
            "name": name,
            "condition": _condition_dict(condition),
            "action": action,
            "active": active,
            "orderExecution": order_execution or {"type": "Market"},
            "pendingOrderPolicy": pending_order_policy,
        }
    )


def session_flatten(
    condition: Condition,
    *,
    name: str = "Session flatten",
    underlyings: Optional[Sequence[str]] = None,
    option_type: Optional[str] = None,
    direction: Optional[str] = None,
    spread_type: Optional[str] = None,
    close_scope: Optional[str] = None,
    quantity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return strategy(
        name,
        condition,
        close_option(
            underlyings=underlyings,
            option_type=option_type,
            direction=direction,
            spread_type=spread_type,
            close_scope=close_scope,
            quantity=quantity,
        ),
    )


def portfolio(
    name: str,
    strategies: Sequence[Dict[str, Any]],
    *,
    initial_value: float = 10000,
    main: Optional[bool] = None,
    supports_fractional_shares: Optional[bool] = None,
    supports_crypto: Optional[bool] = None,
    alerts_enabled: Optional[bool] = None,
    dividend_policy: Optional[Literal["cash", "reinvest"]] = None,
    cash_settlement: Optional[Literal["T1"]] = None,
    policy: Optional[Dict[str, Any]] = None,
    deployment: Optional[Dict[str, Any]] = None,
) -> Portfolio:
    """dividend_policy: "cash" (default when omitted) keeps dividends as cash;
    "reinvest" buys more of the paying stock on each pay date.
    policy takes only {"stockEligibility": {...}} (market-cap bounds,
    industryFilter, missingMarketCapBehavior, shareClassBehavior). Automated
    trading is never authored; only the owner enables it in the NexusTrade UI.
    """
    return Portfolio(
        _compact(
            {
                "name": name,
                "initialValue": initial_value,
                "cash": initial_value,
                "buyingPower": initial_value,
                "strategies": list(strategies),
                "main": main,
                "supportsFractionalShares": supports_fractional_shares,
                "supportsCrypto": supports_crypto,
                "alertsEnabled": alerts_enabled,
                "dividendPolicy": dividend_policy,
                "cashSettlement": cash_settlement,
                "policy": policy,
                "deployment": deployment,
            }
        )
    )

# ---- generated indicator builders ----
def AbsoluteValue(
    operand: Indicator,
) -> Indicator:
    """The absolute value of an indicator.
    """
    d: Dict[str, Any] = {"type": "AbsoluteValue"}
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("AbsoluteValue")

def AccumulationDistribution(
    asset: Union[str, Dict[str, Any], _Candidate],
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Running total of close-location value times volume. Unlike OBV it weights where in the range the bar closed, so weak closes on high volume distribute. Range: Unbounded (cumulative).
    asset: Ticker name (ex. SPY, BTC)
    interval: Bar interval the line is accumulated on
    """
    d: Dict[str, Any] = {"type": "AccumulationDistribution"}
    _set_asset(d, "targetAsset", asset)
    d["interval"] = _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")
    return Indicator(d)

__all__.append("AccumulationDistribution")

def AverageDirectionalIndex(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 14,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    line: Literal["adx", "plusDi", "minusDi"] = "adx",
) -> Indicator:
    """Wilder's ADX: trend strength on 0-100, with optional +DI and -DI trend-direction lines. ADX above ~25 is conventionally a strong trend. Range: 0 to 100.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    line: Which ADX-system line to evaluate
    """
    d: Dict[str, Any] = {"type": "AverageDirectionalIndex"}
    _set_asset(d, "targetAsset", asset)
    d["line"] = _enum(line, ["adx","plusDi","minusDi"], "line")
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("AverageDirectionalIndex")

def AverageTrueRange(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 14,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """The average of True Range over a window. A smoothed measure of volatility used for position sizing and stop-losses. Range: >= 0.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "AverageTrueRange"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("AverageTrueRange")

def BearishFairValueGap(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Detects a bearish fair value gap (3-candle gap down) on minute bars. Returns the gap's resistance price (candle1 low) while the pattern is active; otherwise no value (condition fails). Not a 0/1 flag — never compare to 1. Range: price when present.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "BearishFairValueGap"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("BearishFairValueGap")

def BollingerBand(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    num_standard_deviations: float = 1,
) -> Indicator:
    """One Bollinger band, returned as a single number: the simple moving average of price plus the given number of standard deviations of price over the same window. A positive number gives the upper band, a negative number the lower band, and 0 the moving average itself. Range: Same scale as price.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    num_standard_deviations: How many standard deviations from the mean
    """
    d: Dict[str, Any] = {"type": "BollingerBand"}
    _set_asset(d, "targetAsset", asset)
    d["numStandardDeviations"] = num_standard_deviations
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

BBand = BollingerBand
__all__.append("BollingerBand")
__all__.append("BBand")

def BullishFairValueGap(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Detects a bullish fair value gap (3-candle gap up) on minute bars. Returns the gap's support price (candle1 high) while the pattern is active; otherwise no value (condition fails). Not a 0/1 flag — never compare to 1 (that is for CrossAbove/CrossBelow). Range: price when present.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "BullishFairValueGap"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("BullishFairValueGap")

def BuyingPower() -> Indicator:
    """The amount of cash available to make new purchases. Range: >= 0.
    """
    d: Dict[str, Any] = {"type": "BuyingPower"}
    return Indicator(d)

__all__.append("BuyingPower")

def ChaikinMoneyFlow(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 20,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Money flow volume over money volume across N bars, bounded in -1 to +1. Sustained readings above +0.2 show institutional accumulation. Range: -1 to +1.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "ChaikinMoneyFlow"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("ChaikinMoneyFlow")

def ChartBar(
    asset: Union[str, Dict[str, Any], _Candidate],
    bar_minutes: float = 5,
    line: Literal["open", "high", "low", "close", "sma", "endTime"] = "close",
    bars: float = 1,
    offset_bars: float = 0,
    minute_policy: Literal["strict", "observed"] = "strict",
) -> Indicator:
    """Prices, the average close, or the end time of completed chart candles, built from contiguous minute data. Stock candles align to the regular trading session. Range: Same scale as price for open, high, low, close and sma; the endTime line returns the end time of the newest selected candle in Unix seconds.
    asset: Ticker name (ex. SPY, BTC)
    bar_minutes: Completed candles: stocks divide the 390-minute regular session from 9:30 ET. Crypto divides the 1440-minute UTC day or uses 10080 for a Monday UTC week; legacy intraday sizes remain valid. Observation cadence must be Minute even for daily/weekly candles.
    line: open: oldest selected candle; close: newest; high/low: range; sma: mean candle closes.
    bars: Number of chart candles, not elapsed minutes. SMA20 on five-minute candles: barMinutes=5, bars=20.
    offset_bars: Skip this many latest completed candles. 1 selects the previous window.
    minute_policy: strict requires every minute. observed aggregates actual eligible-trade prints, skips empty candles, and publishes after the clock bucket closes. No synthetic prices; source coverage is a separate check.
    """
    d: Dict[str, Any] = {"type": "ChartBar"}
    _set_asset(d, "targetAsset", asset)
    d["barMinutes"] = bar_minutes
    d["line"] = _enum(line, ["open","high","low","close","sma","endTime"], "line")
    d["bars"] = bars
    d["offsetBars"] = offset_bars
    d["minutePolicy"] = _enum(minute_policy, ["strict","observed"], "minute_policy")
    return Indicator(d)

__all__.append("ChartBar")

def CommodityChannelIndex(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 20,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    constant: float = 0.015,
) -> Indicator:
    """Deviation of the typical price from its moving average, scaled by mean deviation. Readings above +100 are overbought, below -100 oversold. Range: Unbounded (typically -300 to +300).
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    constant: Divisor scaling the mean deviation
    """
    d: Dict[str, Any] = {"type": "CommodityChannelIndex"}
    _set_asset(d, "targetAsset", asset)
    d["constant"] = constant
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("CommodityChannelIndex")

def CompoundAnnualGrowthRate(
    asset: Union[str, Dict[str, Any], _Candidate],
    metric: Literal["totalRevenue", "grossProfit", "operatingIncome", "ebitda", "epsActual", "epsEstimate", "netIncome", "freeCashFlow", "totalAssets", "totalLiab", "shortTermDebt", "longTermDebt", "commonStockSharesOutstanding", "dividendYield", "openingPrice", "highestPrice", "lowestPrice", "closingPrice", "volume", "marketCap", "peRatioTTM", "psRatioTTM", "pbRatioTTM", "enterpriseValue"],
    years: float,
) -> Indicator:
    """The annualized growth rate of a fundamental metric over a specified number of years. Smooths out quarterly volatility to show the long-term trend. Range: Unbounded (percentage).
    asset: Ticker name (ex. SPY, BTC)
    metric: The fundamental metric to use
    years: Floating point years value
    """
    d: Dict[str, Any] = {"type": "CompoundAnnualGrowthRate"}
    _set_asset(d, "targetAsset", asset)
    d["metric"] = _enum(metric, ["totalRevenue","grossProfit","operatingIncome","ebitda","epsActual","epsEstimate","netIncome","freeCashFlow","totalAssets","totalLiab","shortTermDebt","longTermDebt","commonStockSharesOutstanding","dividendYield","openingPrice","highestPrice","lowestPrice","closingPrice","volume","marketCap","peRatioTTM","psRatioTTM","pbRatioTTM","enterpriseValue"], "metric")
    d["years"] = years
    return Indicator(d)

__all__.append("CompoundAnnualGrowthRate")

def ConsecutiveDecrease(
    indicator: Indicator,
    period: Literal["Year", "Quarter", "Month"],
    lookback: float,
) -> Indicator:
    """Number of consecutive completed calendar periods (America/New_York; period is Year, Quarter or Month), ending with the last completed period, whose closing value of the inner indicator was strictly higher (ConsecutiveIncrease) or strictly lower (ConsecutiveDecrease) than the previous period's closing value. A period's closing value is the inner indicator's last non-null value observed in that period. The in-progress current period never counts, equal values are not a change, and a completed period with no value breaks the streak. The count is capped at lookback (an integer from 1 to 60). Returns null when no completed period has a value. Examples: ConsecutiveIncrease(LastDividend(KO), Year, 10) >= 10 (KO raised its regular dividend per share in each of the last 10 completed years); ConsecutiveIncrease(Fundamental(AAPL, totalRevenue), Quarter, 8) (consecutive completed quarters of higher reported revenue for AAPL, up to 8).
    indicator: The inner indicator whose period closing values are compared. Pass CANDIDATE to its asset in a dynamic rebalance pipeline.
    period: Calendar period (America/New_York) whose closing values are compared.
    lookback: Whole number of completed periods to count, 1 to 60; the streak is capped here.
    """
    d: Dict[str, Any] = {"type": "ConsecutiveDecrease"}
    d["period"] = _enum(period, ["Year","Quarter","Month"], "period")
    d["lookback"] = lookback
    d["indicators"] = [indicator.d]
    return Indicator(d)

__all__.append("ConsecutiveDecrease")

def ConsecutiveIncrease(
    indicator: Indicator,
    period: Literal["Year", "Quarter", "Month"],
    lookback: float,
) -> Indicator:
    """Number of consecutive completed calendar periods (America/New_York; period is Year, Quarter or Month), ending with the last completed period, whose closing value of the inner indicator was strictly higher (ConsecutiveIncrease) or strictly lower (ConsecutiveDecrease) than the previous period's closing value. A period's closing value is the inner indicator's last non-null value observed in that period. The in-progress current period never counts, equal values are not a change, and a completed period with no value breaks the streak. The count is capped at lookback (an integer from 1 to 60). Returns null when no completed period has a value. Examples: ConsecutiveIncrease(LastDividend(KO), Year, 10) >= 10 (KO raised its regular dividend per share in each of the last 10 completed years); ConsecutiveIncrease(Fundamental(AAPL, totalRevenue), Quarter, 8) (consecutive completed quarters of higher reported revenue for AAPL, up to 8).
    indicator: The inner indicator whose period closing values are compared. Pass CANDIDATE to its asset in a dynamic rebalance pipeline.
    period: Calendar period (America/New_York) whose closing values are compared.
    lookback: Whole number of completed periods to count, 1 to 60; the streak is capped here.
    """
    d: Dict[str, Any] = {"type": "ConsecutiveIncrease"}
    d["period"] = _enum(period, ["Year","Quarter","Month"], "period")
    d["lookback"] = lookback
    d["indicators"] = [indicator.d]
    return Indicator(d)

__all__.append("ConsecutiveIncrease")

def ConsecutiveTrue(
    condition: Condition,
    length: float = 1,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Report whether the ordinary condition has stayed true for the elapsed window. This is elapsed time, not a count of checks.
    condition: Condition evaluated over the window.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "ConsecutiveTrue"}
    d["condition"] = condition.d
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("ConsecutiveTrue")

def Correlation(
    operands: Sequence[Indicator],
    length: float = 60,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Rolling Pearson correlation (-1 to 1) of two indicators' period-over-period returns over N paired observations. Flat or degenerate windows read no-signal instead of a fabricated number.
    operands: One list of exactly 2 sub-indicators.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "Correlation"}
    d["indicators"] = [i.d for i in operands]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("Correlation")

def CountTrue(
    condition: Condition,
    length: int = 1,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    count_interval: Optional[Dict[str, Any]] = None,
) -> Indicator:
    """Count matching intervals inside a time lookback. The ordinary condition owns comparisons/events. countInterval contributes at most one match per interval; omitted means one unit of the lookback interval. TradingDay skips closed exchange dates.
    condition: Ordinary condition whose matching intervals are counted.
    length: Lookback length.
    interval: Lookback time unit; TradingDay uses observed exchange sessions and TradingMinute (at most 1439) counts regular-session minutes across closed periods.
    count_interval: Count at most once per this interval. Omit for one unit of the lookback interval.
    """
    d: Dict[str, Any] = {"type": "CountTrue"}
    d["condition"] = condition.d
    if count_interval is not None:
        d["countInterval"] = count_interval
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("CountTrue")

def CrossAbove(
    left: Indicator,
    right: Indicator,
) -> Indicator:
    """CrossAbove and CrossBelow are numeric Indicators, not Conditions: with available operands they return 1 when a crossing is detected and 0 otherwise; an unavailable operand produces no value. Comparing the result with Value(0), for example gt(CrossAbove(left, right), Value(0)), is valid; passing the indicator directly to and/or is not. They compare consecutive market observations, not consecutive strategy decisions or completed chart candles. Stateful operands continue observing behind time and position gates, so a crossing before a scheduled decision can be 0 by the decision. Zero at that decision does not prove the underlying series never crossed. For an ordinary daily moving-average crossover strategy without a more specific observation clock, compare the two most recently completed trading sessions and disclose that execution follows confirmation at a later scheduled decision. Define latestLeft=IndicatorWindowAgo(left,1,'TradingDay'), latestRight=IndicatorWindowAgo(right,1,'TradingDay'), priorLeft=IndicatorWindowAgo(left,2,'TradingDay'), and priorRight=IndicatorWindowAgo(right,2,'TradingDay'). An upward completed-session crossing is gt(latestLeft,latestRight) AND lte(priorLeft,priorRight); reverse the comparisons for a downward crossing. Comparing the forming/current average against lag 1 can miss a crossing that occurs at the close after the scheduled decision: by the next decision the lag-1 relationship has already changed. A bare CrossAbove/CrossBelow impulse has a similar observation-versus-decision limitation. Preserve an explicitly requested partial-session/current-versus-prior comparison or observation-level impulse, and explain its sampling limitations rather than silently changing its clock. TradingDay references a confirmed session closing value; Day is elapsed calendar time, including for the underlying average window. ChartBar already excludes the forming candle, so preserve its intended candle identity and offset instead of blindly adding another lag. For other bar/session/decision references choose operands and lag lengths from the requested observation clock. Before diagnosing a crossover mismatch, inspect saved operand windows, observation/decision cadence, current and referenced values, and retained evaluation evidence. An outer comparison audit without inner operands cannot establish why the crossing was absent. Do not propose removing a valid numeric comparison as a repair.
    """
    d: Dict[str, Any] = {"type": "CrossAbove"}
    d["indicators"] = [left.d, right.d]
    return Indicator(d)

__all__.append("CrossAbove")

def CrossBelow(
    left: Indicator,
    right: Indicator,
) -> Indicator:
    """CrossAbove and CrossBelow are numeric Indicators, not Conditions: with available operands they return 1 when a crossing is detected and 0 otherwise; an unavailable operand produces no value. Comparing the result with Value(0), for example gt(CrossAbove(left, right), Value(0)), is valid; passing the indicator directly to and/or is not. They compare consecutive market observations, not consecutive strategy decisions or completed chart candles. Stateful operands continue observing behind time and position gates, so a crossing before a scheduled decision can be 0 by the decision. Zero at that decision does not prove the underlying series never crossed. For an ordinary daily moving-average crossover strategy without a more specific observation clock, compare the two most recently completed trading sessions and disclose that execution follows confirmation at a later scheduled decision. Define latestLeft=IndicatorWindowAgo(left,1,'TradingDay'), latestRight=IndicatorWindowAgo(right,1,'TradingDay'), priorLeft=IndicatorWindowAgo(left,2,'TradingDay'), and priorRight=IndicatorWindowAgo(right,2,'TradingDay'). An upward completed-session crossing is gt(latestLeft,latestRight) AND lte(priorLeft,priorRight); reverse the comparisons for a downward crossing. Comparing the forming/current average against lag 1 can miss a crossing that occurs at the close after the scheduled decision: by the next decision the lag-1 relationship has already changed. A bare CrossAbove/CrossBelow impulse has a similar observation-versus-decision limitation. Preserve an explicitly requested partial-session/current-versus-prior comparison or observation-level impulse, and explain its sampling limitations rather than silently changing its clock. TradingDay references a confirmed session closing value; Day is elapsed calendar time, including for the underlying average window. ChartBar already excludes the forming candle, so preserve its intended candle identity and offset instead of blindly adding another lag. For other bar/session/decision references choose operands and lag lengths from the requested observation clock. Before diagnosing a crossover mismatch, inspect saved operand windows, observation/decision cadence, current and referenced values, and retained evaluation evidence. An outer comparison audit without inner operands cannot establish why the crossing was absent. Do not propose removing a valid numeric comparison as a repair.
    """
    d: Dict[str, Any] = {"type": "CrossBelow"}
    d["indicators"] = [left.d, right.d]
    return Indicator(d)

__all__.append("CrossBelow")

def CurrentTimeHours() -> Indicator:
    """Hours since midnight in America/New_York time, including fractional hours. For example, 9:30 AM returns 9.5. Daylight saving time is handled automatically.
    """
    d: Dict[str, Any] = {"type": "CurrentTimeHours"}
    return Indicator(d)

__all__.append("CurrentTimeHours")

def CurrentTimeMinutes() -> Indicator:
    """Minutes since midnight in America/New_York time, including fractional minutes. For example, 9:30 AM returns 570. Daylight saving time is handled automatically.
    """
    d: Dict[str, Any] = {"type": "CurrentTimeMinutes"}
    return Indicator(d)

__all__.append("CurrentTimeMinutes")

def CurrentTimeSeconds() -> Indicator:
    """Seconds since midnight in America/New_York time, including fractions of a second. For example, 9:30 AM returns 34200. Daylight saving time is handled automatically.
    """
    d: Dict[str, Any] = {"type": "CurrentTimeSeconds"}
    return Indicator(d)

__all__.append("CurrentTimeSeconds")

def CurrentTimestamp() -> Indicator:
    """Evaluation time in Unix seconds, including fractions. Use with ChartBar endTime for ordinary freshness comparisons; backtests use their simulated clock.
    """
    d: Dict[str, Any] = {"type": "CurrentTimestamp"}
    return Indicator(d)

__all__.append("CurrentTimestamp")

def CurrentValue(
    operand: Indicator,
) -> Indicator:
    """Inside a CountTrue historical condition, bind this operand at its value now while other operands use their historical observations. Elsewhere return the operand normally. Wrap a complete stateful operand, not an expression inside its recurrence.
    """
    d: Dict[str, Any] = {"type": "CurrentValue"}
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("CurrentValue")

def CustomIndicator(
    asset: Union[str, Dict[str, Any], _Candidate],
    custom_indicator_id: str,
) -> Indicator:
    """Account-owned timestamp/value data uploaded through CSV or JSON ingestion. Range: User-defined numeric value.
    asset: Pass CANDIDATE inside a rebalance pipeline to bind per candidate.
    custom_indicator_id: 24-char hex ObjectId of the owned series (list_custom_indicators).
    """
    d: Dict[str, Any] = {"type": "CustomIndicator"}
    _set_asset(d, "targetAsset", asset)
    d["customIndicatorId"] = custom_indicator_id
    return Indicator(d)

__all__.append("CustomIndicator")

def Date() -> Indicator:
    """The day of the month in America/New_York, from 1 through 31. Combine with Month and Year for a specific calendar date.
    """
    d: Dict[str, Any] = {"type": "Date"}
    return Indicator(d)

__all__.append("Date")

def Day() -> Indicator:
    """The weekday in America/New_York: Monday = 1 through Sunday = 7. Use Date for the day of the month.
    """
    d: Dict[str, Any] = {"type": "Day"}
    return Indicator(d)

__all__.append("Day")

def DaysSinceAgent() -> Indicator:
    """The number of days since an AI agent was launched by this strategy. Useful for rate-limiting agent launches. Range: >= 0.
    """
    d: Dict[str, Any] = {"type": "DaysSinceAgent"}
    return Indicator(d)

__all__.append("DaysSinceAgent")

def DaysSinceAlert(
    message: str,
) -> Indicator:
    """The number of days since an alert was triggered. Useful for rate-limiting alerts. Range: >= 0.
    message: (Optional) Message to display when the indicator is triggered
    """
    d: Dict[str, Any] = {"type": "DaysSinceAlert"}
    d["message"] = message
    return Indicator(d)

__all__.append("DaysSinceAlert")

def DaysSinceEarnings(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Calendar days since the asset's most recent earnings report. In backtests and live, this is the last actually-reported quarter at-or-before the decision time (no look-ahead). Use to time post-earnings entries (e.g. fade the gap after N days, or wait for the IV crush to settle). Range: >= 0 (0 on the report day; grows as time passes).
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "DaysSinceEarnings"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("DaysSinceEarnings")

def DaysSinceLastRebalanceOptionOrder() -> Indicator:
    """Elapsed days since the latest Filled-matching portfolio order stamped RebalanceOption, RebalanceOptionRenewal or RebalanceOptionFunding. It uses fill time and ignores strategy ID, so these strategies share a cooldown. Unfilled or canceled orders do not reset it. Range: >= 0 (days), or a large number if no RebalanceOption-generated order exists.
    """
    d: Dict[str, Any] = {"type": "DaysSinceLastRebalanceOptionOrder"}
    return Indicator(d)

__all__.append("DaysSinceLastRebalanceOptionOrder")

def DaysSinceOptionOrder(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
    position_effect: Literal["open", "close"],
    min_dte: float,
    max_dte: float,
    order_status: Literal["Pending", "Accepted", "Pending User Approval", "Canceled", "Filled", "Partially Filled"] = "Filled",
) -> Indicator:
    """The number of days since the most recent option order matching the specified filters. Supports filtering by underlying, option type (call/put), spread type, direction (long/short), position effect (open/close), and days to expiration at order time. Range: >= 0 (days), or a large number if no matching order exists.
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    position_effect: Filter by Open (entering position) or Close (exiting). Leave empty for all.
    min_dte: Minimum days to expiration at order time. Leave empty for no minimum.
    max_dte: Maximum days to expiration at order time. Leave empty for no maximum.
    order_status: Matches option order events with this status
    """
    d: Dict[str, Any] = {"type": "DaysSinceOptionOrder"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    d["positionEffect"] = _enum(position_effect, ["open","close"], "position_effect")
    d["minDte"] = min_dte
    d["maxDte"] = max_dte
    d["orderStatus"] = _enum(order_status, ["Pending","Accepted","Pending User Approval","Canceled","Filled","Partially Filled"], "order_status")
    return Indicator(d)

__all__.append("DaysSinceOptionOrder")

def DaysSinceOrder(
    assets: Union[str, Sequence[Any]],
    side: Literal["Buy", "Sell"],
    order_status: Literal["Pending", "Accepted", "Pending User Approval", "Canceled", "Filled", "Partially Filled"] = "Filled",
) -> Indicator:
    """The number of days since the most recent order matching the specified filters. Range: >= 0 (days), or a large number if no matching order exists.
    assets: Ticker(s) whose orders are counted. One string or an array.
    side: Buy or Sell
    """
    d: Dict[str, Any] = {"type": "DaysSinceOrder"}
    _set_assets(d, "targetAssets", assets)
    d["side"] = _enum(side, ["Buy","Sell"], "side")
    d["orderStatus"] = _enum(order_status, ["Pending","Accepted","Pending User Approval","Canceled","Filled","Partially Filled"], "order_status")
    return Indicator(d)

__all__.append("DaysSinceOrder")

def DaysSinceStrategyFired() -> Indicator:
    """The number of days since the current strategy last produced an execution artifact. Counts same-strategy order rows, rebalance orders, and launched agents. Range: >= 0 (days), or a large number if the strategy has never fired.
    """
    d: Dict[str, Any] = {"type": "DaysSinceStrategyFired"}
    return Indicator(d)

__all__.append("DaysSinceStrategyFired")

def DaysUntilEarnings(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Calendar days until the asset's next expected earnings report. In backtests the next date is a point-in-time cadence estimate from the last report (no look-ahead); live trading uses the real next scheduled date. Use to time entries and exits around earnings. Range: >= 0 (0 on/after the expected report day; counts down as it nears).
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "DaysUntilEarnings"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("DaysUntilEarnings")

def Divide(
    left: Indicator,
    right: Indicator,
) -> Indicator:
    """Quotient of indicators.
    """
    d: Dict[str, Any] = {"type": "Divide"}
    d["indicators"] = [left.d, right.d]
    return Indicator(d)

__all__.append("Divide")

def DonchianChannel(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 20,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    line: Literal["upper", "lower", "middle"] = "upper",
) -> Indicator:
    """Highest high and lowest low over N bars with a midline. Breaks of either edge are breakout entries; the midline is the first pullback level. Range: Price scale.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    line: Which Donchian line to evaluate
    """
    d: Dict[str, Any] = {"type": "DonchianChannel"}
    _set_asset(d, "targetAsset", asset)
    d["line"] = _enum(line, ["upper","lower","middle"], "line")
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("DonchianChannel")

def Economic(
    metric: Literal["NonfarmPayrolls", "UnemploymentRate", "MoneySupply", "TreasuryYield3Month", "CPI"],
) -> Indicator:
    """Macroeconomic data not tied to a specific asset.
    metric: The economic indicator to use
    """
    d: Dict[str, Any] = {"type": "Economic"}
    d["metric"] = _enum(metric, ["NonfarmPayrolls","UnemploymentRate","MoneySupply","TreasuryYield3Month","CPI"], "metric")
    return Indicator(d)

__all__.append("Economic")

def ExponentialMovingAverage(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """SMA/EMA window interval Day means elapsed calendar time, including closed exchange dates. TradingDay uses the exchange-session clock for exchange-traded assets and skips closed dates while preserving within-session time. For a conventional N-day stock moving average, author interval TradingDay unless the request explicitly specifies elapsed calendar days. An outer TradingDay lag does not change an inner Day average into an N-session average. Preserve an explicit calendar-day request as Day. Continuous-market daily windows use 24-hour Day intervals. A TradingDay window can still have a forming current reading: choose completed-session references separately when the signal requires confirmed closes. ChartBar periods and offsets already identify completed candles; preserve that clock instead of substituting a time-window average.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "ExponentialMovingAverage"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

EMA = ExponentialMovingAverage
__all__.append("ExponentialMovingAverage")
__all__.append("EMA")

def Exponentiation(
    operand: Indicator,
    exponent: float = 2,
) -> Indicator:
    """Raises an indicator to a specified power.
    exponent: The exponent to raise the value to
    """
    d: Dict[str, Any] = {"type": "Exponentiation"}
    d["exponent"] = exponent
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("Exponentiation")

def Fundamental(
    asset: Union[str, Dict[str, Any], _Candidate],
    metric: Literal["totalRevenue", "grossProfit", "operatingIncome", "ebitda", "epsActual", "epsEstimate", "netIncome", "freeCashFlow", "totalAssets", "totalLiab", "shortTermDebt", "longTermDebt", "commonStockSharesOutstanding", "dividendYield", "openingPrice", "highestPrice", "lowestPrice", "closingPrice", "volume", "marketCap", "peRatioTTM", "psRatioTTM", "pbRatioTTM", "enterpriseValue"],
) -> Indicator:
    """A financial metric from a company's statements or derived valuation ratios.
    asset: Ticker name (ex. SPY, BTC)
    metric: The fundamental metric to use
    """
    d: Dict[str, Any] = {"type": "Fundamental"}
    _set_asset(d, "targetAsset", asset)
    d["metric"] = _enum(metric, ["totalRevenue","grossProfit","operatingIncome","ebitda","epsActual","epsEstimate","netIncome","freeCashFlow","totalAssets","totalLiab","shortTermDebt","longTermDebt","commonStockSharesOutstanding","dividendYield","openingPrice","highestPrice","lowestPrice","closingPrice","volume","marketCap","peRatioTTM","psRatioTTM","pbRatioTTM","enterpriseValue"], "metric")
    return Indicator(d)

__all__.append("Fundamental")

def GapPercentage(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The gap size expressed as a percentage of the previous close. Range: Unbounded (percentage).
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "GapPercentage"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("GapPercentage")

def GapSize(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The dollar difference between today's opening price and yesterday's closing price. Range: Unbounded.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "GapSize"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("GapSize")

def HeikinAshi(
    asset: Union[str, Dict[str, Any], _Candidate],
    line: Literal["open", "high", "low", "close", "streak"] = "close",
) -> Indicator:
    """Heikin-Ashi candles built from COMPLETED one-minute bars in the regular session (9:30-15:59 bar opens; pre-market and after-hours bars are skipped), identically in backtests and live trading. The first bar seeds HA open = (O+C)/2 with its own high, low and close; after that HA close = (O+H+L+C)/4, HA open = (prior HA open + prior HA close)/2, HA high = max(H, HA open, HA close), HA low = min(L, HA open, HA close). The recursion carries across sessions and advances once per new bar. The streak line is the signed length of the current same-color run, counted from the color change (+3 on the third green candle, -3 on the third red); it reads 0 on a doji and for the rest of any run in which a candle shows an opposite-direction wick (a lower wick on green, an upper wick on red). Range: Price scale; streak is a signed integer.
    asset: Ticker name (ex. SPY, BTC)
    line: Which one-minute Heikin-Ashi value to evaluate. streak is the signed length of the current same-color run (0 once it shows an opposite-direction wick, and on a doji)
    """
    d: Dict[str, Any] = {"type": "HeikinAshi"}
    _set_asset(d, "targetAsset", asset)
    d["line"] = _enum(line, ["open","high","low","close","streak"], "line")
    return Indicator(d)

__all__.append("HeikinAshi")

def HighOfDay(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The highest price reached during the current trading session. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "HighOfDay"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("HighOfDay")

def IchimokuCloud(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 9,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    span_length: float = 52,
    line: Literal["tenkan", "kijun", "senkouA", "senkouB", "chikou"] = "tenkan",
) -> Indicator:
    """Five-line trend system: Tenkan and Kijun midpoints, Senkou A/B cloud bands, and the Chikou lagging line. Lines are evaluated unshifted. Price above the cloud with Tenkan above Kijun is the full bullish alignment. Range: Price scale.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    span_length: Senkou B lookback in bars (Kijun scales from conversion)
    line: Which Ichimoku line to evaluate (unshifted)
    """
    d: Dict[str, Any] = {"type": "IchimokuCloud"}
    _set_asset(d, "targetAsset", asset)
    d["spanLength"] = span_length
    d["line"] = _enum(line, ["tenkan","kijun","senkouA","senkouB","chikou"], "line")
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IchimokuCloud")

def ImpliedVolatility(
    asset: Union[str, Dict[str, Any], _Candidate],
    dte: float = 30,
) -> Indicator:
    """Constant-maturity at-the-money implied volatility for the underlying, built from the options chain (near-target-DTE expirations, |delta|≈0.50 call/put average, linearly interpolated). Units are annualized decimals (0.25 = 25%). Used on each candidate of a dynamic rebalance over a broad universe (all US stocks, an index or a watchlist), it reads options chains with Greeks for every stock in that universe instead of one underlying, so the run is heavier than with a named ticker or a specific asset list. Range: > 0 (annualized decimal).
    asset: Ticker name (ex. SPY, BTC)
    dte: Target constant-maturity tenor in calendar days (e.g. 30)
    """
    d: Dict[str, Any] = {"type": "ImpliedVolatility"}
    _set_asset(d, "targetAsset", asset)
    d["dte"] = dte
    return Indicator(d)

__all__.append("ImpliedVolatility")

def Index(
    metric: Literal["VIX", "SPX", "NDX", "DJI", "RUT", "SKEW", "VVIX", "USDBROAD", "TNX"] = "VIX",
) -> Indicator:
    """Daily level for a market index or benchmark (e.g. VIX, SPX) from hydrated index price data — not a tradable equity price. Range: Unbounded (index points).
    metric: Ticker key matching hydrated index price data (add more in BigQuery + rehydrate as needed)
    """
    d: Dict[str, Any] = {"type": "Index"}
    d["metric"] = _enum(metric, ["VIX","SPX","NDX","DJI","RUT","SKEW","VVIX","USDBROAD","TNX"], "metric")
    return Indicator(d)

__all__.append("Index")

def IndicatorAtCondition(
    operand: Indicator,
    condition: Condition,
    selection: Literal["First", "Latest", "Maximum", "Minimum"] = "First",
    clock: Literal["Calendar", "Trading"] = "Calendar",
    initial_value: Optional[float] = None,
    length: Optional[float] = None,
    interval: Optional[Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"]] = None,
) -> Indicator:
    """Retain the operand from matching observations: First, Latest, Maximum, or Minimum. Ordinary conditions decide when to capture. An optional window expires observations; TradingDay skips closed exchange dates. Use this to freeze a qualified setup before entry or ratchet a numeric level, not IndicatorAtEntry when no fill exists yet.
    condition: Condition evaluated over the window.
    """
    d: Dict[str, Any] = {"type": "IndicatorAtCondition"}
    d["condition"] = condition.d
    d["selection"] = _enum(selection, ["First","Latest","Maximum","Minimum"], "selection")
    d["clock"] = _enum(clock, ["Calendar","Trading"], "clock")
    if initial_value is not None:
        d["initialValue"] = initial_value
    d["indicators"] = [operand.d]
    if length is not None or interval is not None:
        d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorAtCondition")

def IndicatorAtEntry(
    operand: Indicator,
    asset: Union[str, Dict[str, Any], _Candidate],
    side: Literal["Buy", "Sell"],
    order_status: Literal["Pending", "Accepted", "Pending User Approval", "Canceled", "Filled", "Partially Filled"] = "Filled",
    anchor: Literal["LatestFill", "HoldingEntry"] = "LatestFill",
) -> Indicator:
    """The value an indicator held at a matching fill of the asset. The entry anchor chooses the fill: LatestFill (the default) follows the most recent matching fill; HoldingEntry preserves the original holding entry when shares are added. The captured level stays fixed instead of rolling forward. In the form, a candidate-bound condition may leave the asset empty and the surrounding action supplies it; SDK code always passes the asset, using CANDIDATE inside a rebalance pipeline.
    asset: Ticker name (ex. SPY, BTC)
    side: The side of the fill the value is anchored to
    order_status: Matches order events with this status
    """
    d: Dict[str, Any] = {"type": "IndicatorAtEntry"}
    _set_asset(d, "targetAsset", asset)
    d["side"] = _enum(side, ["Buy","Sell"], "side")
    d["orderStatus"] = _enum(order_status, ["Pending","Accepted","Pending User Approval","Canceled","Filled","Partially Filled"], "order_status")
    d["anchor"] = _enum(anchor, ["LatestFill","HoldingEntry"], "anchor")
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("IndicatorAtEntry")

def IndicatorAtMinutesAfterOpen(
    operand: Indicator,
    minutes_after_open: float = 30,
) -> Indicator:
    """Captures the first non-null evaluated indicator value at or after the configured number of minutes after market open. The captured value is then frozen until its next daily reset. A delayed evaluation can capture after the target minute.
    minutes_after_open: The number of minutes after market open to capture the indicator value
    """
    d: Dict[str, Any] = {"type": "IndicatorAtMinutesAfterOpen"}
    d["minutesAfterOpen"] = minutes_after_open
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("IndicatorAtMinutesAfterOpen")

def IndicatorExponentialMovingAverage(
    operand: Indicator,
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Computes the Exponential Moving Average of any other indicator. TradingDay follows the input markets: crypto-only inputs use elapsed 24-hour days, including weekends, just like Day. Stock, option, mixed-market and asset-free expressions use confirmed exchange sessions. The window does not change intraday prices into daily closing prices. TradingMinute counts regular-session minutes the same way, so a 70 TradingMinute window at 9:45 reaches back to 3:06 PM on the prior session (the overnight gap is one step); crypto-only inputs use elapsed minutes. It allows at most 1439 minutes; use TradingDay for longer lookbacks. Indicators read every tick have no TradingMinute value outside regular hours, and windows over a ChartBar keep the value of its latest completed candle.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IndicatorExponentialMovingAverage"}
    d["indicators"] = [operand.d]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorExponentialMovingAverage")

def IndicatorMaximum(
    operand: Indicator,
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Rolling maximum of one numeric operand over a time window. ChartBar observations keep their completed candle end time; repeated reads do not extend their age. Available history is used, so express full-history coverage separately.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IndicatorMaximum"}
    d["indicators"] = [operand.d]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorMaximum")

def IndicatorMeanAbsoluteDeviation(
    operand: Indicator,
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Computes the MAD of any indicator over a window. Less sensitive to outliers than standard deviation.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IndicatorMeanAbsoluteDeviation"}
    d["indicators"] = [operand.d]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorMeanAbsoluteDeviation")

def IndicatorMinimum(
    operand: Indicator,
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Rolling minimum of one numeric operand over a time window. ChartBar observations keep their completed candle end time; repeated reads do not extend their age. Available history is used, so express full-history coverage separately.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IndicatorMinimum"}
    d["indicators"] = [operand.d]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorMinimum")

def IndicatorRateOfChange(
    operand: Indicator,
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """The percentage change of any indicator over a window.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IndicatorRateOfChange"}
    d["indicators"] = [operand.d]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorRateOfChange")

def IndicatorSimpleMovingAverage(
    operand: Indicator,
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Computes the Simple Moving Average of any other indicator over a window. TradingDay follows the input markets: crypto-only inputs use elapsed 24-hour days, including weekends, just like Day. Stock, option, mixed-market and asset-free expressions use confirmed exchange sessions. The window does not change intraday prices into daily closing prices. TradingMinute counts regular-session minutes the same way, so a 70 TradingMinute window at 9:45 reaches back to 3:06 PM on the prior session (the overnight gap is one step); crypto-only inputs use elapsed minutes. It allows at most 1439 minutes; use TradingDay for longer lookbacks. Indicators read every tick have no TradingMinute value outside regular hours, and windows over a ChartBar keep the value of its latest completed candle.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IndicatorSimpleMovingAverage"}
    d["indicators"] = [operand.d]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorSimpleMovingAverage")

def IndicatorStandardDeviation(
    operand: Indicator,
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Computes the standard deviation of any indicator over a window.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IndicatorStandardDeviation"}
    d["indicators"] = [operand.d]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorStandardDeviation")

def IndicatorWindowAgo(
    operand: Indicator,
    length: float,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"],
    initial_value: Optional[float] = None,
) -> Indicator:
    """Value at an elapsed time in the past. TradingDay instead reads the confirmed closing value of the reference exchange session, including early closes. Missing session evidence returns no value; initialValue applies before enough history exists.
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IndicatorWindowAgo"}
    if initial_value is not None:
        d["initialValue"] = initial_value
    d["indicators"] = [operand.d]
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IndicatorWindowAgo")

def IndustryNewsSentiment(
    industry: Literal["technology", "financialServices", "healthcare", "energy", "consumerGoods", "entertainmentAndMedia", "manufacturing", "transportation", "realEstate", "utilities", "agriculture", "education", "retail", "artificialIntelligence", "aiInfrastructure", "analytics", "cloudComputing", "dataCenter", "database", "cybersecurity", "developerTools", "software", "saas", "enterpriseSoftware", "customerExperienceSoftware", "productivityTools", "collaborationSoftware", "digitalSignatureAndAuthentication", "hardware", "consumerElectronics", "networking", "semiconductor", "chipDesign", "chipManufacturing", "semiconductorEquipment", "gpuAndAccelerators", "memoryAndStorage", "quantumComputing", "robotics", "industrialAutomation", "iot", "smartDevices", "wearable", "3dPrinting", "augmentedReality", "virtualReality", "space", "satellites", "aerospace", "defense", "drones", "financialInfrastructure", "capitalMarketsTechnology", "banking", "assetManagement", "brokerage", "payments", "fintech", "insurance", "autoInsurance", "homeInsurance", "lifeInsurance", "healthInsurance", "blockchain", "cryptocurrency", "biotechnology", "pharmaceuticals", "medicalDevices", "digitalHealth", "lifeSciencesSoftware", "bioinformatics", "computationalBiology", "computationalDrugDiscovery", "oncology", "immunotherapy", "vaccines", "veterinary", "oil", "gas", "oilfieldServices", "cleanEnergy", "renewableEnergy", "solarEnergy", "windEnergy", "nuclearEnergy", "hydrogen", "batteryTechnology", "miningAndNaturalResources", "gold", "silver", "chemicals", "advancedMaterials", "waterInfrastructure", "wasteManagementAndRecycling", "forestry", "construction", "buildingMaterials", "electricalAndGridInfrastructure", "logisticsAndSupplyChain", "airline", "maritime", "railroads", "automotive", "electricVehicle", "autonomousTransportation", "recreationalVehicle", "cruise", "rideShare", "printing", "packaging", "householdProducts", "ecommerce", "digitalMarketplace", "fashionAndApparel", "luxuryGoods", "jewelry", "foodAndBeverage", "foodDelivery", "nutrition", "petCare", "outdoorAndRecreationalEquipment", "sportsAndFitness", "cannabis", "hospitalityAndTravel", "homeSecurity", "advertising", "gaming", "gambling", "sportsBetting", "socialMedia", "streaming", "movies", "musicAndAudio", "publishing", "telecommunications", "edtech", "proptech"],
    measure: Literal["Mean", "Median", "NetCount", "ArticleCount"] = "Mean",
    lookback_days: int = 1,
    include_member_companies: bool = False,
) -> Indicator:
    """News sentiment about an industry over its last lookbackDays daily news snapshots (default 1, the latest). industry is one of the canonical industry tags (the same list IsIndustry uses). Headlines come from the GDELT global news feed, and a fine-tuned model scores every headline that mentions the industry: whether it is really about the industry, and whether it is positive, neutral or negative news for it (a headline it calls neutral scores 0; others score from -1.0 to 1.0). Each trading day has one snapshot at 09:15 New York, and a headline counts toward the first session whose 09:15 cutoff is at or after the time GDELT published it. Backtests and live trading read the same rows. lookbackDays counts trading sessions whether or not the industry had news in them; a single session is noisy, and about 20 sessions is the usual window for a sentiment rule. Only headlines the model judges to be about the industry count, and copies of one story within 7 days count once per session. measure (default Mean): Mean is the average sentiment from -1.0 to 1.0, Median is the middle score (the average of the two middle scores for an even count), NetCount is positive minus negative headlines, ArticleCount is the number of qualifying headlines. Coverage begins on 2019-09-23: a window reaching earlier is measured over the sessions since then, and the backtest warns that it held less history than configured. Every measure has no value across sessions with no published snapshot, or when the latest snapshot due has not been published. Mean and Median also have no value when the window holds no qualifying headline; the counts are then 0. The value is the same for every asset: it takes no target asset. includeMemberCompanies (default false): also count headlines about the industry's member companies. A headline the model judges to be about a company (for example "SpaceX valuation hits a record") then counts toward every industry that company belongs to with core or material exposure in NexusTrade's industry classification, including companies not yet listed, by SEC CIK. A headline counts once per industry even when it names several member companies or also names the industry, scored by its industry score when it has one and otherwise by its score for the member company it is most about (ties to the numerically lowest SEC CIK); copies of one story count once per session across both kinds. Membership is the company's classification public when the headline appeared, or its first classification for headlines before that, so older history, and the history placed for a newly listed company, uses a membership that became known later. Default false reads exactly the headlines that name the industry.
    industry: Canonical industry tag whose news to read, as IsIndustry lists it. Legacy aliases are separate news series and are rejected, never mapped.
    measure: Median: middle score from -1 to 1 (the average of the two middle scores for an even count), with no value when no qualifying headline. Mean: average sentiment from -1 to 1, with no value when the window has no qualifying headline. NetCount: positive minus negative headlines. ArticleCount: qualifying headlines. Every measure has no value across missing snapshots or when the due snapshot is unpublished. Before coverage begins on 2019-09-23 the window holds the sessions available.
    lookback_days: Whole trading sessions of daily 09:15 New York news snapshots; 1 is the latest and about 20 is the usual window. The same value for every asset.
    include_member_companies: Also count headlines about the industry's member companies, each headline once per industry. Default false reads only headlines that name the industry.
    """
    d: Dict[str, Any] = {"type": "IndustryNewsSentiment"}
    d["industry"] = _enum(industry, ["technology","financialServices","healthcare","energy","consumerGoods","entertainmentAndMedia","manufacturing","transportation","realEstate","utilities","agriculture","education","retail","artificialIntelligence","aiInfrastructure","analytics","cloudComputing","dataCenter","database","cybersecurity","developerTools","software","saas","enterpriseSoftware","customerExperienceSoftware","productivityTools","collaborationSoftware","digitalSignatureAndAuthentication","hardware","consumerElectronics","networking","semiconductor","chipDesign","chipManufacturing","semiconductorEquipment","gpuAndAccelerators","memoryAndStorage","quantumComputing","robotics","industrialAutomation","iot","smartDevices","wearable","3dPrinting","augmentedReality","virtualReality","space","satellites","aerospace","defense","drones","financialInfrastructure","capitalMarketsTechnology","banking","assetManagement","brokerage","payments","fintech","insurance","autoInsurance","homeInsurance","lifeInsurance","healthInsurance","blockchain","cryptocurrency","biotechnology","pharmaceuticals","medicalDevices","digitalHealth","lifeSciencesSoftware","bioinformatics","computationalBiology","computationalDrugDiscovery","oncology","immunotherapy","vaccines","veterinary","oil","gas","oilfieldServices","cleanEnergy","renewableEnergy","solarEnergy","windEnergy","nuclearEnergy","hydrogen","batteryTechnology","miningAndNaturalResources","gold","silver","chemicals","advancedMaterials","waterInfrastructure","wasteManagementAndRecycling","forestry","construction","buildingMaterials","electricalAndGridInfrastructure","logisticsAndSupplyChain","airline","maritime","railroads","automotive","electricVehicle","autonomousTransportation","recreationalVehicle","cruise","rideShare","printing","packaging","householdProducts","ecommerce","digitalMarketplace","fashionAndApparel","luxuryGoods","jewelry","foodAndBeverage","foodDelivery","nutrition","petCare","outdoorAndRecreationalEquipment","sportsAndFitness","cannabis","hospitalityAndTravel","homeSecurity","advertising","gaming","gambling","sportsBetting","socialMedia","streaming","movies","musicAndAudio","publishing","telecommunications","edtech","proptech"], "industry")
    d["measure"] = _enum(measure, ["Mean","Median","NetCount","ArticleCount"], "measure")
    d["lookbackDays"] = lookback_days
    d["includeMemberCompanies"] = include_member_companies
    return Indicator(d)

__all__.append("IndustryNewsSentiment")

def InitialValue() -> Indicator:
    """The starting value of the portfolio when it was created or when the backtest began. Range: > 0.
    """
    d: Dict[str, Any] = {"type": "InitialValue"}
    return Indicator(d)

__all__.append("InitialValue")

def InsiderTrades(
    asset: Union[str, Dict[str, Any], _Candidate],
    metric: Literal["NetValue", "BuyValue", "SellValue", "NetShares", "BuyCount", "SellCount", "AwardCount", "AcquisitionValue", "AcquisitionCount", "DistinctBuyers", "PurchaseSharePercent", "AcquisitionSharePercent"],
    role: Literal["Any", "Officer", "Director", "TenPercentOwner", "OfficerOrDirector", "Ceo"],
    window_days: float,
    owner_cik: Optional[str] = None,
    issuer_cik: Optional[str] = None,
    instrument: Optional[Literal["Equity", "Call", "Put"]] = None,
) -> Indicator:
    """Disclosed purchases (code P, including open-market or private transactions), sales (code S), and a separate exact-owner AwardCount for code A issuer grants or other acquisitions over a trailing publication window. AcquisitionCount combines code P and code A as distinct acquisition signals for modeled follower strategies; awards are not reclassified as purchases or sales and do not prove vested common-share ownership. Exact-owner strategies use versioned common-share, call/put, and issuer-award evidence; exercises and warrants are excluded from tradable purchase signals. A signal becomes eligible when its filing is public, never on an earlier transaction date. Range: Dollars, shares, counts or budget percent; unknown owner/amendment coverage has no value.
    asset: Pass CANDIDATE in a dynamic rebalance pipeline.
    owner_cik: Exact nonzero SEC reporting owner CIK, up to 10 digits. Omit for a whole company (with issuerCik) or aggregate activity.
    issuer_cik: Company CIK. With ownerCik, restricts that owner to this company. Alone, every reporting owner's events at this company, a joint filing counted once.
    instrument: Disclosed instrument; defaults to Equity. Call/Put, award and acquisition metrics, and budget shares require ownerCik or issuerCik. AwardCount is SEC code A issuer acquisitions, not cash purchases or vested-share proof. AcquisitionValue is disclosed purchase dollars plus award shares at the target's current price. Its default Equity bucket includes unresolved restricted units. AcquisitionCount and AcquisitionSharePercent combine public code P purchases with code A issuer acquisitions. Shares use qualifying signal counts across all the owner's tickers, or all the company's tickers under issuerCik alone.
    """
    d: Dict[str, Any] = {"type": "InsiderTrades"}
    _set_asset(d, "targetAsset", asset)
    d["metric"] = _enum(metric, ["NetValue","BuyValue","SellValue","NetShares","BuyCount","SellCount","AwardCount","AcquisitionValue","AcquisitionCount","DistinctBuyers","PurchaseSharePercent","AcquisitionSharePercent"], "metric")
    d["role"] = _enum(role, ["Any","Officer","Director","TenPercentOwner","OfficerOrDirector","Ceo"], "role")
    d["windowDays"] = window_days
    if owner_cik is not None:
        d["ownerCik"] = owner_cik
    if issuer_cik is not None:
        d["issuerCik"] = issuer_cik
    if instrument is not None:
        d["instrument"] = _enum(instrument, ["Equity","Call","Put"], "instrument")
    return Indicator(d)

__all__.append("InsiderTrades")

def InstitutionalHoldings(
    asset: Union[str, Dict[str, Any], _Candidate],
    manager: str,
    metric: Literal["HolderCount", "TotalShares", "TotalValue", "BookSharePercent", "ConcentrationTop5", "NetShareChange", "NetHolderChange", "NewHolders", "ClosedHolders"] = "HolderCount",
    window_days: float = 180,
    instrument: Literal["Equity", "Call", "Put"] = "Equity",
) -> Indicator:
    """Institutional positions in the asset disclosed on SEC Form 13F. Naming a manager follows one institution; an empty manager reads every manager holding the asset (SDK code passes an empty string). Level metrics read the latest quarter whose filing became public on or before the evaluation date; change metrics compare it with the quarter before. Positions are never summed across quarters, and an amended filing replaces the original only from the moment the amendment became public. Range: Counts, shares, dollars or a top-5 share of holdings; 0 when no 13F naming the asset was public within the window.
    asset: Ticker name (ex. SPY, BTC)
    manager: Optional: a CIK such as 1067983, or part of a manager's name such as Renaissance Technologies. Leave empty for every manager. A CIK is exact; a name can match several firms
    metric: Managers holding, shares or value held, concentration, or the change since the previous quarter
    window_days: Read a disclosed period only if its filing became public within this many trailing days
    instrument: Calls and puts require an exact manager CIK and its effective public book
    """
    d: Dict[str, Any] = {"type": "InstitutionalHoldings"}
    _set_asset(d, "targetAsset", asset)
    d["manager"] = manager
    d["metric"] = _enum(metric, ["HolderCount","TotalShares","TotalValue","BookSharePercent","ConcentrationTop5","NetShareChange","NetHolderChange","NewHolders","ClosedHolders"], "metric")
    d["windowDays"] = window_days
    d["instrument"] = _enum(instrument, ["Equity","Call","Put"], "instrument")
    return Indicator(d)

__all__.append("InstitutionalHoldings")

def IsAsset(
    match_asset: Union[str, Dict[str, Any], _Candidate],
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Returns 1 if the asset matches a specific symbol. Useful for including specific assets in multi-asset strategies.
    match_asset: The specific asset to compare against (ex. UPRO, GLD)
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "IsAsset"}
    _set_asset(d, "matchAsset", match_asset)
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("IsAsset")

def IsAssetType(
    asset: Union[str, Dict[str, Any], _Candidate],
    asset_type: Literal["Stock", "Cryptocurrency", "Option", "Other"] = "Stock",
) -> Indicator:
    """Returns 1 if the asset matches Stock, Cryptocurrency, Option or Other, and 0 otherwise. ETFs are Stock assets; use separate stock metadata or a universe filter to select ETFs.
    asset: Ticker name (ex. SPY, BTC)
    asset_type: The asset type to check against
    """
    d: Dict[str, Any] = {"type": "IsAssetType"}
    _set_asset(d, "targetAsset", asset)
    d["assetType"] = _enum(asset_type, ["Stock","Cryptocurrency","Option","Other"], "asset_type")
    return Indicator(d)

__all__.append("IsAssetType")

def IsIndexMember(
    asset: Union[str, Dict[str, Any], _Candidate],
    index: Literal["SP500", "NASDAQ100", "DJIA"] = "SP500",
) -> Indicator:
    """Returns 1 if the asset is a member of a specified index, 0 otherwise.
    asset: Ticker name (ex. SPY, BTC)
    index: The index to check membership against
    """
    d: Dict[str, Any] = {"type": "IsIndexMember"}
    _set_asset(d, "targetAsset", asset)
    d["index"] = _enum(index, ["SP500","NASDAQ100","DJIA"], "index")
    return Indicator(d)

__all__.append("IsIndexMember")

def IsIndustry(
    asset: Union[str, Dict[str, Any], _Candidate],
    industry: Literal["technology", "financialServices", "healthcare", "energy", "consumerGoods", "entertainmentAndMedia", "manufacturing", "transportation", "realEstate", "utilities", "agriculture", "education", "retail", "artificialIntelligence", "aiInfrastructure", "analytics", "cloudComputing", "dataCenter", "database", "cybersecurity", "developerTools", "software", "saas", "enterpriseSoftware", "customerExperienceSoftware", "productivityTools", "collaborationSoftware", "digitalSignatureAndAuthentication", "hardware", "consumerElectronics", "networking", "semiconductor", "chipDesign", "chipManufacturing", "semiconductorEquipment", "gpuAndAccelerators", "memoryAndStorage", "quantumComputing", "robotics", "industrialAutomation", "iot", "smartDevices", "wearable", "3dPrinting", "augmentedReality", "virtualReality", "space", "satellites", "aerospace", "defense", "drones", "financialInfrastructure", "capitalMarketsTechnology", "banking", "assetManagement", "brokerage", "payments", "fintech", "insurance", "autoInsurance", "homeInsurance", "lifeInsurance", "healthInsurance", "blockchain", "cryptocurrency", "biotechnology", "pharmaceuticals", "medicalDevices", "digitalHealth", "lifeSciencesSoftware", "bioinformatics", "computationalBiology", "computationalDrugDiscovery", "oncology", "immunotherapy", "vaccines", "veterinary", "oil", "gas", "oilfieldServices", "cleanEnergy", "renewableEnergy", "solarEnergy", "windEnergy", "nuclearEnergy", "hydrogen", "batteryTechnology", "miningAndNaturalResources", "gold", "silver", "chemicals", "advancedMaterials", "waterInfrastructure", "wasteManagementAndRecycling", "forestry", "construction", "buildingMaterials", "electricalAndGridInfrastructure", "logisticsAndSupplyChain", "airline", "maritime", "railroads", "automotive", "electricVehicle", "autonomousTransportation", "recreationalVehicle", "cruise", "rideShare", "printing", "packaging", "householdProducts", "ecommerce", "digitalMarketplace", "fashionAndApparel", "luxuryGoods", "jewelry", "foodAndBeverage", "foodDelivery", "nutrition", "petCare", "outdoorAndRecreationalEquipment", "sportsAndFitness", "cannabis", "hospitalityAndTravel", "homeSecurity", "advertising", "gaming", "gambling", "sportsBetting", "socialMedia", "streaming", "movies", "musicAndAudio", "publishing", "telecommunications", "edtech", "proptech", "informationTechnology", "personalAndHouseholdGoods", "investing", "spaceExploration", "alternativeEnergy", "clothesAndApparal", "computationalChemistry", "customerEngagement", "dataVisualization", "graphicsCard", "materialScience", "medicine", "messaging", "phones", "security", "socialNetwork", "television", "videoConferencing", "videos", "waterPurifaction", "waterTreatment"],
) -> Indicator:
    """Returns 1 if the asset belongs to a specified industry, 0 otherwise. Over 100 categories supported including Artificial Intelligence, Semiconductor, Electric Vehicles, Space Exploration, Healthcare, Biotechnology, Cloud Computing, Cybersecurity, Renewable Energy, and many more.
    asset: Ticker name (ex. SPY, BTC)
    industry: The industry to check membership against
    """
    d: Dict[str, Any] = {"type": "IsIndustry"}
    _set_asset(d, "targetAsset", asset)
    d["industry"] = _enum({"informationTechnology":"technology","personalAndHouseholdGoods":"householdProducts","investing":"assetManagement","spaceExploration":"space","alternativeEnergy":"cleanEnergy","clothesAndApparal":"fashionAndApparel","computationalChemistry":"computationalDrugDiscovery","customerEngagement":"customerExperienceSoftware","dataVisualization":"analytics","graphicsCard":"gpuAndAccelerators","materialScience":"advancedMaterials","medicine":"pharmaceuticals","messaging":"collaborationSoftware","phones":"consumerElectronics","security":"cybersecurity","socialNetwork":"socialMedia","television":"streaming","videoConferencing":"collaborationSoftware","videos":"streaming","waterPurifaction":"waterInfrastructure","waterTreatment":"waterInfrastructure"}.get(industry, industry), ["technology","financialServices","healthcare","energy","consumerGoods","entertainmentAndMedia","manufacturing","transportation","realEstate","utilities","agriculture","education","retail","artificialIntelligence","aiInfrastructure","analytics","cloudComputing","dataCenter","database","cybersecurity","developerTools","software","saas","enterpriseSoftware","customerExperienceSoftware","productivityTools","collaborationSoftware","digitalSignatureAndAuthentication","hardware","consumerElectronics","networking","semiconductor","chipDesign","chipManufacturing","semiconductorEquipment","gpuAndAccelerators","memoryAndStorage","quantumComputing","robotics","industrialAutomation","iot","smartDevices","wearable","3dPrinting","augmentedReality","virtualReality","space","satellites","aerospace","defense","drones","financialInfrastructure","capitalMarketsTechnology","banking","assetManagement","brokerage","payments","fintech","insurance","autoInsurance","homeInsurance","lifeInsurance","healthInsurance","blockchain","cryptocurrency","biotechnology","pharmaceuticals","medicalDevices","digitalHealth","lifeSciencesSoftware","bioinformatics","computationalBiology","computationalDrugDiscovery","oncology","immunotherapy","vaccines","veterinary","oil","gas","oilfieldServices","cleanEnergy","renewableEnergy","solarEnergy","windEnergy","nuclearEnergy","hydrogen","batteryTechnology","miningAndNaturalResources","gold","silver","chemicals","advancedMaterials","waterInfrastructure","wasteManagementAndRecycling","forestry","construction","buildingMaterials","electricalAndGridInfrastructure","logisticsAndSupplyChain","airline","maritime","railroads","automotive","electricVehicle","autonomousTransportation","recreationalVehicle","cruise","rideShare","printing","packaging","householdProducts","ecommerce","digitalMarketplace","fashionAndApparel","luxuryGoods","jewelry","foodAndBeverage","foodDelivery","nutrition","petCare","outdoorAndRecreationalEquipment","sportsAndFitness","cannabis","hospitalityAndTravel","homeSecurity","advertising","gaming","gambling","sportsBetting","socialMedia","streaming","movies","musicAndAudio","publishing","telecommunications","edtech","proptech"], "industry")
    return Indicator(d)

__all__.append("IsIndustry")

def IsNotAsset(
    match_asset: Union[str, Dict[str, Any], _Candidate],
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Returns 1 if the asset does NOT match a specific symbol. Useful for excluding specific assets.
    match_asset: The specific asset to compare against (ex. UPRO, GLD)
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "IsNotAsset"}
    _set_asset(d, "matchAsset", match_asset)
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("IsNotAsset")

def IVHVRatio(
    asset: Union[str, Dict[str, Any], _Candidate],
    hv_window: float = 20,
) -> Indicator:
    """Constant-maturity ATM implied volatility divided by annualized close-to-close realized volatility (HV) over hvWindow trading days. Values > 1 mean options are pricing more vol than recently realized. Used on each candidate of a dynamic rebalance over a broad universe (all US stocks, an index or a watchlist), it reads options chains with Greeks for every stock in that universe instead of one underlying, so the run is heavier than with a named ticker or a specific asset list. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    hv_window: Trading days of log-return history for realized volatility
    """
    d: Dict[str, Any] = {"type": "IVHVRatio"}
    _set_asset(d, "targetAsset", asset)
    d["hvWindow"] = hv_window
    return Indicator(d)

__all__.append("IVHVRatio")

def IVPercentile(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 252,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Percent of sessions in the lookback window whose constant-maturity ATM IV was strictly below today's (0–100). Distinct from IV Rank. Used on each candidate of a dynamic rebalance over a broad universe (all US stocks, an index or a watchlist), it reads options chains with Greeks for every stock in that universe instead of one underlying, so the run is heavier than with a named ticker or a specific asset list. Range: 0–100.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IVPercentile"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IVPercentile")

def IVRank(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 252,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Where today's constant-maturity ATM IV sits between the lookback window's high and low: (IV − IV_low) / (IV_high − IV_low) × 100. Distinct from IV Percentile. Used on each candidate of a dynamic rebalance over a broad universe (all US stocks, an index or a watchlist), it reads options chains with Greeks for every stock in that universe instead of one underlying, so the run is heavier than with a named ticker or a specific asset list. Range: 0–100.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "IVRank"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("IVRank")

def KeltnerChannel(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 20,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    multiplier: float = 2,
    line: Literal["upper", "middle", "lower"] = "middle",
) -> Indicator:
    """EMA midline with ATR bands above and below. Tighter than Bollinger in calm tape, so closes outside the bands mean stronger conviction moves. Range: Price scale.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    multiplier: How many ATRs from the EMA midline
    line: Which Keltner line to evaluate
    """
    d: Dict[str, Any] = {"type": "KeltnerChannel"}
    _set_asset(d, "targetAsset", asset)
    d["multiplier"] = multiplier
    d["line"] = _enum(line, ["upper","middle","lower"], "line")
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("KeltnerChannel")

def LastDividend(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The most recent regular cash dividend per share whose ex-dividend date is on or before the evaluation date (America/New_York), split-adjusted onto today's share basis, special dividends excluded. Returns null when the asset has no regular dividend on or before that date. Each payment is one observation for TrailingSum, so TrailingSum(LastDividend(asset), 4) sums the last four payments, which is trailing-twelve-month dividends for a quarterly payer (use 12 for a monthly payer).
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "LastDividend"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("LastDividend")

def LastOrderPrice(
    asset: Union[str, Dict[str, Any], _Candidate],
    side: Literal["Buy", "Sell"],
    order_status: Literal["Pending", "Accepted", "Pending User Approval", "Canceled", "Filled", "Partially Filled"] = "Filled",
) -> Indicator:
    """The execution price of the most recent order matching the specified filters. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    side: Matches order events with this side (buy or sell)
    order_status: Matches order events with this status
    """
    d: Dict[str, Any] = {"type": "LastOrderPrice"}
    _set_asset(d, "targetAsset", asset)
    d["side"] = _enum(side, ["Buy","Sell"], "side")
    d["orderStatus"] = _enum(order_status, ["Pending","Accepted","Pending User Approval","Canceled","Filled","Partially Filled"], "order_status")
    return Indicator(d)

__all__.append("LastOrderPrice")

def Log(
    operand: Indicator,
    base: float = 10,
) -> Indicator:
    """Logarithm of an indicator with a specified base.
    base: The base for the logarithm
    """
    d: Dict[str, Any] = {"type": "Log"}
    d["base"] = base
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("Log")

def LowOfDay(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The lowest price reached during the current trading session. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "LowOfDay"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("LowOfDay")

def Max(
    left: Indicator,
    right: Indicator,
) -> Indicator:
    """The maximum value of two or more indicators. The SDK builder takes exactly two operands; nest it to compare more.
    """
    d: Dict[str, Any] = {"type": "Max"}
    d["indicators"] = [left.d, right.d]
    return Indicator(d)

__all__.append("Max")

def MaxDrawdown(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """The largest peak-to-trough percentage decline of the asset's price inside the window, reported as a positive number: 10 means a 10% fall from a high to a later low, and 0 means the price never fell below an earlier price in the window. Measures worst-case downside risk. Range: 0 to 100 (percent, never negative).
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "MaxDrawdown"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("MaxDrawdown")

def MaxDrawup(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """The maximum trough-to-peak percentage increase over a window. Range: >= 0 (percentage).
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "MaxDrawup"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("MaxDrawup")

def MaximumPrice(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """The highest of the asset's observed prices over a window, including the current one. Each observation is the price at that time (a completed day contributes its close, a completed minute its closing price), not an intrabar high. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "MaximumPrice"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("MaximumPrice")

def Min(
    left: Indicator,
    right: Indicator,
) -> Indicator:
    """The minimum value of two or more indicators. The SDK builder takes exactly two operands; nest it to compare more.
    """
    d: Dict[str, Any] = {"type": "Min"}
    d["indicators"] = [left.d, right.d]
    return Indicator(d)

__all__.append("Min")

def MinimumPrice(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """The lowest of the asset's observed prices over a window, including the current one. Each observation is the price at that time (a completed day contributes its close, a completed minute its closing price), not an intrabar low. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "MinimumPrice"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("MinimumPrice")

def Minus(
    left: Indicator,
    right: Indicator,
) -> Indicator:
    """Difference between indicators.
    """
    d: Dict[str, Any] = {"type": "Minus"}
    d["indicators"] = [left.d, right.d]
    return Indicator(d)

__all__.append("Minus")

def MinuteBarHigh(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The high of the most recently COMPLETED one-minute bar. The bar that opens at 9:30 is readable from 9:31, identically in backtests and live trading; the forming bar is never used. Not the day's high (use High of Day). Pre-market and after-hours bars count. Strategies using it run on one-minute data. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "MinuteBarHigh"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("MinuteBarHigh")

def MinuteBarLow(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The low of the most recently COMPLETED one-minute bar. The bar that opens at 9:30 is readable from 9:31, identically in backtests and live trading; the forming bar is never used. Not the day's low (use Low of Day). Pre-market and after-hours bars count. Strategies using it run on one-minute data. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "MinuteBarLow"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("MinuteBarLow")

def MinutesAfterOpen() -> Indicator:
    """Whole minutes since the regular US market open (9:30 AM ET), read from the Eastern clock. The value is not clamped to the session: it is negative before the open and keeps rising above 390 after the 4:00 PM close. Range: 0 to 390 during a regular session; negative before 9:30 AM ET and above 390 after the close.
    """
    d: Dict[str, Any] = {"type": "MinutesAfterOpen"}
    return Indicator(d)

__all__.append("MinutesAfterOpen")

def MinutesSinceAgent() -> Indicator:
    """The number of minutes since an AI agent was launched by this strategy. Useful for fine-grained rate-limiting of agent launches within a single day. Range: >= 0.
    """
    d: Dict[str, Any] = {"type": "MinutesSinceAgent"}
    return Indicator(d)

__all__.append("MinutesSinceAgent")

def MinutesSinceAlert(
    message: str,
) -> Indicator:
    """The number of minutes since an alert was triggered. Useful for fine-grained rate-limiting of alerts within a single day. Range: >= 0.
    message: (Optional) Message to display when the indicator is triggered
    """
    d: Dict[str, Any] = {"type": "MinutesSinceAlert"}
    d["message"] = message
    return Indicator(d)

__all__.append("MinutesSinceAlert")

def MinutesSinceOptionOrder(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
    position_effect: Literal["open", "close"],
    min_dte: float,
    max_dte: float,
    order_status: Literal["Pending", "Accepted", "Pending User Approval", "Canceled", "Filled", "Partially Filled"] = "Filled",
) -> Indicator:
    """The number of minutes since the most recent option order matching the specified filters. Same filters as Days Since Option Order but measured in minutes for intraday strategies. Range: >= 0 (minutes), or a large number if no matching order exists.
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    position_effect: Filter by Open (entering position) or Close (exiting). Leave empty for all.
    min_dte: Minimum days to expiration at order time. Leave empty for no minimum.
    max_dte: Maximum days to expiration at order time. Leave empty for no maximum.
    order_status: Matches option order events with this status
    """
    d: Dict[str, Any] = {"type": "MinutesSinceOptionOrder"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    d["positionEffect"] = _enum(position_effect, ["open","close"], "position_effect")
    d["minDte"] = min_dte
    d["maxDte"] = max_dte
    d["orderStatus"] = _enum(order_status, ["Pending","Accepted","Pending User Approval","Canceled","Filled","Partially Filled"], "order_status")
    return Indicator(d)

__all__.append("MinutesSinceOptionOrder")

def MinutesSinceOrder(
    assets: Union[str, Sequence[Any]],
    side: Literal["Buy", "Sell"],
    order_status: Literal["Pending", "Accepted", "Pending User Approval", "Canceled", "Filled", "Partially Filled"] = "Filled",
    clock: Optional[Literal["Calendar", "Trading"]] = None,
) -> Indicator:
    """Same as Days Since Order but measured in minutes. Useful for intraday strategies. Range: >= 0 (minutes).
    assets: Ticker(s) whose orders are counted. One string or an array.
    side: Buy or Sell
    clock: Optional elapsed-time clock; omitted retains calendar minutes.
    """
    d: Dict[str, Any] = {"type": "MinutesSinceOrder"}
    _set_assets(d, "targetAssets", assets)
    d["side"] = _enum(side, ["Buy","Sell"], "side")
    d["orderStatus"] = _enum(order_status, ["Pending","Accepted","Pending User Approval","Canceled","Filled","Partially Filled"], "order_status")
    if clock is not None:
        d["clock"] = _enum(clock, ["Calendar","Trading"], "clock")
    return Indicator(d)

__all__.append("MinutesSinceOrder")

def MinutesUntilClose() -> Indicator:
    """Whole minutes until the US equity session close, read from the Eastern clock. The close is that day's real session close, so an early-close day counts down to 1:00 PM ET instead of 4:00 PM. The value is not clamped to the session: it is negative after the close and above 390 before the 9:30 AM open. Range: 0 to 390 during a regular session (0 to 210 on a 1:00 PM early close); negative after the close and above 390 before the open.
    """
    d: Dict[str, Any] = {"type": "MinutesUntilClose"}
    return Indicator(d)

__all__.append("MinutesUntilClose")

def MoneyFlowIndex(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 14,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Volume-weighted RSI: money flow direction over N bars on a 0-100 scale. Above 80 is overbought, below 20 oversold. Requires volume data. Range: 0 to 100.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "MoneyFlowIndex"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("MoneyFlowIndex")

def Month() -> Indicator:
    """The current month (1–12).
    """
    d: Dict[str, Any] = {"type": "Month"}
    return Indicator(d)

__all__.append("Month")

def MovingAverageConvergenceDivergence(
    asset: Union[str, Dict[str, Any], _Candidate],
    fast_length: float = 12,
    slow_length: float = 26,
    signal_length: float = 9,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    line: Literal["macd", "signal", "histogram"] = "macd",
) -> Indicator:
    """MACD line (fast EMA minus slow EMA), its signal EMA, or the histogram (MACD minus signal). Classic trend/momentum indicator. Range: Positive or negative for all three lines, in price units: each is a difference of moving averages, not a price level.
    asset: Ticker name (ex. SPY, BTC)
    fast_length: Period of the fast EMA
    slow_length: Period of the slow EMA (must exceed the fast length)
    signal_length: Period of the EMA applied to the MACD line
    interval: Bar interval the MACD is computed on
    line: Which MACD line to evaluate
    """
    d: Dict[str, Any] = {"type": "MovingAverageConvergenceDivergence"}
    _set_asset(d, "targetAsset", asset)
    d["fastLength"] = fast_length
    d["slowLength"] = slow_length
    d["signalLength"] = signal_length
    d["interval"] = _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")
    d["line"] = _enum(line, ["macd","signal","histogram"], "line")
    return Indicator(d)

__all__.append("MovingAverageConvergenceDivergence")

def Multiply(
    left: Indicator,
    right: Indicator,
) -> Indicator:
    """Product of indicators.
    """
    d: Dict[str, Any] = {"type": "Multiply"}
    d["indicators"] = [left.d, right.d]
    return Indicator(d)

__all__.append("Multiply")

def Negative(
    operand: Indicator,
) -> Indicator:
    """Negates an indicator's value.
    """
    d: Dict[str, Any] = {"type": "Negative"}
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("Negative")

def NewInsiderDisclosure(
    owner_cik: Optional[str] = None,
    issuer_cik: Optional[str] = None,
    role: Literal["Any", "Officer", "Director", "TenPercentOwner", "OfficerOrDirector", "Ceo"] = "Any",
    instrument: Literal["Equity", "Call", "Put"] = "Equity",
    disclosure_action: Literal["Any", "Purchase", "Sale", "Award"] = "Any",
) -> Indicator:
    """True when a newly public Form 4 version matches the selected owner, issuer, role, instrument and action and awaits a strategy decision. Exact-owner corrections count as new versions. A company CIK without an owner watches every reporting owner at that company, and a joint filing fires once. A handled event does not fire again on later price ticks; unverified owner coverage has no value, and a company is only as covered as its least-covered matching owner. Range: 1 while a public Form 4 version awaits a decision; otherwise 0.
    owner_cik: Exact SEC reporting owner CIK. Omit for a whole company (with issuerCik) or aggregate Form 4 activity.
    issuer_cik: Company CIK. With ownerCik, restricts that owner; alone, every reporting owner at this company, a joint filing once.
    role: The reporting owner's role.
    instrument: Call and Put require ownerCik or issuerCik.
    disclosure_action: Which newly public Form 4 action triggers a decision. Award requires ownerCik or issuerCik.
    """
    d: Dict[str, Any] = {"type": "NewInsiderDisclosure"}
    if owner_cik is not None:
        d["ownerCik"] = owner_cik
    if issuer_cik is not None:
        d["issuerCik"] = issuer_cik
    d["role"] = _enum(role, ["Any","Officer","Director","TenPercentOwner","OfficerOrDirector","Ceo"], "role")
    d["instrument"] = _enum(instrument, ["Equity","Call","Put"], "instrument")
    d["disclosureAction"] = _enum(disclosure_action, ["Any","Purchase","Sale","Award"], "disclosure_action")
    return Indicator(d)

__all__.append("NewInsiderDisclosure")

def NewInstitutionalFiling(
    manager: str,
) -> Indicator:
    """True when an exact manager CIK has a newly public 13F filing or amendment this strategy has not handled. Each accession creates one decision; later price ticks do not repeat it. The current InstitutionalHoldings book still sets target positions. Range: 1 while a public filing awaits a decision; otherwise 0.
    manager: Exact numeric SEC manager CIK. A new 13F accession, including an amendment, triggers one decision.
    """
    d: Dict[str, Any] = {"type": "NewInstitutionalFiling"}
    d["manager"] = manager
    return Indicator(d)

__all__.append("NewInstitutionalFiling")

def NewPoliticalDisclosure(
    member_id: str,
    instrument: Literal["Equity", "Option"],
    disclosure_action: Literal["Any", "Purchase", "Sale"],
) -> Indicator:
    """True when a selected member has a newly public equity or option trade disclosure that this strategy has not handled. Corrections count when they become public. A handled disclosure does not fire again on later price ticks. Range: 1 while a new disclosure awaits a decision; otherwise 0.
    member_id: Exact Bioguide member ID, such as P000197.
    instrument: Equity or option disclosures, never both for a public bot arm.
    disclosure_action: Which newly public trade sides trigger a decision.
    """
    d: Dict[str, Any] = {"type": "NewPoliticalDisclosure"}
    d["memberId"] = member_id
    d["instrument"] = _enum(instrument, ["Equity","Option"], "instrument")
    d["disclosureAction"] = _enum(disclosure_action, ["Any","Purchase","Sale"], "disclosure_action")
    return Indicator(d)

__all__.append("NewPoliticalDisclosure")

def NewsSentiment(
    asset: Union[str, Dict[str, Any], _Candidate],
    measure: Literal["Mean", "Median", "NetCount", "ArticleCount"] = "Mean",
    lookback_days: int = 1,
) -> Indicator:
    """News sentiment about the asset over its last lookbackDays daily news snapshots (default 1, the latest). Headlines come from the GDELT global news feed, and a fine-tuned model scores every headline that names the company: whether it is really about the company, and whether it is positive, neutral or negative news for it (a headline it calls neutral scores 0; others score from -1.0 to 1.0). Each trading day has one snapshot at 09:15 New York, and a headline counts toward the first session whose 09:15 cutoff is at or after the time GDELT published it. Backtests and live trading read the same rows. lookbackDays counts trading sessions whether or not the asset had news in them; a single session is noisy, and about 20 sessions is the usual window for a sentiment rule. Only headlines the model judges to be about the company count, and copies of one story within 7 days count once per session. measure (default Mean): Mean is the average sentiment from -1.0 to 1.0, Median is the middle score (the average of the two middle scores for an even count), NetCount is positive minus negative headlines, ArticleCount is the number of qualifying headlines. Coverage begins on 2019-09-23: a window reaching earlier is measured over the sessions since then, and the backtest warns that it held less history than configured. Every measure has no value across sessions with no published snapshot, or when the latest snapshot due has not been published. Mean and Median also have no value when the window holds no qualifying headline; the counts are then 0. Headlines are matched to the issuer, so every listed share class reads the same news and META reads Facebook-era headlines.
    asset: Pass CANDIDATE in a dynamic rebalance pipeline.
    measure: Median: middle score from -1 to 1, with no value when no qualifying news. Mean: average sentiment from -1 to 1, with no value when the window has no qualifying news. NetCount: positive minus negative headlines. ArticleCount: qualifying headlines. Every measure has no value across missing snapshots or when the due snapshot is unpublished. Before coverage begins on 2019-09-23 the window holds the sessions available.
    lookback_days: Whole trading sessions of daily 09:15 New York news snapshots; 1 is the latest. A row is visible from the snapshot the lake assigned it, the same time in backtests and live trading.
    """
    d: Dict[str, Any] = {"type": "NewsSentiment"}
    _set_asset(d, "targetAsset", asset)
    d["measure"] = _enum(measure, ["Mean","Median","NetCount","ArticleCount"], "measure")
    d["lookbackDays"] = lookback_days
    return Indicator(d)

__all__.append("NewsSentiment")

def NewTradingWeek() -> Indicator:
    """True on the first evaluated trading tick of an ISO week until the strategy makes a decision. A no-order decision still consumes that week, and live portfolios persist the decision through a restart. Use with an option disclosure trigger for modeled maintenance. Range: 1 while this week's first decision is pending; otherwise 0.
    """
    d: Dict[str, Any] = {"type": "NewTradingWeek"}
    return Indicator(d)

__all__.append("NewTradingWeek")

def OnBalanceVolume(
    asset: Union[str, Dict[str, Any], _Candidate],
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Running total of signed volume: adds the bar's volume on up closes, subtracts on down closes. Rising OBV with flat price is accumulation. Range: Unbounded (cumulative).
    asset: Ticker name (ex. SPY, BTC)
    interval: Bar interval the volume is accumulated on
    """
    d: Dict[str, Any] = {"type": "OnBalanceVolume"}
    _set_asset(d, "targetAsset", asset)
    d["interval"] = _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")
    return Indicator(d)

__all__.append("OnBalanceVolume")

def OpeningPrice(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The price of an asset at market open. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "OpeningPrice"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("OpeningPrice")

def OptionCollateral(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
) -> Indicator:
    """Returns the total collateral locked across option spreads matching the filter. Range: 0 to ∞ (dollars).
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    """
    d: Dict[str, Any] = {"type": "OptionCollateral"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    return Indicator(d)

__all__.append("OptionCollateral")

def OptionDaysHeld(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
    min_or_max: Literal["min", "max"] = "min",
) -> Indicator:
    """Returns the minimum or maximum days since entry across option spreads matching the filter. Range: 0 to ∞.
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    min_or_max: Return minimum or maximum value across matching spreads.
    """
    d: Dict[str, Any] = {"type": "OptionDaysHeld"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    d["minOrMax"] = _enum(min_or_max, ["min","max"], "min_or_max")
    return Indicator(d)

__all__.append("OptionDaysHeld")

def OptionDaysToExpiration(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
    min_or_max: Literal["min", "max"] = "min",
) -> Indicator:
    """Returns the minimum or maximum days to expiration across option spreads matching the filter. Range: 0 to 365+.
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    min_or_max: Return minimum or maximum value across matching spreads.
    """
    d: Dict[str, Any] = {"type": "OptionDaysToExpiration"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    d["minOrMax"] = _enum(min_or_max, ["min","max"], "min_or_max")
    return Indicator(d)

__all__.append("OptionDaysToExpiration")

def OptionGrossExposurePercent() -> Indicator:
    """Sum of absolute option leg marks divided by portfolio value (percent). Matches gross deployment audits. Unfiltered portfolio-wide only. Range: 0% to ∞.
    """
    d: Dict[str, Any] = {"type": "OptionGrossExposurePercent"}
    return Indicator(d)

__all__.append("OptionGrossExposurePercent")

def OptionPositionCount(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
) -> Indicator:
    """Returns the number of open option contract positions/legs matching the filter criteria. Portfolio-wide (no underlying, option type, direction, or spread filters): open legs plus legs reserved by earlier true OpenOption signals in the same tick's strategy loop. Filtered counts (e.g. per ticker) use open legs only. Use Option Spread Count for multi-leg spread groups. Range: 0 to ∞.
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    """
    d: Dict[str, Any] = {"type": "OptionPositionCount"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    return Indicator(d)

__all__.append("OptionPositionCount")

def OptionPositionMaxDrawdown(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
) -> Indicator:
    """Peak-to-current drawdown of the aggregated P&L percentage across option spreads matching the filter. Stateful counterpart of the Option Position P&L% indicator (OptionPositionPercentChange): while that indicator returns the current P&L percentage, this one tracks the highest value observed and returns how far the position has fallen from that peak. Resets when no matching spreads remain so a fresh entry starts from a clean peak. Range: >= 0 (percentage points of P&L).
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    """
    d: Dict[str, Any] = {"type": "OptionPositionMaxDrawdown"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    return Indicator(d)

__all__.append("OptionPositionMaxDrawdown")

def OptionPositionPercentChange(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
) -> Indicator:
    """Returns the weighted average P&L percentage across option spreads matching the filter. Range: -100% to ∞.
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    """
    d: Dict[str, Any] = {"type": "OptionPositionPercentChange"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    return Indicator(d)

__all__.append("OptionPositionPercentChange")

def OptionPositionValue(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
) -> Indicator:
    """Returns the total market value of option spreads matching the filter criteria. Range: 0 to ∞ (dollars).
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    """
    d: Dict[str, Any] = {"type": "OptionPositionValue"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    return Indicator(d)

__all__.append("OptionPositionValue")

def OptionRealizedPnL(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
    lookback_days: Optional[float] = None,
) -> Indicator:
    """Returns the net realized P&L in dollars from CLOSED option positions matching the filter, long and short alike. Losing closes count as negative. Measures closes only and subtracts nothing for premium already spent. Lookback Days limits it to closes within that many days of the evaluation date; omit it for the whole life of the book. Range: -∞ to ∞ (dollars).
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    lookback_days: Only count activity within this many days of the evaluation date. Leave empty for the whole life of the book.
    """
    d: Dict[str, Any] = {"type": "OptionRealizedPnL"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    if lookback_days is not None:
        d["lookbackDays"] = lookback_days
    return Indicator(d)

__all__.append("OptionRealizedPnL")

def OptionRealizedPremium(
    underlying: str,
    lookback_days: Optional[float] = None,
) -> Indicator:
    """Returns realized option premium still available to spend: net realized P&L from closed short option positions, minus premium already spent by opens sized with Percent of Realized Premium. Proceeds from closing long options are not added back. This is the balance that allocation sizes against. It goes negative when closed shorts lost money, and a negative balance funds nothing. Lookback Days limits it to activity within that many days of the evaluation date; omit it for the whole life of the book. Range: -∞ to ∞ (dollars).
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    lookback_days: Only count activity within this many days of the evaluation date. Leave empty for the whole life of the book.
    """
    d: Dict[str, Any] = {"type": "OptionRealizedPremium"}
    d["underlying"] = underlying
    if lookback_days is not None:
        d["lookbackDays"] = lookback_days
    return Indicator(d)

__all__.append("OptionRealizedPremium")

def OptionSpreadCount(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
) -> Indicator:
    """Returns the number of distinct economic open multi-leg option spreads matching the filter criteria, deduplicating equal economic spread keys. Does not count standalone single-leg option positions. Range: 0 to ∞.
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    """
    d: Dict[str, Any] = {"type": "OptionSpreadCount"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    return Indicator(d)

__all__.append("OptionSpreadCount")

def OptionSpreadEntryPrice(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
) -> Indicator:
    """Returns the net price per share paid or collected to open the option spreads matching the filter: positive for a net debit, negative for a net credit. Several matching spreads average by contracts. Has no value when no matching spread is open. Use it as an option limit amount to price one spread off another spread's fill; wrap a credit spread's price in Absolute Value first, because a limit must be positive. Range: -∞ to ∞ (dollars per share).
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    """
    d: Dict[str, Any] = {"type": "OptionSpreadEntryPrice"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    return Indicator(d)

__all__.append("OptionSpreadEntryPrice")

def OptionUnrealizedPnL(
    underlying: str,
    option_type: Literal["call", "put"],
    direction: Literal["long", "short"],
    spread_type: Literal["vertical", "calendar", "diagonal", "ironCondor", "straddle", "strangle", "custom"],
) -> Indicator:
    """Returns the total unrealized P&L in dollars across option spreads matching the filter. Range: -∞ to ∞ (dollars).
    underlying: Filter by underlying (e.g., AAPL, SPY). Leave empty for all.
    option_type: Filter by Call or Put. Leave empty for all.
    direction: Filter by Long or Short. Leave empty for all.
    spread_type: Filter by spread type. Leave empty for all.
    """
    d: Dict[str, Any] = {"type": "OptionUnrealizedPnL"}
    d["underlying"] = underlying
    d["optionType"] = _enum(option_type, ["call","put"], "option_type")
    d["direction"] = _enum(direction, ["long","short"], "direction")
    d["spreadType"] = _enum(spread_type, ["vertical","calendar","diagonal","ironCondor","straddle","strangle","custom"], "spread_type")
    return Indicator(d)

__all__.append("OptionUnrealizedPnL")

def ParabolicSar(
    asset: Union[str, Dict[str, Any], _Candidate],
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    step: float = 0.02,
    maximum: float = 0.2,
) -> Indicator:
    """Trailing stop-and-reverse level that accelerates toward price as the trend extends. Price crossing the SAR flips the trend. Range: Price scale.
    asset: Ticker name (ex. SPY, BTC)
    interval: Bar interval the SAR trails on
    step: AF increment per new extreme
    maximum: AF cap
    """
    d: Dict[str, Any] = {"type": "ParabolicSar"}
    _set_asset(d, "targetAsset", asset)
    d["interval"] = _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")
    d["step"] = step
    d["maximum"] = maximum
    return Indicator(d)

__all__.append("ParabolicSar")

def Plus(
    left: Indicator,
    right: Indicator,
) -> Indicator:
    """Sum of two or more indicators. The SDK builder takes exactly two operands; nest it to add more.
    """
    d: Dict[str, Any] = {"type": "Plus"}
    d["indicators"] = [left.d, right.d]
    return Indicator(d)

__all__.append("Plus")

def PoliticalPurchaseShare(
    member_id: str,
    instrument: Literal["Equity", "Option"] = "Equity",
    amount_basis: Literal["LowerBound", "Midpoint", "UpperBound"] = "Midpoint",
    purchase_scope: Literal["AllPurchases", "RemainingLots"] = "AllPurchases",
) -> Indicator:
    """A member's publicly known equity or option purchase dollars divided by the sum of both, across all disclosed tickers. Event versions enter only when public. Unclassified instruments are excluded. purchaseScope defaults to AllPurchases for historical flow. RemainingLots sums surviving original purchase costs after reported full sales and exercises, excluding unresolved ticker buckets. Unknown partial quantities retain their estimate. Neither setting is current market value or verified holdings. Range: 0 to 100 percent; zero before a qualifying purchase.
    member_id: Bioguide member id, for example P000197. The share uses the member's whole public purchase record.
    instrument: Which side of the equity/option purchase mix to return, as a percent from 0 to 100.
    amount_basis: Estimate for disclosed purchase ranges. Midpoint is the default.
    purchase_scope: AllPurchases preserves historical purchase flow. RemainingLots uses surviving purchase costs across identifiable tickers; unknown partial quantities retain their original estimate. Neither is current market value.
    """
    d: Dict[str, Any] = {"type": "PoliticalPurchaseShare"}
    d["memberId"] = member_id
    d["instrument"] = _enum(instrument, ["Equity","Option"], "instrument")
    d["amountBasis"] = _enum(amount_basis, ["LowerBound","Midpoint","UpperBound"], "amount_basis")
    d["purchaseScope"] = _enum(purchase_scope, ["AllPurchases","RemainingLots"], "purchase_scope")
    return Indicator(d)

__all__.append("PoliticalPurchaseShare")

def PoliticalTrades(
    asset: Union[str, Dict[str, Any], _Candidate],
    filer: str,
    metric: Literal["NetAmount", "BuyAmount", "RemainingBuyAmount", "SellAmount", "BuyCount", "SellCount", "DistinctBuyers", "Held"] = "BuyAmount",
    window_days: float = 90,
    amount_basis: Literal["LowerBound", "Midpoint", "UpperBound"] = "LowerBound",
    instrument: Literal["Equity", "Option", "All"] = "Equity",
    chamber: Literal["All", "House", "Senate"] = "All",
    member_id: str = "",
) -> Indicator:
    """Trades by members of Congress in the asset, from the unified House and Senate disclosure lake, over a trailing window. A trade counts from the moment its report became public, never its trade date, and an amended report changes a value only from the moment the amendment became public. Amount metrics can use the lower, midpoint, or upper end of every disclosed range. Equity disclosures are the default; confirmed option disclosures can be selected separately for numeric activity. Held requires Equity and reads the member's whole public record instead: 1 while their latest equity disclosure for the asset is a purchase or partial sale, 0 after a full sale. Held is a disclosure signal, not verified holdings or share quantity. RemainingBuyAmount reads the full public history and sums original purchase ranges of remaining lots after full sales and exercises. Unknown partial quantities retain their purchase amount estimate; sale proceeds are never subtracted. This is estimated remaining cost, not current market value. Printed expirations identify lots but do not implicitly end modeled renewal exposure. Range: Disclosed amount-range values or counts over the window, or 1/0 for Held; 0 when no matching trade was disclosed.
    asset: Pass CANDIDATE inside a rebalance pipeline to bind each stock.
    filer: Member full or last name. Pass an empty string for all members.
    metric: Amount-range aggregate, event count, distinct purchasing members, RemainingBuyAmount (surviving original purchase amounts; ignores window; unknown partial quantities retained), or Held: 1 while the member still holds the asset (latest public disclosure is a purchase or partial sale), ignoring the window.
    window_days: Trailing calendar days measured from when each event became public.
    amount_basis: Range endpoint used by amount metrics; LowerBound is conservative.
    instrument: Equity excludes confirmed option disclosures; Option selects them explicitly.
    chamber: Optional advanced cohort filter; named-member requests should normally use All.
    member_id: Bioguide id such as P000197. Matches exactly and overrides filer, because names collide.
    """
    d: Dict[str, Any] = {"type": "PoliticalTrades"}
    _set_asset(d, "targetAsset", asset)
    d["filer"] = filer
    d["metric"] = _enum(metric, ["NetAmount","BuyAmount","RemainingBuyAmount","SellAmount","BuyCount","SellCount","DistinctBuyers","Held"], "metric")
    d["windowDays"] = window_days
    d["amountBasis"] = _enum(amount_basis, ["LowerBound","Midpoint","UpperBound"], "amount_basis")
    d["instrument"] = _enum(instrument, ["Equity","Option","All"], "instrument")
    d["chamber"] = _enum(chamber, ["All","House","Senate"], "chamber")
    d["memberId"] = member_id
    return Indicator(d)

__all__.append("PoliticalTrades")

def PortfolioValue() -> Indicator:
    """The total current value of your portfolio (cash + all positions). Range: >= 0.
    """
    d: Dict[str, Any] = {"type": "PortfolioValue"}
    return Indicator(d)

__all__.append("PortfolioValue")

def PositionEntryPrice(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Original filled entry price for the current holding. Added shares preserve this original anchor; closing the holding resets it.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PositionEntryPrice"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("PositionEntryPrice")

def PositionMaxDrawdown(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The maximum percentage decline in the position's unrealized P&L from its peak. Aggregates the stock or crypto holding plus all option spreads on that underlying. Returns 0 at a new high water mark; rises as P&L falls below the peak, so it is never negative. Range: >= 0 (percentage points of P&L).
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PositionMaxDrawdown"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("PositionMaxDrawdown")

def PositionMaxDrawup(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The percentage increase of the asset's current market price from the lowest price observed by this indicator. Share purchases and sales do not change the calculation; it does not use position cost basis. Range: >= 0 (percentage).
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PositionMaxDrawup"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("PositionMaxDrawup")

def PositionMinutesSinceHigh(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Exchange trading minutes since the current holding's last new high. Closed-market minutes do not count. Added shares preserve the holding's observation path.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PositionMinutesSinceHigh"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("PositionMinutesSinceHigh")

def PositionPeakPrice(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """Highest observed price since the current holding's original entry. Added shares preserve the peak path; closing resets it.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PositionPeakPrice"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("PositionPeakPrice")

def PositionPercentChange(
    assets: Union[str, Sequence[Any]],
) -> Indicator:
    """How much your position has gained or lost as a percentage since you opened it. Range: Unbounded (percentage).
    assets: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PositionPercentChange"}
    _set_assets(d, "targetAssets", assets)
    return Indicator(d)

__all__.append("PositionPercentChange")

def PositionValue(
    assets: Union[str, Sequence[Any]],
) -> Indicator:
    """The current dollar value of your position in one or more assets. Range: >= 0.
    assets: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PositionValue"}
    _set_assets(d, "targetAssets", assets)
    return Indicator(d)

__all__.append("PositionValue")

def PreviousClosingPrice(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The closing price from the previous trading session. It already has a one-session offset; wrapping it in Indicator Window Ago adds another lag rather than selecting that same previous close. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PreviousClosingPrice"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("PreviousClosingPrice")

def Price(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The current market price of any asset. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "Price"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("Price")

def PriceChangeSinceOpen(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The dollar change in price since market open. Range: Unbounded.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "PriceChangeSinceOpen"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("PriceChangeSinceOpen")

def PriceMeanAbsoluteDeviation(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """The mean absolute deviation of an asset's price over a window. Similar to standard deviation but less sensitive to outliers. Range: >= 0.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "PriceMeanAbsoluteDeviation"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("PriceMeanAbsoluteDeviation")

def PriceRateOfChange(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Measures the percentage change in price between the current price and the price N periods ago. Range: Unbounded (percentage).
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "PriceRateOfChange"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("PriceRateOfChange")

def PriceStandardDeviation(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """The standard deviation of an asset's closing price over a window. Measures price volatility. Range: >= 0.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "PriceStandardDeviation"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

PriceStdDev = PriceStandardDeviation
__all__.append("PriceStandardDeviation")
__all__.append("PriceStdDev")

def RebalanceDecisionMetric(
    metric: Literal["allocationDrift", "plannedTurnover", "estimatedCost", "expectedBenefit", "netBenefit"] = "netBenefit",
) -> Indicator:
    """Exposes the allocation plan that a DynamicRebalance or RebalanceOption action is about to execute, so the strategy condition can decide whether that plan is worthwhile. Range: Drift and turnover are portfolio fractions; cost and benefit metrics are dollar amounts.
    metric: Reads the prospective plan calculated by DynamicRebalance or RebalanceOption before the strategy condition runs.
    """
    d: Dict[str, Any] = {"type": "RebalanceDecisionMetric"}
    d["metric"] = _enum(metric, ["allocationDrift","plannedTurnover","estimatedCost","expectedBenefit","netBenefit"], "metric")
    return Indicator(d)

__all__.append("RebalanceDecisionMetric")

def RelativeStrengthIndex(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Measures the speed and magnitude of recent price changes. Oscillates between 0 and 100. Range: 0 to 100.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "RelativeStrengthIndex"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

RSI = RelativeStrengthIndex
__all__.append("RelativeStrengthIndex")
__all__.append("RSI")

def RelativeVolume(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 14,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Current bar volume divided by the average volume of the trailing N completed bars. 1.0 means the bar is trading exactly its recent pace. Range: >= 0 (ratio around 1.0).
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "RelativeVolume"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("RelativeVolume")

def SimpleMovingAverage(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 30,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """SMA/EMA window interval Day means elapsed calendar time, including closed exchange dates. TradingDay uses the exchange-session clock for exchange-traded assets and skips closed dates while preserving within-session time. For a conventional N-day stock moving average, author interval TradingDay unless the request explicitly specifies elapsed calendar days. An outer TradingDay lag does not change an inner Day average into an N-session average. Preserve an explicit calendar-day request as Day. Continuous-market daily windows use 24-hour Day intervals. A TradingDay window can still have a forming current reading: choose completed-session references separately when the signal requires confirmed closes. ChartBar periods and offsets already identify completed candles; preserve that clock instead of substituting a time-window average.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "SimpleMovingAverage"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

SMA = SimpleMovingAverage
__all__.append("SimpleMovingAverage")
__all__.append("SMA")

def SquareRoot(
    operand: Indicator,
) -> Indicator:
    """Square root of an indicator.
    """
    d: Dict[str, Any] = {"type": "SquareRoot"}
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("SquareRoot")

def Stochastic(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 14,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    smooth_k: float = 3,
    smooth_d: float = 3,
    line: Literal["k", "d"] = "k",
) -> Indicator:
    """Momentum oscillator comparing the close to the high-low range over N bars, with %K smoothing and a %D signal average. Readings above 80 are overbought, below 20 oversold. Range: 0 to 100.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    smooth_k: Moving-average length applied to raw %K
    smooth_d: Moving-average length applied to smoothed %K
    line: Which stochastic line to evaluate
    """
    d: Dict[str, Any] = {"type": "Stochastic"}
    _set_asset(d, "targetAsset", asset)
    d["smoothK"] = smooth_k
    d["smoothD"] = smooth_d
    d["line"] = _enum(line, ["k","d"], "line")
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("Stochastic")

def StockReport(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """AI stock report rating: a fundamental-strength score from 1 to 5, higher is stronger, synthesizing revenue growth, profitability, balance sheet health, valuation and competitive positioning. Point-in-time: a backtest reads the rating in effect on each date. Works on a named ticker and on CANDIDATE inside a rebalance filter, rank or weight, e.g. gte(StockReport(CANDIDATE), Value(3.5)) for fundamentally strong stocks.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "StockReport"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("StockReport")

def SumOrderAmount(
    side: Literal["Buy", "Sell"],
    order_status: Literal["Pending", "Accepted", "Pending User Approval", "Canceled", "Filled", "Partially Filled"] = "Filled",
    length: float = 1,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    assets: Optional[Union[str, Sequence[Any]]] = None,
) -> Indicator:
    """The total dollar amount across all matching orders within a window. Range: >= 0.
    side: Matches order events with this side (buy or sell)
    order_status: Matches order events with this status
    length: Length of time
    interval: Unit of time
    assets: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "SumOrderAmount"}
    d["side"] = _enum(side, ["Buy","Sell"], "side")
    d["orderStatus"] = _enum(order_status, ["Pending","Accepted","Pending User Approval","Canceled","Filled","Partially Filled"], "order_status")
    _set_assets(d, "targetAssets", assets)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("SumOrderAmount")

def SumOrderQuantity(
    side: Literal["Buy", "Sell"],
    order_status: Literal["Pending", "Accepted", "Pending User Approval", "Canceled", "Filled", "Partially Filled"] = "Filled",
    length: float = 1,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    assets: Optional[Union[str, Sequence[Any]]] = None,
) -> Indicator:
    """The total number of shares across all matching orders within a window. Range: >= 0.
    side: Matches order events with this side (buy or sell)
    order_status: Matches order events with this status
    length: Length of time
    interval: Unit of time
    assets: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "SumOrderQuantity"}
    d["side"] = _enum(side, ["Buy","Sell"], "side")
    d["orderStatus"] = _enum(order_status, ["Pending","Accepted","Pending User Approval","Canceled","Filled","Partially Filled"], "order_status")
    _set_assets(d, "targetAssets", assets)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("SumOrderQuantity")

def Supertrend(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 10,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
    multiplier: float = 3,
    line: Literal["value", "direction"] = "value",
) -> Indicator:
    """ATR trailing band that ratchets with the trend and flips sides on a close through it. The direction line reads +1 in an uptrend, -1 in a downtrend. Range: Price scale (value); +1 / -1 (direction).
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    multiplier: How many ATRs from HL2
    line: Band value or trend direction (+1 up, -1 down)
    """
    d: Dict[str, Any] = {"type": "Supertrend"}
    _set_asset(d, "targetAsset", asset)
    d["multiplier"] = multiplier
    d["line"] = _enum(line, ["value","direction"], "line")
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("Supertrend")

def TrailingSum(
    operand: Indicator,
    length: float = 4,
) -> Indicator:
    """The sum of the last N accepted observations of the inner indicator. Generic inputs append when the value changes; source-backed inputs use their own observation identities. Length counts retained observations rather than every evaluation tick.
    length: Length of the rolling window
    """
    d: Dict[str, Any] = {"type": "TrailingSum"}
    d["length"] = length
    d["indicators"] = [operand.d]
    return Indicator(d)

__all__.append("TrailingSum")

def TrueRange(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The greatest of: current high minus low, |high − previous close|, or |low − previous close|. Measures single-period volatility. Range: >= 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "TrueRange"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("TrueRange")

def UnderlyingMaxDrawdown(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The current percentage decline of the asset's market price from the highest price observed by this indicator. Tracks the underlying price independently of position state. Range: 0 to 100 (percentage).
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "UnderlyingMaxDrawdown"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("UnderlyingMaxDrawdown")

def Value(
    value: float = 0,
) -> Indicator:
    """A fixed numeric value. Useful as one side of a comparison.
    value: Constant value
    """
    d: Dict[str, Any] = {"type": "Value"}
    d["value"] = value
    return Indicator(d)

__all__.append("Value")

def Volume(
    asset: Union[str, Dict[str, Any], _Candidate],
    metric: Literal["totalRevenue", "grossProfit", "operatingIncome", "ebitda", "epsActual", "epsEstimate", "netIncome", "freeCashFlow", "totalAssets", "totalLiab", "shortTermDebt", "longTermDebt", "commonStockSharesOutstanding", "dividendYield", "openingPrice", "highestPrice", "lowestPrice", "closingPrice", "volume", "marketCap", "peRatioTTM", "psRatioTTM", "pbRatioTTM", "enterpriseValue"] = "volume",
) -> Indicator:
    """Trading volume for the asset. It is the Fundamental indicator with its metric preset to Volume, on the same pipeline and data sources. Keep the metric at Volume: any other metric returns that fundamental metric instead of volume. Range: >= 0.
    asset: Ticker name (ex. SPY, BTC)
    metric: The fundamental metric to use
    """
    d: Dict[str, Any] = {"type": "Volume"}
    _set_asset(d, "targetAsset", asset)
    d["metric"] = _enum(metric, ["totalRevenue","grossProfit","operatingIncome","ebitda","epsActual","epsEstimate","netIncome","freeCashFlow","totalAssets","totalLiab","shortTermDebt","longTermDebt","commonStockSharesOutstanding","dividendYield","openingPrice","highestPrice","lowestPrice","closingPrice","volume","marketCap","peRatioTTM","psRatioTTM","pbRatioTTM","enterpriseValue"], "metric")
    return Indicator(d)

__all__.append("Volume")

def VWAP(
    asset: Union[str, Dict[str, Any], _Candidate],
) -> Indicator:
    """The average price weighted by volume throughout the trading session. A key intraday benchmark. Range: > 0.
    asset: Ticker name (ex. SPY, BTC)
    """
    d: Dict[str, Any] = {"type": "VWAP"}
    _set_asset(d, "targetAsset", asset)
    return Indicator(d)

__all__.append("VWAP")

def WilliamsR(
    asset: Union[str, Dict[str, Any], _Candidate],
    length: float = 14,
    interval: Literal["Day", "Hour", "Minute", "TradingDay", "TradingMinute"] = "Day",
) -> Indicator:
    """Momentum oscillator placing the close within the high-low range over N bars, on a -100 to 0 scale. Above -20 is overbought, below -80 oversold. Range: -100 to 0.
    asset: Ticker name (ex. SPY, BTC)
    length: Length of time
    interval: Unit of time
    """
    d: Dict[str, Any] = {"type": "WilliamsR"}
    _set_asset(d, "targetAsset", asset)
    d["window"] = {"length": length, "interval": _enum(interval, ["Day","Hour","Minute","TradingDay","TradingMinute"], "interval")}
    return Indicator(d)

__all__.append("WilliamsR")

def Year() -> Indicator:
    """The current year.
    """
    d: Dict[str, Any] = {"type": "Year"}
    return Indicator(d)

__all__.append("Year")


# ---- generated job builders ----
Gene = Dict[str, Any]
FeeConfig = Dict[str, Any]
SelectionPolicy = Dict[str, Any]
SweepConfig = Dict[str, Any]
AdaptiveConfig = Dict[str, Any]

def backtest(
    portfolio: Dict[str, Any],
    *,
    start_date: str,
    end_date: str,
    baseline_symbol: Optional[str] = None,
    baseline_asset: Optional[Dict[str, Any]] = None,
    interval: Optional[Literal["Day", "Minute"]] = None,
    initial_value: Optional[float] = None,
    generate_events: Optional[bool] = None,
    preview_only: Optional[bool] = None,
    force_new_run: Optional[bool] = None,
    fee_config: Optional[FeeConfig] = None,
    dividend_policy: Optional[Literal["cash", "reinvest"]] = None,
) -> Dict[str, Any]:
    """Submit a backtest. Returns a handle; results are read after it completes.
    start_date: Backtest start date (ISO format, e.g. 2024-01-01). For interval=Minute, default first-look is the last 90 inclusive calendar days and the selected range plus minute-indicator warmup cannot exceed 730 days of minute data. Do not use 2010-01-01 on Minute.
    end_date: Backtest end date (ISO format, e.g. 2024-12-31). Minute allows up to 730 days of minute data from start_date, counting minute-indicator warmup; never split a longer span into yearly Minute jobs.
    baseline_symbol: Deprecated Stock benchmark shorthand for comparisonValue. Use baseline_asset with an explicit Stock or Cryptocurrency type; never provide both fields. When both fields are omitted, the single traded stock/option underlying is inferred, otherwise SPY. For single-name options use that underlying; compare multi-name books against a deliberately chosen benchmark.
    baseline_asset: Typed buy-and-hold benchmark for comparisonValue. Use {type:'Cryptocurrency',symbol:'BTC-USD',name:'BTC-USD'} for BTC or {type:'Stock',symbol:'SPY',name:'SPY'} for a stock/ETF. Symbol text never determines asset type. Omit baseline_symbol when using this field.
    interval: Time interval: Day or Minute (default Day). Day uses fixed OHLC decision/fill snapshots and overrides the portfolio deployment frequency, openDelayMinutes, closeLeadMinutes and intervalSeconds. Use Minute when the authored intraday schedule matters. Minute is a daytrade tape. 730 days of minute data (selected range plus warmup) is the HARD CAP; 90 days is only the DEFAULT when you name no dates. Name the dates you actually want — certifying a candidate on a full year or two is a normal second backtest, not an escalation.
    initial_value: Starting portfolio value (default 10000) Range 1..inf.
    generate_events: Generate detailed event data (signals, orders, audits) during backtest. Costs 5x research tokens for minute-level backtests. Events are retained for 3 days. Use query_backtest_events to explore them while retained.
    preview_only: Quote the estimated research-token cost of a fresh run without launching. Small daily stock backtests are free. Larger runs are priced by strategy, assets scanned, and calendar days including warm-up.
    force_new_run: Create and execute a fresh backtest instead of reusing a prior identical result. Defaults to false. Requires portfolio_id. Use for an explicitly requested rerun or warm-cache timing; this reruns the simulation and incurs the normal backtest cost.
    fee_config: Optional fee contract keyed by AssetTypeEnum (Stock/Cryptocurrency/Option) with {amount, type: percent|dollars}. Omit for shared defaults (Option $0.65/contract). For replay of an optimizer/walk-forward study, pass that study's persisted feeConfig. OptionSlippageFraction is supported: 0 fills at quote mid, 1 at ask for buys/bid for sells; omitted uses the engine default 0.5. Fills require a current executable quote; this does not enable last-trade fallback pricing.
    dividend_policy: Overrides the portfolio's dividend policy for this run only. Omit to use the portfolio's own setting (cash unless it was set to reinvest). "reinvest" buys more of the paying stock on each pay date while that stock is still held. A fully exited position receives its locked dividend entitlement as cash without reopening the position. Reinvest is what a buy-and-hold comparison against the SPY total-return baseline needs.
    """
    args = {
        "start_date": start_date,
        "end_date": end_date,
        "baseline_symbol": baseline_symbol,
        "baseline_asset": baseline_asset,
        "interval": interval,
        "initial_value": initial_value,
        "generate_events": generate_events,
        "preview_only": preview_only,
        "force_new_run": force_new_run,
        "fee_config": fee_config,
        "dividend_policy": dividend_policy,
    }
    return {
        "tool": "backtest_portfolio",
        "portfolio": portfolio,
        "args": {k: v for k, v in args.items() if v is not None},
    }

__all__.append("backtest")

def walk_forward(
    portfolio: Dict[str, Any],
    *,
    fold_count: float,
    global_start_date: Optional[str] = None,
    global_end_date: Optional[str] = None,
    engine_kind: Optional[Literal["ga", "sweep"]] = None,
    walk_forward_mode: Optional[Literal["anchored", "rolling"]] = None,
    oos_width_days: Optional[float] = None,
    validation_percent: Optional[float] = None,
    embargo_days: Optional[float] = None,
    population_size: Optional[float] = None,
    num_generations: Optional[float] = None,
    num_windows: Optional[float] = None,
    training_percent: Optional[float] = None,
    window_overlap_percent: Optional[float] = None,
    fitness_functions: Optional[Sequence[Literal["sharpeRatio", "sortinoRatio", "winRate", "profitFactor", "calmarRatio", "maxDrawdown", "avgDrawdown", "percentChange", "dollarsSold", "ulcerPerformanceIndex", "participationRate", "distinctUnderlyingsTraded", "medianDeployment"]]] = None,
    selection_policy: Optional[SelectionPolicy] = None,
    fold_selection_policy: Optional[SelectionPolicy] = None,
    fee_config: Optional[FeeConfig] = None,
    degradation_penalty: Optional[float] = None,
    leaderboard_size: Optional[float] = None,
    search_mode: Optional[Literal["auto", "exhaustive", "generational"]] = None,
    preview_only: Optional[bool] = None,
    interval: Optional[Literal["Day", "Minute"]] = None,
    auto_run: Optional[bool] = None,
    name: Optional[str] = None,
    mode: Optional[Literal["validation", "adaptive", "both"]] = None,
    oos_stride_days: Optional[float] = None,
    adaptive_config: Optional[AdaptiveConfig] = None,
    inner_mode: Optional[Literal["optimize", "backtest_only"]] = None,
    certification: Optional[bool] = None,
    allow_ga_certification: Optional[bool] = None,
    use_certification_selection_policy: Optional[bool] = None,
    genes: Optional[Sequence[Gene]] = None,
) -> Dict[str, Any]:
    """Submit a walk-forward study (anchored or rolling folds, GA or sweep engine).
    fold_count: Number of forward folds (2–8, required). Range 2..8.
    global_start_date: Global calendar start for the walk-forward study (ISO date). Optional: defaults to six years before global_end_date, or for a Minute study to the longest window ending there that fits the cap. Each Minute evaluation window cannot exceed 730 days of minute data counting minute-indicator warmup, which is the only cap.
    global_end_date: Global calendar end for the walk-forward study (ISO date). Optional: defaults to the last complete US equity trading session.
    engine_kind: Child optimizer engine: ga (default) or sweep. Prefer sweep for deploy certification and options rotation books; use ga for exploratory continuous mutation.
    walk_forward_mode: Anchored (default) or rolling outer window mode.
    oos_width_days: OOS window width in calendar days (default 252). Range 1..inf.
    validation_percent: Validation share inside each fold's development window (1–50). Default 20 for exploratory runs. Default 50 when certification: true (sweep or GA) — longer validation reduces fold-winner selection noise. Range 1..50.
    embargo_days: Embargo gap between training and validation (default 14). Range 0..inf.
    population_size: GA population size (default 6). Range 3..12.
    num_generations: GA generations (default 6). Range 1..10.
    num_windows: Inner rolling training windows (default 3). Range 1..20.
    training_percent: Inner train/validation split percent (default 75). Range 10..95.
    window_overlap_percent: Inner rolling window overlap percent (default 50). Range 0..95.
    selection_policy: Winner selection policy (objectives, constraints, tieBreakers). Applies to sweep leaderboard and fold winner selection. For GA WF, also sets NSGA-II activity constraints when fold_selection_policy is omitted.
    fold_selection_policy: Explicit fold winner selection policy. Overrides selection_policy for fold winner pick. When set, train→validation degradation penalty defaults to 0.5 unless degradation_penalty is set.
    fee_config: Optional fee contract keyed by AssetTypeEnum (Stock/Cryptocurrency/Option) with {amount, type: percent|dollars}. Frozen on the study and applied to all fold scoring / OOS. Omit for shared defaults matching standalone backtests (Option $0.65/contract). Unknown keys are rejected.
    degradation_penalty: Train→validation degradation weight for fold winner ranking (default 0.5 when an explicit policy is set; 0.0 for legacy GA Sharpe-only studies). Range 0..1.
    leaderboard_size: Sweep leaderboard size when using gene_intents (default 25). Range 1..250.
    search_mode: Sweep search mode: auto (default), exhaustive, or generational.
    preview_only: When true, compile and validate the walk-forward plan without creating a study or charging tokens. Returns fold calendar, estimated token cost, and compiled sweepGenes (when engine_kind is sweep).
    auto_run: Start orchestrator immediately (default true).
    name: Optional study label.
    mode: Study scoring mode: validation (per-fold independent OOS, default), adaptive (stitched re-optimized policy curve), or both.
    oos_stride_days: Validation-only OOS stride in days; when less than oos_width_days folds overlap. Ignored in adaptive/both (contiguous tiling). Range 1..inf.
    adaptive_config: Adaptive/both only: { reoptimizeWindow: anchored|rolling, hysteresisMarginPct, maxTurnoverPerRollPct? }.
    inner_mode: Per-fold inner execution: optimize (default, run GA/sweep per fold) or backtest_only (certify fixed portfolio — train/val/OOS backtests only, no inner optimizer).
    certification: When true with inner_mode optimize, apply default certification selection policy (activity floors + percentChange≥0 + sortino≥0.5) unless explicit selection_policy is set. Also defaults validation_percent to 50. Prefer engine_kind sweep for deploy certification; GA+certification is rejected unless allow_ga_certification is true.
    allow_ga_certification: Explicit override to allow engine_kind ga with certification: true. Default false — use sweep+certification or inner_mode backtest_only for deployable studies.
    use_certification_selection_policy: Alias for certification — applies DEFAULT_CERTIFICATION_SELECTION_POLICY on optimize-mode studies.
    genes: Typed sweep genes built with the gene* builders (deterministic; no LLM compile).
    """
    args = {
        "fold_count": fold_count,
        "global_start_date": global_start_date,
        "global_end_date": global_end_date,
        "engine_kind": engine_kind,
        "walk_forward_mode": walk_forward_mode,
        "oos_width_days": oos_width_days,
        "validation_percent": validation_percent,
        "embargo_days": embargo_days,
        "population_size": population_size,
        "num_generations": num_generations,
        "num_windows": num_windows,
        "training_percent": training_percent,
        "window_overlap_percent": window_overlap_percent,
        "fitness_functions": fitness_functions,
        "selection_policy": selection_policy,
        "fold_selection_policy": fold_selection_policy,
        "fee_config": fee_config,
        "degradation_penalty": degradation_penalty,
        "leaderboard_size": leaderboard_size,
        "search_mode": search_mode,
        "preview_only": preview_only,
        "interval": interval,
        "auto_run": auto_run,
        "name": name,
        "mode": mode,
        "oos_stride_days": oos_stride_days,
        "adaptive_config": adaptive_config,
        "inner_mode": inner_mode,
        "certification": certification,
        "allow_ga_certification": allow_ga_certification,
        "use_certification_selection_policy": use_certification_selection_policy,
        "genes": genes,
    }
    return {
        "tool": "run_walk_forward_study",
        "portfolio": portfolio,
        "args": {k: v for k, v in args.items() if v is not None},
    }

__all__.append("walk_forward")

def optimization(
    portfolio: Dict[str, Any],
    *,
    start_date: str,
    end_date: str,
    fitness_functions: Optional[Sequence[Literal["sharpeRatio", "sortinoRatio", "winRate", "profitFactor", "calmarRatio", "maxDrawdown", "avgDrawdown", "percentChange", "dollarsSold", "ulcerPerformanceIndex", "participationRate", "distinctUnderlyingsTraded", "medianDeployment"]]] = None,
    population_size: Optional[float] = None,
    num_generations: Optional[float] = None,
    num_windows: Optional[float] = None,
    training_percent: Optional[float] = None,
    window_overlap_percent: Optional[float] = None,
    interval: Optional[Literal["Day", "Minute"]] = None,
    selection_policy: Optional[SelectionPolicy] = None,
    fee_config: Optional[FeeConfig] = None,
    genes: Optional[Sequence[Gene]] = None,
) -> Dict[str, Any]:
    """Submit a genetic optimization over the portfolio's tunable genes.
    start_date: Optimization start date (ISO format, e.g. 2024-01-01). Minute has ONE cap: 730 days of minute data, counting minute-indicator warmup. The 90-day first look is a default for callers who name no dates, not a ceiling — pass the window the question needs.
    end_date: Optimization end date (ISO format, e.g. 2024-12-31). Minute selected range plus minute-indicator warmup cannot exceed 730 days of minute data.
    fitness_functions: Fitness functions: sharpeRatio, sortinoRatio, winRate, profitFactor, calmarRatio, maxDrawdown, avgDrawdown, percentChange, dollarsSold, ulcerPerformanceIndex, participationRate, distinctUnderlyingsTraded, medianDeployment
    population_size: Population size for genetic algorithm (default 6, range 3-12) Range 3..12.
    num_generations: Number of generations (default 6, max 10) Range 1..10.
    num_windows: Number of rolling training windows (default 3, max 20) Range 1..20.
    training_percent: Whole-percent share of the requested date range used for training; the remaining tail is validation (default 75). Uses app-style percent units, e.g. 75 not 0.75. Range 10..95.
    window_overlap_percent: Whole-percent overlap between adjacent rolling training windows (default 50). Uses app-style percent units, e.g. 50 not 0.5. Range 0..95.
    interval: Time interval: Day or Minute (default Day)
    selection_policy: Optional winner-selection policy with activity/quality/posture floors (participationRate, distinctUnderlyingsTraded, medianDeployment, percentChange, sortinoRatio, etc.). Steers GA away from thin or over-deployed validation winners during evolution and leaderboard ranking. Never hard-fails the run.
    fee_config: Optional fee contract keyed by AssetTypeEnum (Stock/Cryptocurrency/Option) with {amount, type: percent|dollars}. Omit for shared defaults matching standalone backtests (Option $0.65/contract). Unknown keys are rejected.
    genes: Typed sweep genes built with the gene* builders (deterministic; no LLM compile).
    """
    args = {
        "start_date": start_date,
        "end_date": end_date,
        "fitness_functions": fitness_functions,
        "population_size": population_size,
        "num_generations": num_generations,
        "num_windows": num_windows,
        "training_percent": training_percent,
        "window_overlap_percent": window_overlap_percent,
        "interval": interval,
        "selection_policy": selection_policy,
        "fee_config": fee_config,
        "genes": genes,
    }
    return {
        "tool": "optimize_portfolio",
        "portfolio": portfolio,
        "args": {k: v for k, v in args.items() if v is not None},
    }

__all__.append("optimization")


# ---- generated sweep-gene builders ----
def _gene_wire_value(value: Any) -> Any:
    """Recursively unwrap SDK Indicator/Condition values for JSON host calls."""
    if isinstance(value, (Indicator, Condition)):
        return _gene_wire_value(value.to_dict())
    if isinstance(value, dict):
        return {key: _gene_wire_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_gene_wire_value(item) for item in value]
    return value

def gene_subject(
    *,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep Subject (Portfolio scope) over a value set."""
    return {
        "field": "Subject",
        "scope": "Portfolio",
        "target": {"scope": "Portfolio", "field": "Subject"},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_subject")

def gene_entry_condition(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep EntryCondition (Strategy scope) over a value set."""
    return {
        "field": "EntryCondition",
        "scope": "Strategy",
        "target": {"scope": "Strategy", "field": "EntryCondition", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_entry_condition")

def gene_replace_entry_condition(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep EntryCondition (Strategy scope), replacing the seed's gate rather than adding to it."""
    return {
        "field": "EntryCondition",
        "scope": "Strategy",
        "target": {"scope": "Strategy", "field": "EntryCondition", "strategyIndex": strategy_index, "replace": True},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_replace_entry_condition")

def gene_exit_condition(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep ExitCondition (Strategy scope) over a value set."""
    return {
        "field": "ExitCondition",
        "scope": "Strategy",
        "target": {"scope": "Strategy", "field": "ExitCondition", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_exit_condition")

def gene_replace_exit_condition(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep ExitCondition (Strategy scope), replacing the seed's gate rather than adding to it."""
    return {
        "field": "ExitCondition",
        "scope": "Strategy",
        "target": {"scope": "Strategy", "field": "ExitCondition", "strategyIndex": strategy_index, "replace": True},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_replace_exit_condition")

def gene_entry_cooldown_days(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep EntryCooldownDays (Strategy scope) over a value set."""
    return {
        "field": "EntryCooldownDays",
        "scope": "Strategy",
        "target": {"scope": "Strategy", "field": "EntryCooldownDays", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_entry_cooldown_days")

def gene_condition_window_length(
    *,
    strategy_index: int,
    path: Sequence[int],
    slot: str,
    values: Sequence[Any],
    indicator_path: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Sweep ConditionWindowLength (Strategy scope) over a value set."""
    target: Dict[str, Any] = {"scope": "Strategy", "field": "ConditionWindowLength", "strategyIndex": strategy_index, "path": path, "slot": slot}
    if indicator_path is not None:
        target["indicatorPath"] = indicator_path
    return {
        "field": "ConditionWindowLength",
        "scope": "Strategy",
        "target": target,
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_condition_window_length")

def gene_condition_threshold(
    *,
    strategy_index: int,
    path: Sequence[int],
    slot: str,
    values: Sequence[Any],
    indicator_path: Optional[Sequence[int]] = None,
    parameter: Optional[str] = None,
) -> Dict[str, Any]:
    """Sweep ConditionThreshold (Strategy scope) over a value set."""
    target: Dict[str, Any] = {"scope": "Strategy", "field": "ConditionThreshold", "strategyIndex": strategy_index, "path": path, "slot": slot}
    if indicator_path is not None:
        target["indicatorPath"] = indicator_path
    if parameter is not None:
        target["parameter"] = parameter
    return {
        "field": "ConditionThreshold",
        "scope": "Strategy",
        "target": target,
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_condition_threshold")

def gene_rank_signal(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep RankSignal (Action scope) over a value set."""
    return {
        "field": "RankSignal",
        "scope": "Action",
        "target": {"scope": "Action", "field": "RankSignal", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_rank_signal")

def gene_buying_power_pct(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep BuyingPowerPct (Action scope) over a value set."""
    return {
        "field": "BuyingPowerPct",
        "scope": "Action",
        "target": {"scope": "Action", "field": "BuyingPowerPct", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_buying_power_pct")

def gene_allocation_pct(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep AllocationPct (Action scope) over a value set."""
    return {
        "field": "AllocationPct",
        "scope": "Action",
        "target": {"scope": "Action", "field": "AllocationPct", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_allocation_pct")

def gene_allocation_dollars(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep AllocationDollars (Action scope) over a value set."""
    return {
        "field": "AllocationDollars",
        "scope": "Action",
        "target": {"scope": "Action", "field": "AllocationDollars", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_allocation_dollars")

def gene_total_budget_pct(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep TotalBudgetPct (Action scope) over a value set."""
    return {
        "field": "TotalBudgetPct",
        "scope": "Action",
        "target": {"scope": "Action", "field": "TotalBudgetPct", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_total_budget_pct")

def gene_deployment_pct(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep DeploymentPct (Action scope) over a value set."""
    return {
        "field": "DeploymentPct",
        "scope": "Action",
        "target": {"scope": "Action", "field": "DeploymentPct", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_deployment_pct")

def gene_roll_trigger_dte(
    *,
    values: Sequence[Any],
    strategy_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Sweep RollTriggerDte (Action scope) over a value set."""
    target: Dict[str, Any] = {"scope": "Action", "field": "RollTriggerDte"}
    if strategy_index is not None:
        target["strategyIndex"] = strategy_index
    return {
        "field": "RollTriggerDte",
        "scope": "Action",
        "target": target,
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_roll_trigger_dte")

def gene_take_profit_pct(
    *,
    values: Sequence[Any],
    strategy_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Sweep TakeProfitPct (Action scope) over a value set."""
    target: Dict[str, Any] = {"scope": "Action", "field": "TakeProfitPct"}
    if strategy_index is not None:
        target["strategyIndex"] = strategy_index
    return {
        "field": "TakeProfitPct",
        "scope": "Action",
        "target": target,
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_take_profit_pct")

def gene_stop_loss_pct(
    *,
    values: Sequence[Any],
    strategy_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Sweep StopLossPct (Action scope) over a value set."""
    target: Dict[str, Any] = {"scope": "Action", "field": "StopLossPct"}
    if strategy_index is not None:
        target["strategyIndex"] = strategy_index
    return {
        "field": "StopLossPct",
        "scope": "Action",
        "target": target,
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_stop_loss_pct")

def gene_structure_kind(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep StructureKind (Action scope) over a value set."""
    return {
        "field": "StructureKind",
        "scope": "Action",
        "target": {"scope": "Action", "field": "StructureKind", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_structure_kind")

def gene_allocation_policy(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep AllocationPolicy (Action scope) over a value set."""
    return {
        "field": "AllocationPolicy",
        "scope": "Action",
        "target": {"scope": "Action", "field": "AllocationPolicy", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_allocation_policy")

def gene_exposure_policy(
    *,
    strategy_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep ExposurePolicy (Action scope) over a value set."""
    return {
        "field": "ExposurePolicy",
        "scope": "Action",
        "target": {"scope": "Action", "field": "ExposurePolicy", "strategyIndex": strategy_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_exposure_policy")

def gene_option_delta(
    *,
    strategy_index: int,
    leg_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep OptionDelta (OptionLeg scope) over a value set."""
    return {
        "field": "OptionDelta",
        "scope": "OptionLeg",
        "target": {"scope": "OptionLeg", "field": "OptionDelta", "strategyIndex": strategy_index, "legIndex": leg_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_option_delta")

def gene_strike_distance(
    *,
    strategy_index: int,
    leg_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep StrikeDistance (OptionLeg scope) over a value set."""
    return {
        "field": "StrikeDistance",
        "scope": "OptionLeg",
        "target": {"scope": "OptionLeg", "field": "StrikeDistance", "strategyIndex": strategy_index, "legIndex": leg_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_strike_distance")

def gene_dte_bracket(
    *,
    strategy_index: int,
    leg_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep DteBracket (OptionLeg scope) over a value set."""
    return {
        "field": "DteBracket",
        "scope": "OptionLeg",
        "target": {"scope": "OptionLeg", "field": "DteBracket", "strategyIndex": strategy_index, "legIndex": leg_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_dte_bracket")

def gene_max_spread_pct(
    *,
    strategy_index: int,
    leg_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep MaxSpreadPct (OptionLeg scope) over a value set."""
    return {
        "field": "MaxSpreadPct",
        "scope": "OptionLeg",
        "target": {"scope": "OptionLeg", "field": "MaxSpreadPct", "strategyIndex": strategy_index, "legIndex": leg_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_max_spread_pct")

def gene_select_top_limit(
    *,
    strategy_index: int,
    values: Sequence[Any],
    sleeve_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Sweep SelectTopLimit (UniversePipeline scope) over a value set."""
    target: Dict[str, Any] = {"scope": "UniversePipeline", "field": "SelectTopLimit", "strategyIndex": strategy_index}
    if sleeve_name is not None:
        target["sleeveName"] = sleeve_name
    return {
        "field": "SelectTopLimit",
        "scope": "UniversePipeline",
        "target": target,
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_select_top_limit")

def gene_universe_pipeline_filter(
    *,
    strategy_index: int,
    stage_index: int,
    values: Sequence[Any],
) -> Dict[str, Any]:
    """Sweep UniversePipelineFilter (UniversePipeline scope) over a value set."""
    return {
        "field": "UniversePipelineFilter",
        "scope": "UniversePipeline",
        "target": {"scope": "UniversePipeline", "field": "UniversePipelineFilter", "strategyIndex": strategy_index, "stageIndex": stage_index},
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_universe_pipeline_filter")

def gene_append_universe_pipeline_filter(
    *,
    strategy_index: int,
    values: Sequence[Any],
    sleeve_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Sweep UniversePipelineFilter (UniversePipeline scope) by adding a Filter stage to a pipeline that has none to replace. The stage goes ahead of the first SelectTop/SelectPercentile, so it narrows the universe before ranking."""
    target: Dict[str, Any] = {"scope": "UniversePipeline", "field": "UniversePipelineFilter", "strategyIndex": strategy_index, "append": True}
    if sleeve_name is not None:
        target["sleeveName"] = sleeve_name
    return {
        "field": "UniversePipelineFilter",
        "scope": "UniversePipeline",
        "target": target,
        "values": [_gene_wire_value(value) for value in values],
    }

__all__.append("gene_append_universe_pipeline_filter")
