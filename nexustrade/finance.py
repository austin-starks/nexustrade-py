"""Dependency-free accounting and valuation arithmetic.

These functions deliberately do not choose forecasts, tax rates, capital
structure assumptions, or accounting classifications. They make an analyst's
disclosed inputs mechanically reproducible and fail on ambiguous inputs instead
of silently substituting zero.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any

Number = int | float


def _flow_periods(
    count: int, valuation_date: str | None, cash_flow_dates: Sequence[str] | None,
) -> list[float]:
    """Return annual exponents; dated flows use Actual/365 from valuation."""
    if valuation_date is None and cash_flow_dates is None:
        return [float(i) for i in range(1, count + 1)]
    if valuation_date is None or cash_flow_dates is None:
        raise ValueError("supply valuation_date and cash_flow_dates together")
    if isinstance(cash_flow_dates, (str, bytes)) or len(cash_flow_dates) != count:
        raise ValueError("cash_flow_dates must align one-to-one with cash flows")
    try:
        start = date.fromisoformat(valuation_date)
        dates = [date.fromisoformat(value) for value in cash_flow_dates]
    except (TypeError, ValueError) as error:
        raise ValueError("valuation and cash-flow dates must be ISO calendar dates") from error
    previous = start
    for payment in dates:
        if payment <= previous:
            raise ValueError("cash_flow_dates must increase strictly after valuation_date")
        previous = payment
    return [(payment - start).days / 365.0 for payment in dates]


def _finite(name: str, value: Number) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _rate(name: str, value: Number, *, lower: float = -1.0) -> float:
    result = _finite(name, value)
    if result <= lower:
        raise ValueError(f"{name} must be greater than {lower}")
    return result


def _tax_rate(value: Number) -> float:
    result = _finite("tax_rate", value)
    if result < 0.0 or result > 1.0:
        raise ValueError("tax_rate must be between 0 and 1")
    return result


def nopat(operating_income: Number, tax_rate: Number) -> float:
    """Return operating profit after an explicitly supplied operating tax rate."""
    return _finite("operating_income", operating_income) * (1.0 - _tax_rate(tax_rate))


def operating_nwc(
    operating_current_assets: Number,
    operating_current_liabilities: Number,
) -> float:
    """Return operating current assets less operating current liabilities."""
    return _finite("operating_current_assets", operating_current_assets) - _finite(
        "operating_current_liabilities", operating_current_liabilities
    )


def change_in_operating_nwc(
    current_operating_nwc: Number,
    prior_operating_nwc: Number,
) -> float:
    """Return current less prior operating NWC; an increase is a cash use."""
    return _finite("current_operating_nwc", current_operating_nwc) - _finite(
        "prior_operating_nwc", prior_operating_nwc
    )


def fcff(
    nopat_value: Number,
    depreciation_and_amortization: Number,
    capital_expenditures: Number,
    change_in_operating_nwc_value: Number,
) -> float:
    """Return FCFF = NOPAT + D&A - capex - change in operating NWC."""
    return (
        _finite("nopat_value", nopat_value)
        + _finite("depreciation_and_amortization", depreciation_and_amortization)
        - _finite("capital_expenditures", capital_expenditures)
        - _finite("change_in_operating_nwc_value", change_in_operating_nwc_value)
    )


def invested_capital_from_operations(
    operating_assets: Number,
    operating_liabilities: Number,
    capitalized_operating_assets: Number = 0.0,
) -> float:
    """Return operating assets less operating liabilities plus capitalized assets."""
    return (
        _finite("operating_assets", operating_assets)
        - _finite("operating_liabilities", operating_liabilities)
        + _finite("capitalized_operating_assets", capitalized_operating_assets)
    )


def net_investment(
    capital_expenditures: Number,
    depreciation_and_amortization: Number,
    change_in_operating_nwc_value: Number,
) -> float:
    """Return capex less D&A plus the cash invested in operating NWC."""
    return (
        _finite("capital_expenditures", capital_expenditures)
        - _finite("depreciation_and_amortization", depreciation_and_amortization)
        + _finite("change_in_operating_nwc_value", change_in_operating_nwc_value)
    )


def return_on_invested_capital(nopat_value: Number, invested_capital: Number) -> float:
    """Return NOPAT divided by beginning or average invested capital."""
    capital = _finite("invested_capital", invested_capital)
    if capital <= 0.0:
        raise ValueError("invested_capital must be positive")
    return _finite("nopat_value", nopat_value) / capital


def incremental_return_on_invested_capital(
    current_nopat: Number,
    prior_nopat: Number,
    current_invested_capital: Number,
    prior_invested_capital: Number,
) -> float:
    """Return change in NOPAT divided by change in invested capital."""
    change_in_capital = _finite(
        "current_invested_capital", current_invested_capital
    ) - _finite("prior_invested_capital", prior_invested_capital)
    if change_in_capital == 0.0:
        raise ValueError("change in invested capital must be non-zero")
    return (
        _finite("current_nopat", current_nopat)
        - _finite("prior_nopat", prior_nopat)
    ) / change_in_capital


def reinvestment_rate(net_investment_value: Number, nopat_value: Number) -> float:
    """Return net operating investment divided by NOPAT."""
    operating_profit = _finite("nopat_value", nopat_value)
    if operating_profit == 0.0:
        raise ValueError("nopat_value must be non-zero")
    return _finite("net_investment_value", net_investment_value) / operating_profit


def economic_value_added(
    nopat_value: Number,
    invested_capital: Number,
    cost_of_capital: Number,
) -> float:
    """Return NOPAT less the dollar capital charge."""
    capital = _finite("invested_capital", invested_capital)
    if capital < 0.0:
        raise ValueError("invested_capital must be non-negative")
    return _finite("nopat_value", nopat_value) - capital * _rate(
        "cost_of_capital", cost_of_capital
    )


def capm_cost_of_equity(
    risk_free_rate: Number,
    beta: Number,
    equity_risk_premium: Number,
) -> float:
    """Return CAPM cost of equity from explicitly supplied market inputs."""
    return _rate("risk_free_rate", risk_free_rate) + _finite("beta", beta) * _finite(
        "equity_risk_premium", equity_risk_premium
    )


def wacc(
    equity_value: Number,
    debt_value: Number,
    cost_of_equity: Number,
    pretax_cost_of_debt: Number,
    tax_rate: Number,
) -> float:
    """Return market-value-weighted after-tax cost of capital."""
    equity = _finite("equity_value", equity_value)
    debt = _finite("debt_value", debt_value)
    if equity < 0.0 or debt < 0.0 or equity + debt <= 0.0:
        raise ValueError("equity_value and debt_value must form positive capital")
    equity_cost = _rate("cost_of_equity", cost_of_equity)
    debt_cost = _rate("pretax_cost_of_debt", pretax_cost_of_debt)
    tax = _tax_rate(tax_rate)
    total = equity + debt
    return equity / total * equity_cost + debt / total * debt_cost * (1.0 - tax)


def present_value_cash_flows(
    cash_flows: Sequence[Number],
    discount_rate: Number,
    *,
    valuation_date: str | None = None,
    cash_flow_dates: Sequence[str] | None = None,
) -> float:
    """Discount future cash flows; optional dates use Actual/365 annual rates.

    Dated amounts must contain only cash flows remaining after valuation. This
    helper does not infer or subtract elapsed cash flows from full-year totals.
    Without dates, preserve the period-1-through-period-N convention.
    """
    rate = _rate("discount_rate", discount_rate)
    values = [
        _finite(f"cash_flows[{index}]", value)
        for index, value in enumerate(cash_flows)
    ]
    periods = _flow_periods(len(values), valuation_date, cash_flow_dates)
    return sum(
        value / (1.0 + rate) ** period
        for period, value in zip(periods, values)
    )


def gordon_growth_terminal_value(
    final_forecast_fcff: Number,
    discount_rate: Number,
    perpetual_growth_rate: Number,
) -> float:
    """Return terminal enterprise value at the final forecast date."""
    final_fcff = _finite("final_forecast_fcff", final_forecast_fcff)
    discount = _rate("discount_rate", discount_rate)
    growth = _rate("perpetual_growth_rate", perpetual_growth_rate)
    if discount <= growth:
        raise ValueError("discount_rate must exceed perpetual_growth_rate")
    return final_fcff * (1.0 + growth) / (discount - growth)


def enterprise_value_from_fcff(
    forecast_fcff: Sequence[Number],
    discount_rate: Number,
    terminal_value: Number,
    *,
    valuation_date: str | None = None,
    cash_flow_dates: Sequence[str] | None = None,
) -> float:
    """Return time-zero enterprise value from forecast FCFF and terminal value."""
    rate = _rate("discount_rate", discount_rate)
    values = [
        _finite(f"forecast_fcff[{index}]", value)
        for index, value in enumerate(forecast_fcff)
    ]
    if not values:
        raise ValueError("forecast_fcff must contain at least one period")
    periods = _flow_periods(len(values), valuation_date, cash_flow_dates)
    return present_value_cash_flows(values, rate, valuation_date=valuation_date,
                                   cash_flow_dates=cash_flow_dates) + _finite(
        "terminal_value", terminal_value
    ) / (1.0 + rate) ** periods[-1]


def enterprise_to_equity_value(
    enterprise_value: Number,
    cash_and_non_operating_assets: Number,
    debt_and_debt_like_liabilities: Number,
    other_senior_claims: Number = 0.0,
) -> float:
    """Apply one explicit enterprise-to-equity bridge at the same date."""
    return (
        _finite("enterprise_value", enterprise_value)
        + _finite("cash_and_non_operating_assets", cash_and_non_operating_assets)
        - _finite("debt_and_debt_like_liabilities", debt_and_debt_like_liabilities)
        - _finite("other_senior_claims", other_senior_claims)
    )


def per_share_value(equity_value: Number, diluted_shares: Number) -> float:
    """Return equity value per diluted share."""
    shares = _finite("diluted_shares", diluted_shares)
    if shares <= 0.0:
        raise ValueError("diluted_shares must be positive")
    return _finite("equity_value", equity_value) / shares


def probability_weighted_value(
    values: Sequence[Number],
    probabilities: Sequence[Number],
) -> float:
    """Return a probability-weighted value after validating probability mass."""
    normalized_values = [
        _finite(f"values[{index}]", value)
        for index, value in enumerate(values)
    ]
    normalized_probabilities = [
        _finite(f"probabilities[{index}]", value)
        for index, value in enumerate(probabilities)
    ]
    if not normalized_values or len(normalized_values) != len(normalized_probabilities):
        raise ValueError("values and probabilities must have the same non-zero length")
    if any(value < 0.0 for value in normalized_probabilities):
        raise ValueError("probabilities must be non-negative")
    if not math.isclose(sum(normalized_probabilities), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("probabilities must sum to 1")
    return sum(
        value * probability
        for value, probability in zip(
            normalized_values, normalized_probabilities, strict=True
        )
    )


def margin_of_safety(intrinsic_value: Number, market_price: Number) -> float:
    """Return the intrinsic-value discount: (intrinsic - price) / intrinsic.

    This is retained for compatibility. Prefer the explicitly named price
    comparison helpers in new analysis so a negative discount is not mislabeled
    as the positive premium paid above intrinsic value.
    """
    return price_discount_to_intrinsic_value(intrinsic_value, market_price)


def price_discount_to_intrinsic_value(
    intrinsic_value: Number, market_price: Number
) -> float:
    """Return (intrinsic - price) / intrinsic; positive means price is cheaper."""
    intrinsic = _finite("intrinsic_value", intrinsic_value)
    if intrinsic <= 0.0:
        raise ValueError("intrinsic_value must be positive")
    return (intrinsic - _finite("market_price", market_price)) / intrinsic


def price_upside_to_intrinsic_value(
    intrinsic_value: Number, market_price: Number
) -> float:
    """Return (intrinsic - price) / price; positive means upside from price."""
    price = _finite("market_price", market_price)
    if price <= 0.0:
        raise ValueError("market_price must be positive")
    return (_finite("intrinsic_value", intrinsic_value) - price) / price


def price_premium_to_intrinsic_value(
    market_price: Number, intrinsic_value: Number
) -> float:
    """Return (price - intrinsic) / intrinsic; positive means price is dearer."""
    intrinsic = _finite("intrinsic_value", intrinsic_value)
    if intrinsic <= 0.0:
        raise ValueError("intrinsic_value must be positive")
    return (_finite("market_price", market_price) - intrinsic) / intrinsic


#: The exact definition of every price comparison this module emits. A writer
#: handed a bare ``discount`` scalar has to guess a denominator, and a delivered
#: report once labelled its discount column with the UPSIDE formula — a
#: different number that was also present in the same handoff. Carry the
#: definition with the number so the writer quotes it instead of inventing one.
PRICE_COMPARISON_DEFINITIONS = {
    "discount_to_intrinsic_value": "(intrinsic_value - market_price) / intrinsic_value",
    "upside_to_intrinsic_value": "(intrinsic_value - market_price) / market_price",
    "premium_to_intrinsic_value": "(market_price - intrinsic_value) / intrinsic_value",
}


def price_comparison(intrinsic_value: Number, market_price: Number) -> dict[str, Any]:
    """Return every price/value comparison with its own denominator named.

    The three differ only in denominator and sign, so a single scalar labelled
    "discount" is ambiguous on its face. Each value here ships beside the exact
    expression that produced it and the inputs it used.
    """
    intrinsic = _finite("intrinsic_value", intrinsic_value)
    price = _finite("market_price", market_price)
    return {
        "intrinsic_value": intrinsic,
        "market_price": price,
        "discount_to_intrinsic_value": price_discount_to_intrinsic_value(
            intrinsic, price
        ),
        "upside_to_intrinsic_value": price_upside_to_intrinsic_value(
            intrinsic, price
        ),
        "premium_to_intrinsic_value": price_premium_to_intrinsic_value(
            price, intrinsic
        ),
        "definitions": dict(PRICE_COMPARISON_DEFINITIONS),
    }


def hurdle_comparison(
    cases: Mapping[str, Number], hurdle: Number, *, hurdle_name: str = "hurdle"
) -> dict[str, Any]:
    """Split named returns into those that clear a hurdle and those that miss.

    Exists so a sentence like "only the bull case clears the hurdle" is DERIVED
    from the same numbers the table prints rather than written separately and
    left behind when the numbers change. A repaired model once cut its best-case
    IRR below the cost of capital while the prose still said that case cleared it.

    Equality is a miss: a return exactly at the cost of capital creates no value.
    """
    if not isinstance(hurdle_name, str) or not hurdle_name.strip():
        raise ValueError("hurdle_name must be an explicit nonempty string")
    if not isinstance(cases, Mapping) or not cases:
        raise ValueError("cases must be a nonempty mapping of name to return")
    threshold = _finite(hurdle_name, hurdle)
    values = {
        str(name): _finite(f"cases[{name!r}]", value)
        for name, value in cases.items()
    }
    clears = [name for name, value in values.items() if value > threshold]
    misses = [name for name, value in values.items() if value <= threshold]
    return {
        "hurdle": threshold,
        "hurdle_name": hurdle_name,
        "cases": values,
        "clears": clears,
        "misses": misses,
        "spreads": {name: value - threshold for name, value in values.items()},
        "any_clears": bool(clears),
        "all_clear": not misses,
        "comparison": f"case return strictly greater than {hurdle_name}",
    }


def cash_flow_after_equity_compensation(
    reported_cash_flow: Number,
    stock_based_compensation: Number,
) -> float:
    """Deduct SBC from a cash-flow measure that added it back as non-cash.

    This is an analyst adjustment, not GAAP cash flow. Keep the reported and
    adjusted series side by side in user-facing work.
    """
    compensation = _finite("stock_based_compensation", stock_based_compensation)
    if compensation < 0.0:
        raise ValueError("stock_based_compensation must be non-negative")
    return _finite("reported_cash_flow", reported_cash_flow) - compensation


def capitalize_operating_expense(
    expense_history: Sequence[Number],
    amortization_years: int,
) -> dict[str, float]:
    """Straight-line capitalize a current operating expense such as R&D.

    `expense_history` is chronological and ends with the current period. A
    current-period outlay enters the asset immediately and starts amortizing in
    the next period. The returned operating-income adjustment is current expense
    less amortization. Classification and useful-life choice remain the analyst's.
    """
    if isinstance(amortization_years, bool) or not isinstance(amortization_years, int):
        raise ValueError("amortization_years must be a positive integer")
    if amortization_years <= 0:
        raise ValueError("amortization_years must be a positive integer")
    expenses = [
        _finite(f"expense_history[{index}]", value)
        for index, value in enumerate(expense_history)
    ]
    if not expenses:
        raise ValueError("expense_history must contain at least one period")
    if any(value < 0.0 for value in expenses):
        raise ValueError("expense_history must be non-negative")
    current_expense = expenses[-1]
    prior_vintages = list(reversed(expenses[:-1]))[:amortization_years]
    current_amortization = sum(prior_vintages) / amortization_years
    unamortized_asset = current_expense + sum(
        expense * (amortization_years - age) / amortization_years
        for age, expense in enumerate(prior_vintages, start=1)
    )
    return {
        "current_expense": current_expense,
        "current_amortization": current_amortization,
        "unamortized_asset": unamortized_asset,
        "operating_income_adjustment": current_expense - current_amortization,
    }


def gordon_growth_terminal_value_from_nopat(
    final_forecast_nopat: Number,
    discount_rate: Number,
    perpetual_growth_rate: Number,
    return_on_new_invested_capital: Number,
) -> dict[str, float]:
    """Return a Gordon terminal value with explicit steady-state reinvestment.

    Growth requires reinvestment: reinvestment rate = g / return on new invested
    capital. The terminal cash flow is next-period NOPAT after that reinvestment.
    """
    discount = _rate("discount_rate", discount_rate)
    growth = _rate("perpetual_growth_rate", perpetual_growth_rate)
    if discount <= growth:
        raise ValueError("discount_rate must exceed perpetual_growth_rate")
    ronic = _finite(
        "return_on_new_invested_capital", return_on_new_invested_capital
    )
    if ronic <= 0.0:
        raise ValueError("return_on_new_invested_capital must be positive")
    terminal_reinvestment_rate = growth / ronic
    if terminal_reinvestment_rate < 0.0 or terminal_reinvestment_rate > 1.0:
        raise ValueError("terminal reinvestment rate must be between 0 and 1")
    next_period_nopat = _finite("final_forecast_nopat", final_forecast_nopat) * (
        1.0 + growth
    )
    terminal_fcff = next_period_nopat * (1.0 - terminal_reinvestment_rate)
    return {
        "reinvestment_rate": terminal_reinvestment_rate,
        "next_period_nopat": next_period_nopat,
        "terminal_fcff": terminal_fcff,
        "terminal_value": terminal_fcff / (discount - growth),
    }


def internal_rate_of_return(
    cash_flows: Sequence[Number], *, valuation_date: str | None = None,
    cash_flow_dates: Sequence[str] | None = None,
) -> float:
    """Solve conventional IRR; optional dates describe flows AFTER initial outlay.

    The initial outlay occurs on valuation_date. Supply len(cash_flows)-1 future
    dates, using the same dates as the valuation. Annualization is Actual/365.
    """
    values = [
        _finite(f"cash_flows[{index}]", value)
        for index, value in enumerate(cash_flows)
    ]
    if len(values) < 2 or values[0] >= 0.0:
        raise ValueError(
            "cash_flows must be conventional and begin with an initial outflow"
        )
    first_inflow = next(
        (index for index, value in enumerate(values) if value > 0.0), None
    )
    if first_inflow is None:
        raise ValueError("cash_flows must include at least one positive future flow")
    if any(value < 0.0 for value in values[first_inflow + 1:]):
        raise ValueError(
            "cash_flows must be conventional: initial outflows followed by "
            "non-negative flows"
        )

    periods = [0.0, *_flow_periods(len(values) - 1, valuation_date, cash_flow_dates)]

    def npv(rate: float) -> float:
        return sum(
            value / (1.0 + rate) ** period
            for period, value in zip(periods, values)
        )

    low = -0.999999999
    high = 1.0
    while npv(high) > 0.0 and high < 1_000_000.0:
        high = high * 2.0 + 1.0
    if npv(high) > 0.0:
        raise ValueError("cash_flows do not produce a finite conventional IRR")
    for _ in range(200):
        midpoint = (low + high) / 2.0
        if npv(midpoint) > 0.0:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def operating_period_metrics(
    *,
    operating_income: Number,
    tax_rate: Number,
    depreciation_and_amortization: Number,
    capital_expenditures: Number,
    current_operating_nwc: Number,
    prior_operating_nwc: Number,
    current_invested_capital: Number,
    prior_invested_capital: Number,
    cost_of_capital: Number,
) -> dict[str, float]:
    """Return one reproducible operating-period bridge from explicit inputs.

    This composes the public primitives; it does not decide which filing facts
    are operating, normalize capex, capitalize R&D, or choose a tax rate/WACC.
    ROIC and EVA use average beginning/ending invested capital.
    """
    current_capital = _finite("current_invested_capital", current_invested_capital)
    prior_capital = _finite("prior_invested_capital", prior_invested_capital)
    average_capital = (current_capital + prior_capital) / 2.0
    if average_capital <= 0.0:
        raise ValueError("average invested capital must be positive")
    operating_profit_after_tax = nopat(operating_income, tax_rate)
    nwc_change = change_in_operating_nwc(
        current_operating_nwc,
        prior_operating_nwc,
    )
    investment = net_investment(
        capital_expenditures,
        depreciation_and_amortization,
        nwc_change,
    )
    return {
        "nopat": operating_profit_after_tax,
        "change_in_operating_nwc": nwc_change,
        "fcff": fcff(
            operating_profit_after_tax,
            depreciation_and_amortization,
            capital_expenditures,
            nwc_change,
        ),
        "average_invested_capital": average_capital,
        "roic": return_on_invested_capital(
            operating_profit_after_tax,
            average_capital,
        ),
        "net_investment": investment,
        "reinvestment_rate": reinvestment_rate(
            investment,
            operating_profit_after_tax,
        ),
        "eva": economic_value_added(
            operating_profit_after_tax,
            average_capital,
            cost_of_capital,
        ),
    }


def fcff_valuation_case(
    *,
    forecast_fcff: Sequence[Number],
    discount_rate: Number,
    perpetual_growth_rate: Number | None = None,
    terminal_value: Number | None = None,
    cash_and_non_operating_assets: Number,
    debt_and_debt_like_liabilities: Number,
    diluted_shares: Number,
    other_senior_claims: Number = 0.0,
    market_price: Number | None = None,
    valuation_date: str | None = None,
    cash_flow_dates: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Value FCFF with either perpetual growth or an explicit terminal EV.

    ``terminal_value`` is undiscounted enterprise value at the end of the final
    forecast period, including values from gordon_growth_terminal_value_from_nopat.
    Supply exactly one terminal construction. Existing growth-only calls retain
    their behavior. Growth-based terminal value requires a normalized full-year
    final FCFF; if the last flow is a stub, supply a separately modeled explicit
    terminal_value instead of growing the stub into a perpetuity.
    """
    values = [
        _finite(f"forecast_fcff[{index}]", value)
        for index, value in enumerate(forecast_fcff)
    ]
    if not values:
        raise ValueError("forecast_fcff must contain at least one period")
    if (perpetual_growth_rate is None) == (terminal_value is None):
        raise ValueError("supply exactly one of perpetual_growth_rate or terminal_value")
    terminal = (
        _finite("terminal_value", terminal_value)
        if terminal_value is not None
        else gordon_growth_terminal_value(values[-1], discount_rate, perpetual_growth_rate)
    )
    enterprise = enterprise_value_from_fcff(values, discount_rate, terminal,
                                          valuation_date=valuation_date,
                                          cash_flow_dates=cash_flow_dates)
    equity = enterprise_to_equity_value(
        enterprise,
        cash_and_non_operating_assets,
        debt_and_debt_like_liabilities,
        other_senior_claims,
    )
    per_share = per_share_value(equity, diluted_shares)
    result = {
        "terminal_value": terminal,
        "enterprise_value": enterprise,
        "equity_value": equity,
        "per_share_value": per_share,
    }
    if market_price is not None:
        # margin_of_safety is the discount to intrinsic value. Emit the full
        # comparison set beside it so a downstream writer never has to infer a
        # denominator from the label alone.
        result["margin_of_safety"] = margin_of_safety(per_share, market_price)
        result["price_comparison"] = price_comparison(per_share, market_price)
    return result


def _calendar_date(name: str, value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a YYYY-MM-DD calendar date") from error


def elapsed_period_fraction(
    *, period_start: str, period_end: str, as_of: str
) -> dict[str, Any]:
    """Return the INCLUSIVE elapsed-day fraction of a calendar period.

    Matches ``remaining_period_flow``: the valuation is after operations on
    ``as_of``, so ``as_of`` itself is elapsed and the remainder begins the next
    day. A hand-rolled ``(as_of - period_start).days`` is one day short and has
    silently shipped as the elapsed fraction of a forecast stub. Both day counts
    are returned so a prorated amount and its basis can be serialized together.

    Proration is the CALLER's decision; this only counts days. A flow that is
    not earned evenly over the period must not be prorated by time at all.
    """
    start = _calendar_date("period_start", period_start)
    end = _calendar_date("period_end", period_end)
    cutoff = _calendar_date("as_of", as_of)
    if start > end:
        raise ValueError("period_start must not follow period_end")
    if not start <= cutoff <= end:
        raise ValueError("as_of must fall within the period")
    elapsed_days = (cutoff - start).days + 1
    total_days = (end - start).days + 1
    return {
        "period_start": period_start,
        "period_end": period_end,
        "as_of": as_of,
        "elapsed_days": elapsed_days,
        "total_days": total_days,
        "remaining_days": total_days - elapsed_days,
        "elapsed_fraction": elapsed_days / total_days,
        "remaining_fraction": (total_days - elapsed_days) / total_days,
        "remaining_period_start": (cutoff + timedelta(days=1)).isoformat()
        if cutoff < end
        else None,
        "convention": (
            "inclusive calendar days; as_of is elapsed and the remainder "
            "begins the following day"
        ),
    }


def observation_as_of(
    observations: Sequence[Mapping[str, Any]],
    *,
    as_of: str,
    timestamp_field: str,
    value_field: str,
) -> dict[str, Any]:
    """Return the latest observation on or before the ``as_of`` CALENDAR DATE.

    The distinction this exists for: an observation stamped ``2026-09-04
    20:00:00`` is an observation FOR 2026-09-04, but it is after the instant
    ``2026-09-04``. A cutoff written as ``timestamp <= '2026-09-04'`` compares
    against midnight and drops that day entirely, which is how a valuation once
    took the prior session's close while the requested day's close sat in the
    same frame. Selection here is by calendar date; the exact observed instant
    is returned beside the value so the calculation carries its own provenance
    instead of a scalar copied out of console output.

    Rows whose timestamp cannot be read as a date or datetime are rejected
    rather than skipped. A future observation is excluded, never clamped.
    """
    cutoff = _calendar_date("as_of", as_of)
    if isinstance(observations, (str, bytes, Mapping)):
        raise ValueError("observations must be a sequence of mappings")
    for name in ("timestamp_field", "value_field"):
        text = timestamp_field if name == "timestamp_field" else value_field
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{name} must be an explicit nonempty string")
    eligible: list[tuple[datetime, int, Mapping[str, Any]]] = []
    later = 0
    for index, row in enumerate(observations):
        if not isinstance(row, Mapping):
            raise ValueError(f"observations[{index}] must be an object")
        if timestamp_field not in row:
            raise ValueError(f"observations[{index}] has no {timestamp_field!r}")
        if value_field not in row:
            raise ValueError(f"observations[{index}] has no {value_field!r}")
        raw = row[timestamp_field]
        observed_at = _observed_instant(f"observations[{index}]", raw)
        if observed_at.date() > cutoff:
            later += 1
            continue
        eligible.append((observed_at, index, row))
    if not eligible:
        raise ValueError(
            f"no observation on or before {as_of}; "
            f"{later} later observation(s) were excluded"
        )
    # Latest instant wins; on an exact tie the LAST such row in the supplied
    # order wins, so a corrected row appended after the one it supersedes is the
    # one selected. Ordering the input therefore decides ties, deliberately.
    observed_at, _index, row = max(eligible, key=lambda item: (item[0], item[1]))
    return {
        "value": row[value_field],
        "observed_at": observed_at.isoformat(),
        "observed_date": observed_at.date().isoformat(),
        "as_of": as_of,
        "is_as_of_date": observed_at.date() == cutoff,
        "observations_considered": len(eligible),
        "observations_after_as_of": later,
        "selection": (
            f"latest {value_field} whose {timestamp_field} calendar date is on "
            f"or before {as_of}"
        ),
        "row": deepcopy(dict(row)),
    }


def _naive_utc(value: datetime) -> datetime:
    """Return a comparable instant, CONVERTING an offset rather than dropping it.

    Discarding ``tzinfo`` would put ``2026-09-05T01:00:00+05:00`` on calendar
    date 09-05 when the instant it names is 09-04 20:00 UTC — reintroducing the
    day-boundary error this module exists to prevent. Naive values are left
    alone: the lake stores naive session-close stamps and reinterpreting them
    would shift every one of them.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _observed_instant(name: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        return _naive_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        text = value.strip().replace(" ", "T", 1)
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(
                f"{name} timestamp must be an ISO date or datetime"
            ) from None
        return _naive_utc(parsed)
    # A pandas/numpy timestamp exposes to_pydatetime(); accept it without
    # importing either, so the base install stays dependency-free.
    converter = getattr(value, "to_pydatetime", None)
    if callable(converter):
        converted = converter()
        if isinstance(converted, datetime):
            return _naive_utc(converted)
    raise ValueError(f"{name} timestamp must be an ISO date or datetime")


#: How the amount in a ``period_flow`` came to exist, independent of the prose
#: in ``definition``. ``reported`` is lifted from a published statement,
#: ``modeled`` is built from forecast primitives, ``estimated`` fills a gap.
FLOW_ORIGINS = ("reported", "modeled", "estimated")


def flow_basis(
    *, measure: str, origin: str, adjustments: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """Describe what an amount actually IS, beneath the label it is given.

    ``definition`` on a ``period_flow`` is free text, so making two flows share a
    definition is one edit away and proves nothing: an elapsed reported
    CFO-minus-capex and a modeled NOPAT+D&A-capex-dNWC forecast were once
    reconciled by giving them the same definition string, leaving the amounts and
    the accounting gap exactly as they were.

    ``measure`` names the underlying quantity (the reported line, or the modeled
    construction). ``origin`` is one of ``FLOW_ORIGINS``. ``adjustments`` are the
    explicit named bridges applied to reach ``definition`` from ``measure``, each
    ``{"name": str, "value": number}``; an unquantified bridge is not one.

    Declaring a basis records a claim for review. It does not verify that the
    bridge is complete, and nothing here should be read as approval.
    """
    if not isinstance(measure, str) or not measure.strip():
        raise ValueError("measure must be an explicit nonempty string")
    if origin not in FLOW_ORIGINS:
        raise ValueError(f"origin must be one of {', '.join(FLOW_ORIGINS)}")
    if isinstance(adjustments, (str, bytes, Mapping)):
        raise ValueError("adjustments must be a sequence of objects")
    bridged = []
    for index, adjustment in enumerate(adjustments):
        if not isinstance(adjustment, Mapping):
            raise ValueError(f"adjustments[{index}] must be an object")
        name = adjustment.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"adjustments[{index}] needs an explicit name")
        if "value" not in adjustment:
            raise ValueError(f"adjustments[{index}] needs an explicit value")
        bridged.append({**deepcopy(dict(adjustment)), "name": name,
                        "value": _finite(f"adjustments[{index}].value", adjustment["value"])})
    return {"measure": measure, "origin": origin, "adjustments": bridged,
            "total_adjustment": sum(row["value"] for row in bridged)}


def period_flow(
    value: Number | None, *, period_start: str, period_end: str, as_of: str,
    unit: str, definition: str, status: str = "model_assumption",
    provenance: Mapping[str, Any] | None = None,
    basis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe an additive flow over inclusive calendar dates, without inference.

    ``as_of`` is the information cutoff, not the end of the operating period.
    ``unit`` includes currency and scale; ``definition`` identifies the accounting
    basis. Status and provenance remain caller declarations, not verification.
    A missing amount stays None. This record must not describe a stock or ratio.

    ``basis`` optionally records what the amount is beneath its label — see
    ``flow_basis``. Supplying it lets ``remaining_period_flow`` see a reported
    proxy standing in for a modeled construction even after both were given the
    same ``definition``.
    """
    start = _calendar_date("period_start", period_start)
    end = _calendar_date("period_end", period_end)
    _calendar_date("as_of", as_of)
    if start > end:
        raise ValueError("period_start must not follow period_end")
    for name, text in (("unit", unit), ("definition", definition), ("status", status)):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{name} must be an explicit nonempty string")
    if provenance is not None and not isinstance(provenance, Mapping):
        raise ValueError("provenance must be an object")
    record = {"value": None if value is None else _finite("value", value),
              "period_start": period_start, "period_end": period_end, "as_of": as_of,
              "unit": unit, "definition": definition, "status": status,
              "provenance": deepcopy(dict(provenance or {}))}
    if basis is not None:
        if not isinstance(basis, Mapping):
            raise ValueError("basis must be an object")
        record["basis"] = flow_basis(
            measure=basis.get("measure"), origin=basis.get("origin"),
            adjustments=basis.get("adjustments", ()),
        )
    return record


def remaining_period_flow(
    full_period: Mapping[str, Any], elapsed_flows: Sequence[Mapping[str, Any]], *,
    valuation_date: str,
) -> dict[str, Any]:
    """Subtract fully covered elapsed operations; never prorate a missing stub.

    The valuation is after operations on ``valuation_date``. The result begins
    the next calendar day. Inputs must share exact units and definitions, be
    available by valuation, and cover nonoverlapping elapsed intervals within
    the full period. Missing coverage/amounts yield ``value=None``. Estimates may
    explicitly fill a gap, retaining their status and provenance for review.
    """
    def checked(record: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(record, Mapping):
            raise ValueError("period flow must be an object")
        try:
            normalized = period_flow(**{key: record[key] for key in (
                "value", "period_start", "period_end", "as_of", "unit", "definition", "status")},
                provenance=record.get("provenance"), basis=record.get("basis"))
        except KeyError as error:
            raise ValueError(f"period flow missing {error.args[0]}") from error
        # Preserve additional declared metadata rather than stripping evidence.
        return {**deepcopy(dict(record)), **normalized}

    full = checked(full_period)
    cutoff = _calendar_date("valuation_date", valuation_date)
    start = _calendar_date("period_start", full["period_start"])
    end = _calendar_date("period_end", full["period_end"])
    if not start <= cutoff < end:
        raise ValueError("valuation_date must be within the full period before its end")
    if _calendar_date("as_of", full["as_of"]) > cutoff:
        raise ValueError("full-period forecast was not available by valuation_date")
    flows = sorted((checked(row) for row in elapsed_flows), key=lambda row: row["period_start"])
    cursor = start
    missing = []
    for row in flows:
        row_start = _calendar_date("period_start", row["period_start"])
        row_end = _calendar_date("period_end", row["period_end"])
        if row["unit"] != full["unit"] or row["definition"] != full["definition"]:
            raise ValueError("elapsed flows must have the same unit and definition as the forecast")
        if _calendar_date("as_of", row["as_of"]) > cutoff:
            raise ValueError("elapsed flow was not available by valuation_date")
        if row_start < cursor or row_end > cutoff:
            raise ValueError("elapsed flows overlap or extend outside the elapsed period")
        if row_start > cursor:
            missing.append({"period_start": cursor.isoformat(), "period_end": (row_start - timedelta(days=1)).isoformat()})
        if row["value"] is None:
            missing.append({"period_start": row["period_start"], "period_end": row["period_end"]})
        cursor = row_end + timedelta(days=1)
    if cursor <= cutoff:
        missing.append({"period_start": cursor.isoformat(), "period_end": valuation_date})
    complete = not missing and full["value"] is not None
    result = period_flow(
        full["value"] - sum(row["value"] for row in flows) if complete else None,
        period_start=(cutoff + timedelta(days=1)).isoformat(), period_end=end.isoformat(),
        as_of=valuation_date, unit=full["unit"], definition=full["definition"],
        status="derived" if complete else "incomplete",
    )
    return {**result, "valuation_date": valuation_date, "missing_intervals": missing,
            "basis_reconciliation": _basis_reconciliation(full, flows),
            "full_period": full, "elapsed_flows": flows}


def _basis_reconciliation(
    full: Mapping[str, Any], flows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """State whether the elapsed amounts are the same KIND of thing as the forecast.

    Matching ``unit`` and ``definition`` is required above, but both are free
    text and an author can make them agree in one edit. This reports what the
    declared bases actually say. It is a disclosure, not an approval: an
    unreconciled result is still returned, with the mismatch attached to it.
    """
    declared = [row for row in (full, *flows) if isinstance(row.get("basis"), Mapping)]
    if not declared:
        return {"declared": False, "reconciled": None,
                "note": ("No flow declared a basis. Equal definition strings do not "
                         "establish that a reported proxy and a modeled forecast "
                         "measure the same cash flow; see flow_basis.")}
    undeclared = [row for row in (full, *flows) if not isinstance(row.get("basis"), Mapping)]
    forecast_basis = full.get("basis") if isinstance(full.get("basis"), Mapping) else None
    measures = {row["basis"]["measure"] for row in declared}
    origins = {row["basis"]["origin"] for row in declared}
    adjustments = [name for row in declared
                   for name in (item["name"] for item in row["basis"]["adjustments"])]
    reconciled = (
        bool(flows) and not undeclared and len(measures) == 1 and len(origins) == 1
    )
    return {
        "declared": True,
        "reconciled": reconciled,
        "forecast_basis": deepcopy(forecast_basis),
        "elapsed_bases": [deepcopy(row.get("basis")) for row in flows],
        "measures": sorted(measures),
        "origins": sorted(origins),
        "declared_adjustments": adjustments,
        "flows_without_declared_basis": len(undeclared),
        "note": ("Declared bases agree." if reconciled else
                 "No elapsed flow was supplied, so no basis was reconciled." if not flows else
                 "Declared bases differ. The elapsed amounts and the forecast are "
                 "not the same measurement; the difference between them is not "
                 "established by these records and needs a stated bridge or an "
                 "investigation, not a shared definition string."),
    }


def forecast_remainder(
    full_period_forecast: Number,
    actual_to_date: Number,
    *,
    prior_comparable_remainder: Number | None = None,
) -> dict[str, float | None]:
    """Expose the implied remainder of a forecast for an additive flow.

    The caller must align currency, units, fiscal periods and flow definitions.
    Do not use this for balances, ratios, or overlapping quarterly/YTD amounts.
    No change threshold is imposed. A nonpositive comparator has no growth rate;
    the absolute change remains available for review.
    """
    forecast = _finite("full_period_forecast", full_period_forecast)
    actual = _finite("actual_to_date", actual_to_date)
    remainder = forecast - actual
    prior = (None if prior_comparable_remainder is None else
             _finite("prior_comparable_remainder", prior_comparable_remainder))
    return {
        "full_period_forecast": forecast,
        "actual_to_date": actual,
        "remaining_forecast": remainder,
        "prior_comparable_remainder": prior,
        "remaining_change": None if prior is None else remainder - prior,
        "remaining_growth": None if prior is None or prior <= 0 else remainder / prior - 1,
    }


def operating_forecast_period(
    *,
    operating_income: Number,
    tax_rate: Number,
    depreciation_and_amortization: Number,
    capital_expenditures: Number,
    current_operating_nwc: Number,
    prior_operating_nwc: Number,
    prior_invested_capital: Number,
    cost_of_capital: Number,
    additional_cash_investment: Number = 0.0,
    noncash_invested_capital_changes: Number = 0.0,
) -> dict[str, float]:
    """Roll invested capital and calculate FCFF/ROIC from the same primitives.

    Operating income retains equity compensation expense; fixed current diluted
    shares are not payment for future grants. Do not add SBC to this bridge or
    deduct it from net investment. Additional cash investment (e.g. acquisitions)
    consumes FCFF and increases capital; noncash changes affect capital only.
    Model these separately, not as residuals fitted to a target ROIC. Do not
    include investments already counted in capex or working capital again.
    """
    investment = net_investment(capital_expenditures, depreciation_and_amortization,
                                change_in_operating_nwc(current_operating_nwc, prior_operating_nwc))
    cash_investment = _finite("additional_cash_investment", additional_cash_investment)
    noncash = _finite("noncash_invested_capital_changes", noncash_invested_capital_changes)
    investment += cash_investment
    current_capital = _finite("prior_invested_capital", prior_invested_capital) + investment + noncash
    metrics = operating_period_metrics(
        operating_income=operating_income, tax_rate=tax_rate,
        depreciation_and_amortization=depreciation_and_amortization,
        capital_expenditures=capital_expenditures,
        current_operating_nwc=current_operating_nwc, prior_operating_nwc=prior_operating_nwc,
        current_invested_capital=current_capital, prior_invested_capital=prior_invested_capital,
        cost_of_capital=cost_of_capital,
    )
    return {**metrics, "current_invested_capital": current_capital,
            "fcff": metrics["fcff"] - cash_investment,
            "net_investment": investment,
            "reinvestment_rate": reinvestment_rate(investment, metrics["nopat"]),
            "additional_cash_investment": cash_investment,
            "noncash_invested_capital_changes": noncash}


def equity_return_case(
    *,
    entry_price: Number,
    interim_distributions: Sequence[Number],
    exit_price: Number,
    required_return: Number | None = None,
) -> dict[str, float]:
    """Return holding-period IRR and, optionally, the hurdle-consistent entry.

    Each distribution occurs at the end of its numbered period. The exit occurs
    with the final distribution. This keeps timing explicit and avoids treating
    a time-zero DCF value as a future exit price.
    """
    entry = _finite("entry_price", entry_price)
    if entry <= 0.0:
        raise ValueError("entry_price must be positive")
    distributions = [
        _finite(f"interim_distributions[{index}]", value)
        for index, value in enumerate(interim_distributions)
    ]
    if not distributions:
        raise ValueError("interim_distributions must contain at least one period")
    if any(value < 0.0 for value in distributions):
        raise ValueError("interim_distributions must be non-negative")
    exit_value = _finite("exit_price", exit_price)
    if exit_value < 0.0:
        raise ValueError("exit_price must be non-negative")
    cash_flows = [-entry, *distributions]
    cash_flows[-1] += exit_value
    result = {"irr": internal_rate_of_return(cash_flows)}
    if required_return is not None:
        hurdle = _rate("required_return", required_return)
        result["hurdle_entry_price"] = present_value_cash_flows(
            [*distributions[:-1], distributions[-1] + exit_value],
            hurdle,
        )
    return result


__all__ = [
    "capm_cost_of_equity",
    "change_in_operating_nwc",
    "cash_flow_after_equity_compensation",
    "capitalize_operating_expense",
    "enterprise_to_equity_value",
    "enterprise_value_from_fcff",
    "economic_value_added",
    "equity_return_case",
    "fcff",
    "fcff_valuation_case",
    "flow_basis",
    "FLOW_ORIGINS",
    "forecast_remainder",
    "period_flow",
    "remaining_period_flow",
    "elapsed_period_fraction",
    "gordon_growth_terminal_value",
    "gordon_growth_terminal_value_from_nopat",
    "hurdle_comparison",
    "internal_rate_of_return",
    "incremental_return_on_invested_capital",
    "invested_capital_from_operations",
    "margin_of_safety",
    "nopat",
    "net_investment",
    "observation_as_of",
    "operating_nwc",
    "operating_period_metrics",
    "operating_forecast_period",
    "per_share_value",
    "price_comparison",
    "PRICE_COMPARISON_DEFINITIONS",
    "price_discount_to_intrinsic_value",
    "price_premium_to_intrinsic_value",
    "price_upside_to_intrinsic_value",
    "present_value_cash_flows",
    "probability_weighted_value",
    "reinvestment_rate",
    "return_on_invested_capital",
    "wacc",
]
