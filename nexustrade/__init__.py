"""NexusTrade SDK.

The authoring/API surface has no third-party runtime dependencies. Analytics,
lake, and compute-host helpers live in submodules and are resolved lazily, so
``pip install nexustrade`` remains useful outside the compute image.

Modules named in ``_SANDBOX_ONLY_MODULES`` ship only inside the NexusTrade
compute sandbox, which overlays them into this package at image build time.
``nexustrade.lake`` is published but needs the ``[lake]`` extra.
"""

from importlib import import_module
from importlib.util import find_spec
from typing import Any

from nexustrade import portfolio as _portfolio
from nexustrade.client import (
    HttpTransport,
    NexusTradeApiError,
    NexusTradeClient,
    create_portfolio,
    wait_for_operation,
)
from nexustrade.portfolio import *

__all__ = [
    *_portfolio.__all__,
    "HttpTransport",
    "NexusTradeApiError",
    "NexusTradeClient",
    "create_portfolio",
    "wait_for_operation",
]

# Present only inside the compute sandbox. Naming them keeps the historical
# top-level imports working there without making the lake/host stack a
# dependency of the published package.
_SANDBOX_ONLY_MODULES = {
    "nexustrade.host",
    "nexustrade.report",
    "nexustrade.scanned_table",
    "nexustrade.signal",
    "nexustrade.tigris",
}

# Preserve the historical top-level compute imports without making optional
# analytics/lake dependencies a requirement for ordinary SDK clients. Resolved
# on attribute access only — see the note above ``__dir__``.
# Which extra installs each optional module. `nt.lake` needs duckdb/pyarrow,
# `nt.stats` needs numpy/pandas — naming the wrong one in the error sends people
# to install the wrong thing.
_OPTIONAL_EXTRA_BY_MODULE = {
    "nexustrade.lake": "lake",
    "nexustrade.stats": "stats",
}

_LAZY_EXPORTS = {
    # `(module, None)` exposes the SUBMODULE itself, so `nt.lake.sql(...)`
    # resolves lazily without making duckdb a base-install dependency.
    "lake": ("nexustrade.lake", None),
    "stats": ("nexustrade.stats", None),
    "flush_requests": ("nexustrade.host", "flush_requests"),
    "gateway_chat": ("nexustrade.host", "gateway_chat"),
    "gateway_chat_json": ("nexustrade.host", "gateway_chat_json"),
    "gateway_chat_text": ("nexustrade.host", "gateway_chat_text"),
    "gateway_fetch_json": ("nexustrade.host", "gateway_fetch_json"),
    "options_lake": ("nexustrade.host", "options_lake"),
    "poll_backtest": ("nexustrade.host", "poll_backtest"),
    "poll_backtests": ("nexustrade.host", "poll_backtests"),
    "poll_optimization": ("nexustrade.host", "poll_optimization"),
    "poll_walk_forward": ("nexustrade.host", "poll_walk_forward"),
    "queue_backtest": ("nexustrade.host", "queue_backtest"),
    "queue_backtest_poll": ("nexustrade.host", "queue_backtest_poll"),
    "queue_fetch": ("nexustrade.host", "queue_fetch"),
    "queue_options_lake": ("nexustrade.host", "queue_options_lake"),
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

# ``__all__`` deliberately excludes ``_LAZY_EXPORTS``. ``import *`` resolves
# every name in ``__all__`` eagerly, so listing sandbox-only and [stats]-only
# helpers there would make ``from nexustrade import *`` raise on an ordinary
# ``pip install nexustrade``. They stay reachable by attribute access
# (``nt.search``) and by ``from nexustrade import search``, which is how the
# compute sandbox has always used them — ``__getattr__`` serves both.


def _module_is_present(module_name: str) -> bool:
    """True if the module is importable-by-location (does not execute it)."""
    try:
        return find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


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
        # Distinguish "the module is not shipped here" from "it is shipped but
        # its dependencies are missing". Inside the sandbox the overlay IS
        # present, so a duckdb/pandas failure must not be reported as absence.
        if module_name in _SANDBOX_ONLY_MODULES and not _module_is_present(
            module_name
        ):
            raise AttributeError(
                f"'{name}' comes from {module_name}, which exists only inside "
                "the NexusTrade compute sandbox. The published SDK exposes "
                "NexusTradeClient and the portfolio authoring builders."
            ) from error
        extra = _OPTIONAL_EXTRA_BY_MODULE.get(module_name, "stats")
        raise AttributeError(
            f"'{name}' comes from {module_name}, whose optional dependencies "
            f"are not installed ({error}). Install them with: "
            f"pip install 'nexustrade[{extra}]'"
        ) from error
    value = module if attribute is None else getattr(module, attribute)
    globals()[name] = value
    return value
