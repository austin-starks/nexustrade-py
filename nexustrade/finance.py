"""Dependency-free accounting and valuation arithmetic.

These functions deliberately do not choose forecasts, tax rates, capital
structure assumptions, or accounting classifications. They make an analyst's
disclosed inputs mechanically reproducible and fail on ambiguous inputs instead
of silently substituting zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Number = int | float


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
) -> float:
    """Discount period-1-through-period-N cash flows to time zero."""
    rate = _rate("discount_rate", discount_rate)
    values = [
        _finite(f"cash_flows[{index}]", value)
        for index, value in enumerate(cash_flows)
    ]
    return sum(
        value / (1.0 + rate) ** period
        for period, value in enumerate(values, start=1)
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
) -> float:
    """Return time-zero enterprise value from forecast FCFF and terminal value."""
    rate = _rate("discount_rate", discount_rate)
    values = [
        _finite(f"forecast_fcff[{index}]", value)
        for index, value in enumerate(forecast_fcff)
    ]
    if not values:
        raise ValueError("forecast_fcff must contain at least one period")
    return present_value_cash_flows(values, rate) + _finite(
        "terminal_value", terminal_value
    ) / (1.0 + rate) ** len(values)


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
    """Return (intrinsic value - price) / intrinsic value."""
    intrinsic = _finite("intrinsic_value", intrinsic_value)
    if intrinsic <= 0.0:
        raise ValueError("intrinsic_value must be positive")
    return (intrinsic - _finite("market_price", market_price)) / intrinsic


def internal_rate_of_return(cash_flows: Sequence[Number]) -> float:
    """Solve a unique conventional IRR for an initial outflow and later inflows."""
    values = [
        _finite(f"cash_flows[{index}]", value)
        for index, value in enumerate(cash_flows)
    ]
    if len(values) < 2 or values[0] >= 0.0 or any(value < 0.0 for value in values[1:]):
        raise ValueError(
            "cash_flows must be conventional: one initial outflow followed by "
            "non-negative flows"
        )
    if not any(value > 0.0 for value in values[1:]):
        raise ValueError("cash_flows must include at least one positive future flow")

    def npv(rate: float) -> float:
        return sum(
            value / (1.0 + rate) ** period
            for period, value in enumerate(values)
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
    perpetual_growth_rate: Number,
    cash_and_non_operating_assets: Number,
    debt_and_debt_like_liabilities: Number,
    diluted_shares: Number,
    other_senior_claims: Number = 0.0,
    market_price: Number | None = None,
) -> dict[str, float]:
    """Return an internally consistent FCFF-to-per-share valuation bridge."""
    values = [
        _finite(f"forecast_fcff[{index}]", value)
        for index, value in enumerate(forecast_fcff)
    ]
    if not values:
        raise ValueError("forecast_fcff must contain at least one period")
    terminal = gordon_growth_terminal_value(
        values[-1],
        discount_rate,
        perpetual_growth_rate,
    )
    enterprise = enterprise_value_from_fcff(values, discount_rate, terminal)
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
        result["margin_of_safety"] = margin_of_safety(per_share, market_price)
    return result


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
    "enterprise_to_equity_value",
    "enterprise_value_from_fcff",
    "economic_value_added",
    "equity_return_case",
    "fcff",
    "fcff_valuation_case",
    "gordon_growth_terminal_value",
    "internal_rate_of_return",
    "incremental_return_on_invested_capital",
    "invested_capital_from_operations",
    "margin_of_safety",
    "nopat",
    "net_investment",
    "operating_nwc",
    "operating_period_metrics",
    "per_share_value",
    "present_value_cash_flows",
    "probability_weighted_value",
    "reinvestment_rate",
    "return_on_invested_capital",
    "wacc",
]
