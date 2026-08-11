"""NexusTrade SDK.

The authoring/API surface has no third-party runtime dependencies. Analytics,
lake, document, and compute-host helpers live in public submodules and are
resolved lazily, so ``pip install nexustrade`` remains useful without extras.
``nexustrade.lake`` and ``nexustrade.tigris`` need ``[lake]`` for local
materialization; PDF inspection/OCR needs ``[documents]``.

``# sandbox-prune:begin/end <region>`` comments mark code the compute image
strips from its copy of this package — agent runs are refused for compute
principals server-side, so the sandbox never sees that surface. They have no
effect on an ordinary install: everything between them is live code here. Keep
the pair balanced when editing, and put new agent entry points inside it. See
infra/sandbox-compute/prune_agent_surface.py.
"""

from importlib import import_module
from typing import Any

from nexustrade import portfolio as _portfolio
# sandbox-prune:begin agent-surface
from nexustrade.agent import AgentEvent, AgentRun
# sandbox-prune:end agent-surface
from nexustrade.client import (
    HttpTransport,
    NexusTradeApiError,
    NexusTradeClient,
    create_custom_indicator,
    create_portfolio,
    wait_for_operation,
)
from nexustrade.portfolio import *
from nexustrade.portfolio_handle import DeployResult, Portfolio, PortfolioList

__all__ = [
    *_portfolio.__all__,
    # sandbox-prune:begin agent-surface
    "AgentEvent",
    "AgentRun",
    # sandbox-prune:end agent-surface
    "DeployResult",
    "HttpTransport",
    "NexusTradeApiError",
    "NexusTradeClient",
    "Portfolio",
    "PortfolioList",
    "create_custom_indicator",
    "create_portfolio",
    "wait_for_operation",
]

# Preserve top-level compute imports without making optional analytics/lake
# dependencies a requirement for ordinary SDK clients. Resolved on attribute
# access only — see the note above ``__dir__``.
# Which extra installs each optional module. `nt.lake` needs duckdb/pyarrow,
# `nt.stats` needs numpy/pandas — naming the wrong one in the error sends people
# to install the wrong thing.
_OPTIONAL_EXTRA_BY_MODULE = {
    "nexustrade.inspect_document": "documents",
    "nexustrade.lake": "lake",
    "nexustrade.scanned_table": "documents",
    "nexustrade.stats": "stats",
    "nexustrade.tigris": "lake",
}

_LAZY_EXPORTS = {
    # `(module, None)` exposes the SUBMODULE itself, so `nt.lake.sql(...)`
    # resolves lazily without making duckdb a base-install dependency.
    "lake": ("nexustrade.lake", None),
    # No optional dependency of its own — lazy purely so `nt.nl` matches the
    # `nt.lake` shape rather than importing at package load.
    "nl": ("nexustrade.nl", None),
    "stats": ("nexustrade.stats", None),
    "flush_requests": ("nexustrade.host", "flush_requests"),
    "gateway_chat": ("nexustrade.host", "gateway_chat"),
    "gateway_chat_json": ("nexustrade.host", "gateway_chat_json"),
    "gateway_chat_text": ("nexustrade.host", "gateway_chat_text"),
    "gateway_fetch_json": ("nexustrade.host", "gateway_fetch_json"),
    "poll_backtest": ("nexustrade.host", "poll_backtest"),
    "poll_backtests": ("nexustrade.host", "poll_backtests"),
    "poll_optimization": ("nexustrade.host", "poll_optimization"),
    "poll_walk_forward": ("nexustrade.host", "poll_walk_forward"),
    "queue_backtest": ("nexustrade.host", "queue_backtest"),
    "queue_backtest_poll": ("nexustrade.host", "queue_backtest_poll"),
    "queue_fetch": ("nexustrade.host", "queue_fetch"),
    "queue_portfolio_job": ("nexustrade.host", "queue_portfolio_job"),
    "queue_portfolio_job_read": ("nexustrade.host", "queue_portfolio_job_read"),
    "queue_search": ("nexustrade.host", "queue_search"),
    "read_result": ("nexustrade.host", "read_result"),
    "read_results": ("nexustrade.host", "read_results"),
    "search": ("nexustrade.host", "search"),
    "submit_backtest": ("nexustrade.host", "submit_backtest"),
    "submit_backtests": ("nexustrade.host", "submit_backtests"),
    "submit_optimization": ("nexustrade.host", "submit_optimization"),
    "submit_walk_forward": ("nexustrade.host", "submit_walk_forward"),
    "write_report": ("nexustrade.report", "write"),
    "write_report_inputs": ("nexustrade.report", "write_inputs"),
    "extract_pdf": ("nexustrade.scanned_table", "extract_pdf"),
    "extract_pdf_markdown": ("nexustrade.scanned_table", "extract_pdf_markdown"),
    "extract_pdfs": ("nexustrade.scanned_table", "extract_pdfs"),
    "extract_rows": ("nexustrade.scanned_table", "extract_rows"),
    "inspect_document": ("nexustrade.inspect_document", "inspect_document"),
    "probe_pdf": ("nexustrade.scanned_table", "probe_pdf"),
    "validate_row": ("nexustrade.signal", "validate_row"),
    "write_rows": ("nexustrade.signal", "write_rows"),
    "align_calendar_to_sessions": (
        "nexustrade.stats",
        "align_calendar_to_sessions",
    ),
    "benjamini_hochberg": ("nexustrade.stats", "benjamini_hochberg"),
    "block_bootstrap_corr": ("nexustrade.stats", "block_bootstrap_corr"),
    "mean_shift_break": ("nexustrade.stats", "mean_shift_break"),
    "newey_west_slope": ("nexustrade.stats", "newey_west_slope"),
    "next_session_frame": ("nexustrade.stats", "next_session_frame"),
    "spec_curve": ("nexustrade.stats", "spec_curve"),
    "LAKE_CATALOG": ("nexustrade.tigris", "LAKE_CATALOG"),
    "connect": ("nexustrade.tigris", "connect"),
    "host_options_lake_enabled": (
        "nexustrade.tigris",
        "host_options_lake_enabled",
    ),
    "read_crypto_daily": ("nexustrade.tigris", "read_crypto_daily"),
    "read_day_shards": ("nexustrade.tigris", "read_day_shards"),
    "read_fetch_bytes": ("nexustrade.tigris", "read_fetch_bytes"),
    "read_fetch_result": ("nexustrade.tigris", "read_fetch_result"),
    "read_ohlc": ("nexustrade.tigris", "read_ohlc"),
    "read_options_daily": ("nexustrade.tigris", "read_options_daily"),
    "read_options_event_windows": (
        "nexustrade.tigris",
        "read_options_event_windows",
    ),
    "read_snapshot": ("nexustrade.tigris", "read_snapshot"),
    "read_year_shards": ("nexustrade.tigris", "read_year_shards"),
}

# Base-install-safe lazy modules are part of the explicit public surface.
# Tigris/stats names remain attribute-accessible but stay out of ``__all__``
# because ``import *`` resolves every name eagerly and would require extras.
_PUBLIC_BASE_LAZY_MODULES = {
    "nexustrade.host",
    "nexustrade.inspect_document",
    "nexustrade.report",
    "nexustrade.scanned_table",
    "nexustrade.signal",
}
__all__.extend(
    sorted(
        name
        for name, (module_name, _attribute) in _LAZY_EXPORTS.items()
        if module_name in _PUBLIC_BASE_LAZY_MODULES
    )
)


def __dir__() -> list[str]:
    return sorted({*__all__, *_LAZY_EXPORTS})


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'nexustrade' has no attribute {name!r}")
    module_name, attribute = target
    try:
        module = import_module(module_name)
    except ImportError as error:
        extra = _OPTIONAL_EXTRA_BY_MODULE.get(module_name, "stats")
        raise AttributeError(
            f"'{name}' comes from {module_name}, whose optional dependencies "
            f"are not installed ({error}). Install them with: "
            f"pip install 'nexustrade[{extra}]'"
        ) from error
    value = module if attribute is None else getattr(module, attribute)
    globals()[name] = value
    return value
