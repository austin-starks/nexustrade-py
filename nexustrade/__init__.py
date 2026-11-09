"""NexusTrade SDK.

The authoring/API surface has no third-party runtime dependencies. Analytics,
document, and compute-host helpers live in public submodules and are resolved
lazily, so ``pip install nexustrade`` remains useful without extras. PDF
inspection/OCR needs ``[documents]``; statistics need ``[stats]``.

``# sandbox-prune:begin/end <region>`` comments mark code that a restricted
runtime strips from its copy of this package. They have no effect on an
ordinary install: everything between them is live code here. Keep each pair
balanced when editing, and put new agent entry points inside it.
"""

from importlib import import_module
from typing import Any

from nexustrade import portfolio as _portfolio
# sandbox-prune:begin agent-surface
from nexustrade.agent import AgentEvent, AgentRun
# sandbox-prune:end agent-surface
from nexustrade.client import (
    AddStrategiesOperation,
    BacktestCollateralStatistics,
    HttpTransport,
    NexusTradeApiError,
    NexusTradeClient,
    NexusTradeWorkspaceSessionExpiredError,
    PortfolioEditOperation,
    UpdatePortfolioOperation,
    AuthoredPortfolioPatch,
    PortfolioRenameOperation,
    RemoveStrategiesOperation,
    ReplaceStrategiesOperation,
    ReplaceStrategyByIdOperation,
    ReplaceStrategyByNameOperation,
    create_custom_indicator,
    create_portfolio,
    wait_for_operation,
)
from nexustrade.portfolio import *
from nexustrade.portfolio_handle import (
    DeployResult,
    Portfolio,
    PortfolioAutomatedApproval,
    PortfolioIndustryFilter,
    PortfolioList,
    PortfolioPolicy,
    PortfolioStockEligibility,
)

__all__ = [
    *_portfolio.__all__,
    # sandbox-prune:begin agent-surface
    "AgentEvent",
    "AgentRun",
    # sandbox-prune:end agent-surface
    "AddStrategiesOperation",
    "BacktestCollateralStatistics",
    "DeployResult",
    "HttpTransport",
    "NexusTradeApiError",
    "NexusTradeClient",
    "NexusTradeWorkspaceSessionExpiredError",
    "Portfolio",
    "PortfolioAutomatedApproval",
    "PortfolioEditOperation",
    "UpdatePortfolioOperation",
    "AuthoredPortfolioPatch",
    "PortfolioIndustryFilter",
    "PortfolioList",
    "PortfolioPolicy",
    "PortfolioRenameOperation",
    "PortfolioStockEligibility",
    "RemoveStrategiesOperation",
    "ReplaceStrategiesOperation",
    "ReplaceStrategyByIdOperation",
    "ReplaceStrategyByNameOperation",
    "create_custom_indicator",
    "create_portfolio",
    "wait_for_operation",
]

# Preserve top-level helper imports without making optional analytics
# dependencies a requirement for ordinary SDK clients. Resolved on attribute
# access only.
# Which extra installs each optional module, so the error names the right one.
_OPTIONAL_EXTRA_BY_MODULE = {
    "nexustrade.inspect_document": "documents",
    "nexustrade.scanned_table": "documents",
    "nexustrade.stats": "stats",
}

_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    # `(module, None)` exposes the SUBMODULE itself.
    # Pure accounting and valuation arithmetic with no optional dependencies.
    "finance": ("nexustrade.finance", None),
    "nl": ("nexustrade.nl", None),
    "sec": ("nexustrade.sec", None),
    "research": ("nexustrade.research", None),
    "host": ("nexustrade.host", None),
    "report": ("nexustrade.report", None),
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
    "queue_browse": ("nexustrade.host", "queue_browse"),
    "browse": ("nexustrade.host", "browse"),
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
    "extract_web_pages": ("nexustrade.scanned_table", "extract_web_pages"),
    "prepare_web_pages": ("nexustrade.scanned_table", "prepare_web_pages"),
    "read_source_tables": ("nexustrade.source_tables", "read_source_tables"),
    "select_source_table_cell": ("nexustrade.source_tables", "select_source_table_cell"),
    "audit_inclusions": ("nexustrade.semantic", "audit_inclusions"),
    "derive_rows": ("nexustrade.semantic", "derive_rows"),
    "verify_semantic_citations": (
        "nexustrade.semantic",
        "verify_semantic_citations",
    ),
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
}

# Base-install-safe lazy modules are part of the explicit public surface.
# Stats names remain attribute-accessible but stay out of ``__all__`` because
# ``import *`` resolves every name eagerly and would require extras.
_PUBLIC_BASE_LAZY_MODULES = {
    "nexustrade.finance",
    "nexustrade.host",
    "nexustrade.inspect_document",
    "nexustrade.report",
    "nexustrade.sec",
    "nexustrade.scanned_table",
    "nexustrade.semantic",
    "nexustrade.signal",
}
__all__.extend(
    sorted(
        name
        for name, (module_name, _attribute) in _LAZY_EXPORTS.items()
        if module_name in _PUBLIC_BASE_LAZY_MODULES
    )
)


# A runtime that ships extra modules inside this package may add top-level
# names by providing ``nexustrade._extensions`` with an ``EXPORTS`` mapping of
# the same shape as ``_LAZY_EXPORTS``. An ordinary install has none.
_EXTENSIONS_MODULE = "nexustrade._extensions"
_extension_exports_cache: dict[str, tuple[str, str | None]] | None = None


def _extension_exports() -> dict[str, tuple[str, str | None]]:
    global _extension_exports_cache
    if _extension_exports_cache is None:
        try:
            module = import_module(_EXTENSIONS_MODULE)
        except ModuleNotFoundError as error:
            if error.name != _EXTENSIONS_MODULE:
                raise
            _extension_exports_cache = {}
        else:
            _extension_exports_cache = {
                name: target
                for name, target in dict(getattr(module, "EXPORTS", {})).items()
                if name not in _LAZY_EXPORTS
            }
    return _extension_exports_cache


def __dir__() -> list[str]:
    return sorted({*__all__, *_LAZY_EXPORTS, *_extension_exports()})


def _resolve_export(name: str) -> Any:
    """Resolve a lazy top-level name from its module, without caching it."""
    if name.startswith("__"):
        raise AttributeError(f"module 'nexustrade' has no attribute {name!r}")
    target = _LAZY_EXPORTS.get(name)
    extension = target is None
    if extension:
        target = _extension_exports().get(name)
    if target is None:
        raise AttributeError(f"module 'nexustrade' has no attribute {name!r}")
    module_name, attribute = target
    try:
        module = import_module(module_name)
    except ImportError as error:
        if extension:
            raise AttributeError(
                f"'{name}' comes from {module_name}, which could not be "
                f"imported ({error})."
            ) from error
        extra = _OPTIONAL_EXTRA_BY_MODULE.get(module_name, "stats")
        raise AttributeError(
            f"'{name}' comes from {module_name}, whose optional dependencies "
            f"are not installed ({error}). Install them with: "
            f"pip install 'nexustrade[{extra}]'"
        ) from error
    return module if attribute is None else getattr(module, attribute)


def __getattr__(name: str) -> Any:
    value = _resolve_export(name)
    globals()[name] = value
    return value
