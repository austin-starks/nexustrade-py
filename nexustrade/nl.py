"""Natural-language screening: ``import nexustrade as nt`` then ``nt.nl.screen_stocks(...)``.

Submit/poll/cancel live on ``NexusTradeClient``. This is the ergonomic layer:
one blocking call that returns a result object instead of a raw operation dict.

The generated SQL always comes back. It is the audit trail — an NL result
without it is a number nobody can re-derive, and a grader re-running the exact
statement is the whole reason to keep it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from nexustrade.client import NexusTradeClient

__all__ = ["NlScreen", "NlScreenFailed", "screen_stocks"]


class NlScreenFailed(RuntimeError):
    """A screen that spent its retry budget without producing usable SQL.

    Carries the last SQL when there was one — a rejected query is the most
    useful thing to read, so it is never swallowed.
    """

    def __init__(self, message: str, sql: str | None = None) -> None:
        super().__init__(message)
        self.sql = sql


@dataclass
class NlScreen:
    """A finished screen.

    ``outcome`` is what to branch on, not truthiness of ``rows``: EMPTY and
    CLARIFICATION both have no rows and mean completely different things.

    ``used_fallback_tables`` is a degradation signal, not a detail: the screen
    narrows the catalog with a table-selector round first, and when that round
    fails it falls back to the whole stored table index. The SQL that follows is
    then written against a much broader context and can silently read the wrong
    table, so a caller checking provenance has to be able to see it.
    """

    id: str
    outcome: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    sql: str | None = None
    engine: str | None = None
    catalog_version: str | None = None
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
        """Rows as a DataFrame. Requires ``nexustrade[stats]`` or ``[lake]``."""
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
            sql=result.get("sql"),
            engine=result.get("engine"),
            catalog_version=result.get("catalogVersion"),
            tables=list(result.get("tables") or []),
            used_fallback_tables=bool(result.get("usedFallbackTables")),
            as_of_date=result.get("asOfDate"),
            clarification=result.get("clarification"),
            question=result.get("question"),
        )


def screen_stocks(
    question: str,
    *,
    client: NexusTradeClient | None = None,
    return_query: bool = True,
    **wait_options: Any,
) -> NlScreen:
    """Screen stocks from a plain-language question. Blocks until terminal.

    Raises ``NlScreenFailed`` only when generation itself failed. An empty
    result and a clarification are returned, not raised — they are answers the
    caller has to see, and raising would push them into an except block that
    cannot inspect them.
    """
    resolved = client or NexusTradeClient.from_environment()
    started = resolved.create_nl_screen(question, return_query=return_query)
    operation = resolved.wait_for_nl_screen(
        str(started["id"]),
        raise_on_failure=False,
        **wait_options,
    )
    screen = NlScreen._from_operation(operation)
    if operation.get("status") == "failed":
        error = operation.get("error") or {}
        raise NlScreenFailed(
            str(error.get("message") or "Natural-language screen failed."),
            sql=screen.sql,
        )
    return screen
