"""Agent runs as a blocking iterator.

Every other NexusTrade job is fire-and-poll: submit, wait, read a terminal
result. Agents are not, because three of their states —
``pending_plan_approval``, ``pending_action_approval`` and
``awaiting_user_input`` — are ones the run cannot leave on its own. A caller
that only polled would start a run that stalls forever waiting for an approval
nobody is present to give, and bill for the wait.

So the caller *is* the approver::

    run = nt.create_agent("Find momentum names in the S&P 500",
                          idempotency_key="momentum-scan-v1")
    for event in run:
        print(event.text)
        if event.needs_approval:
            run.approve()

See designs/2026-07-26-sdk-agent-runs.md.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from nexustrade.client import (
    NexusTradeApiError,
    _DEFAULT_POLL_INTERVAL_SECONDS,
    _DEFAULT_POLL_TIMEOUT_SECONDS,
    _MAX_POLL_INTERVAL_SECONDS,
    _NO_HTTP_STATUS,
    _POLL_BACKOFF_FACTOR,
)

_DEFAULT_EVENT_LIMIT = 50


@dataclass(frozen=True)
class AgentEvent:
    """One message from the run."""

    id: str
    digest: str
    role: str
    text: str
    data: Any = None
    #: The run is blocked until ``approve()`` or ``reject()`` is called.
    needs_approval: bool = False
    #: "plan" or "action" when ``needs_approval``; otherwise None.
    approval_kind: str | None = None
    #: The run is blocked until ``say(...)`` is called.
    needs_input: bool = False
    #: This event replaces an earlier one with the same ``id``.
    supersedes: bool = False

    @property
    def blocked(self) -> bool:
        """True when the run cannot advance until the caller answers."""
        return self.needs_approval or self.needs_input


@dataclass
class AgentRun:
    """A live agent run. Iterate it; answer it when it asks."""

    id: str
    _client: Any = field(repr=False)
    status: str = "initializing"
    terminal: bool = False
    #: Every event yielded so far, in order.
    events: list[AgentEvent] = field(default_factory=list, repr=False)
    _cursor: str | None = field(default=None, repr=False)
    _seen: dict[str, str] = field(default_factory=dict, repr=False)
    #: Give up waiting on a blocked run after this long. The run keeps going.
    timeout_seconds: float = _DEFAULT_POLL_TIMEOUT_SECONDS
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS
    max_poll_interval_seconds: float = _MAX_POLL_INTERVAL_SECONDS
    event_limit: int = _DEFAULT_EVENT_LIMIT

    # -- iteration ---------------------------------------------------------

    def __iter__(self) -> Iterator[AgentEvent]:
        """Yield events until the run is terminal.

        Blocks between polls on the same deterministic backoff the operation
        waiter uses. When the run reaches a state only the caller can clear, the
        blocking event is yielded and iteration then waits for the state to
        move — the caller is expected to answer from inside the loop body.
        """
        interval = min(self.poll_interval_seconds, self.max_poll_interval_seconds)
        deadline = time.monotonic() + self.timeout_seconds
        blocked_status: str | None = None

        while True:
            page = self._fetch()
            fresh = self._absorb(page)
            for event in fresh:
                yield event

            if self.terminal:
                return

            if fresh:
                # Progress resets both the backoff and the stall deadline: a run
                # that is still talking is not stuck, however long it runs.
                interval = min(
                    self.poll_interval_seconds,
                    self.max_poll_interval_seconds,
                )
                deadline = time.monotonic() + self.timeout_seconds

            waiting_on_caller = bool(page.get("pendingApproval")) or bool(
                page.get("needsInput")
            )
            if waiting_on_caller:
                if blocked_status is None:
                    blocked_status = self.status
                elif self.status != blocked_status:
                    # The caller answered; resume at full speed.
                    blocked_status = None
                    interval = min(
                        self.poll_interval_seconds,
                        self.max_poll_interval_seconds,
                    )
                    deadline = time.monotonic() + self.timeout_seconds
            else:
                blocked_status = None

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NexusTradeApiError(
                    _NO_HTTP_STATUS,
                    "agent_awaiting_input" if waiting_on_caller else "agent_timeout",
                    (
                        f"Agent {self.id} was still '{self.status}' after "
                        f"{self.timeout_seconds:g}s. It is still running — "
                        "answer it, or attach again with the same id."
                    ),
                )
            if interval > 0:
                time.sleep(min(interval, remaining))
            interval = min(
                interval * _POLL_BACKOFF_FACTOR,
                self.max_poll_interval_seconds,
            )

    def _fetch(self) -> dict[str, Any]:
        path = f"agents/{urllib.parse.quote(self.id, safe='')}/events"
        query: dict[str, str] = {"limit": str(self.event_limit)}
        if self._cursor:
            query["cursor"] = self._cursor
        response = self._client._transport.request(
            "GET",
            f"{path}?{urllib.parse.urlencode(query)}",
        )
        return response

    def _absorb(self, page: Mapping[str, Any]) -> list[AgentEvent]:
        """Fold a page into run state, dropping anything already delivered.

        The server re-sends rather than risk skipping, so the same id can arrive
        twice. Deduping here is what turns that guarantee into an exactly-once
        stream for the caller.
        """
        self.status = str(page.get("status") or self.status)
        self.terminal = bool(page.get("terminal"))
        cursor = page.get("nextCursor")
        if isinstance(cursor, str) and cursor:
            self._cursor = cursor

        approval = page.get("pendingApproval")
        approval_kind = (
            str(approval.get("kind")) if isinstance(approval, Mapping) else None
        )
        needs_input = bool(page.get("needsInput"))

        raw_events = page.get("events")
        if not isinstance(raw_events, list):
            return []

        fresh: list[AgentEvent] = []
        for index, raw in enumerate(raw_events):
            if not isinstance(raw, Mapping):
                continue
            identity = str(raw.get("id") or "")
            digest = str(raw.get("digest") or "")
            if not identity:
                continue
            previous = self._seen.get(identity)
            if previous == digest:
                continue  # already delivered, unchanged
            self._seen[identity] = digest
            is_last = index == len(raw_events) - 1
            event = AgentEvent(
                id=identity,
                digest=digest,
                role=str(raw.get("role") or "Assistant"),
                text=str(raw.get("text") or ""),
                data=raw.get("data"),
                # Only the final event of a page can be the one the run is
                # blocked on — the state applies to the tail, not the history.
                needs_approval=bool(approval_kind) and is_last,
                approval_kind=approval_kind if is_last else None,
                needs_input=needs_input and is_last,
                supersedes=previous is not None,
            )
            fresh.append(event)
            self.events.append(event)
        return fresh

    # -- answering ---------------------------------------------------------

    def approve(self) -> str:
        """Approve a pending plan or action. Needs the `trade` scope."""
        return self._post("approve")

    def reject(self) -> str:
        """Reject a pending plan or action."""
        return self._post("reject")

    def stop(self) -> str:
        """Stop the run."""
        return self._post("stop")

    def say(self, content: str) -> str:
        """Append a user message — a follow-up or a course correction."""
        return self._post("messages", body={"content": content})

    def refresh(self) -> str:
        """Re-read status without consuming events."""
        response = self._client.get_agent(self.id)
        self.status = str(response.get("status") or self.status)
        self.terminal = bool(response.get("terminal"))
        return self.status

    def _post(self, action: str, body: Mapping[str, Any] | None = None) -> str:
        response = self._client._transport.request(
            "POST",
            f"agents/{urllib.parse.quote(self.id, safe='')}/{action}",
            body=body or {},
            idempotency_key=f"{self.id}:{action}:{len(self.events)}",
        )
        agent = response.get("agent")
        if isinstance(agent, Mapping):
            self.status = str(agent.get("status") or self.status)
        return self.status


__all__ = ["AgentEvent", "AgentRun"]
