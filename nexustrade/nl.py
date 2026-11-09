"""Natural-language screening: ``import nexustrade as nt`` then ``nt.nl.screen_stocks(...)``.

Submit/poll/cancel live on ``NexusTradeClient``. This is the ergonomic layer:
one blocking call that returns a result object instead of a raw operation dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, TypeVar

from nexustrade.client import NexusTradeClient

__all__ = ["NlScreen", "NlScreenFailed", "screen_stocks"]

# A runtime may supply subclasses of the result and failure types through the
# package's extension registry (see ``nexustrade/__init__.py``). Without one the
# public types are used.
_SCREEN_TYPE_EXPORT = "_nl_screen_type"
_FAILED_TYPE_EXPORT = "_nl_screen_failed_type"


class NlScreenFailed(RuntimeError):
    """A screen that spent its retry budget without producing a usable answer."""

    @classmethod
    def _from_operation(
        cls, message: str, operation: Mapping[str, Any]
    ) -> "NlScreenFailed":
        return cls(message)


@dataclass
class NlScreen:
    """A finished screen.

    ``outcome`` is what to branch on, not truthiness of ``rows``: EMPTY and
    CLARIFICATION both have no rows and mean completely different things.

    ``used_fallback_tables`` is a degradation signal, not a detail: the screen
    narrows its data sources first, and when that step fails it falls back to
    every source. The answer is then built from a much broader context and can
    read the wrong table, so a caller checking provenance has to be able to see
    it.
    """

    id: str
    outcome: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    tables: list[str] = field(default_factory=list)
    used_fallback_tables: bool = False
    as_of_date: str | None = None
    clarification: str | None = None
    question: str | None = None

    @property
    def is_empty(self) -> bool:
        """Every filter ran and nothing cleared them all. An answer, not a fault."""
        return self.outcome == "EMPTY"

    @property
    def needs_clarification(self) -> bool:
        """The question was ambiguous. Retrying rewrites the same ambiguity."""
        return self.outcome == "CLARIFICATION"

    def to_pandas(self) -> Any:
        """Rows as a DataFrame. Requires ``nexustrade[stats]``."""
        try:
            import pandas
        except ModuleNotFoundError as error:  # pragma: no cover - import guard
            raise ModuleNotFoundError(
                "to_pandas() needs pandas: pip install 'nexustrade[stats]'"
            ) from error
        return pandas.DataFrame(self.rows)

    @classmethod
    def _from_operation(cls, operation: Mapping[str, Any]) -> "NlScreen":
        result = operation.get("result") or {}
        return cls(
            id=str(operation.get("id", "")),
            outcome=str(result.get("outcome") or "GENERATION_FAILED"),
            rows=list(result.get("rows") or []),
            row_count=int(result.get("rowCount") or 0),
            tables=list(result.get("tables") or []),
            used_fallback_tables=bool(result.get("usedFallbackTables")),
            as_of_date=result.get("asOfDate"),
            clarification=result.get("clarification"),
            question=result.get("question"),
        )


_Resolved = TypeVar("_Resolved", bound=type)


def _result_type(export: str, default: _Resolved) -> _Resolved:
    import nexustrade

    if export not in nexustrade._extension_exports():
        return default
    resolved = nexustrade._resolve_export(export)
    if not (isinstance(resolved, type) and issubclass(resolved, default)):
        raise TypeError(f"{export} must be a subclass of {default.__name__}.")
    return resolved


def screen_stocks(
    question: str,
    *,
    client: NexusTradeClient | None = None,
    **wait_options: Any,
) -> NlScreen:
    """Screen stocks from a plain-language question. Blocks until terminal.

    Raises ``NlScreenFailed`` only when generation itself failed. An empty
    result and a clarification are returned, not raised — they are answers the
    caller has to see, and raising would push them into an except block that
    cannot inspect them.
    """
    resolved = client or NexusTradeClient.from_environment()
    started = resolved.create_nl_screen(question)
    operation = resolved.wait_for_nl_screen(
        str(started["id"]),
        raise_on_failure=False,
        **wait_options,
    )
    screen = _result_type(_SCREEN_TYPE_EXPORT, NlScreen)._from_operation(operation)
    if operation.get("status") == "failed":
        error = operation.get("error") or {}
        raise _result_type(_FAILED_TYPE_EXPORT, NlScreenFailed)._from_operation(
            str(error.get("message") or "Natural-language screen failed."),
            operation,
        )
    return screen
