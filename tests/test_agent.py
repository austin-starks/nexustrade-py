"""Agent-run iteration contract. Mirrored by test/agent.test.ts."""

from __future__ import annotations

import unittest
from typing import Any, Mapping

from nexustrade import NexusTradeApiError, NexusTradeClient


class ScriptedTransport:
    """Replays a fixed sequence of /events pages and records mutations."""

    def __init__(self, pages: list[dict[str, Any]], sticky: bool = False) -> None:
        self.pages = pages
        # `sticky` keeps replaying the final page instead of ending the run —
        # how a genuinely stalled agent behaves.
        self.sticky = sticky
        self.last: dict[str, Any] | None = None
        self.calls: list[tuple[str, str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if path == "agents":
            return {"agent": {"id": "agent-1", "status": "initializing"}}
        # The real client appends a query string, so match on the segment.
        if "/events" in path:
            if self.pages:
                self.last = self.pages.pop(0)
            elif not self.sticky:
                self.last = _page([], terminal=True)
            return self.last or _page([], terminal=True)
        if path == "agents/agent-1":
            return {"agent": {"id": "agent-1", "status": "running"}}
        return {"agent": {"id": "agent-1", "status": "action_approved"}}


def _event(identity: str, text: str, digest: str = "d1") -> dict[str, Any]:
    return {"id": identity, "digest": digest, "role": "Assistant", "text": text}


def _page(
    events: list[dict[str, Any]],
    *,
    status: str = "running",
    terminal: bool = False,
    pending: str | None = None,
    needs_input: bool = False,
    cursor: str = "c1",
) -> dict[str, Any]:
    page: dict[str, Any] = {
        "events": events,
        "nextCursor": cursor,
        "hasMore": False,
        "supersededFirst": False,
        "status": "completed" if terminal else status,
        "needsInput": needs_input,
        "terminal": terminal,
    }
    if pending:
        page["pendingApproval"] = {"kind": pending}
    return page


def _client(
    pages: list[dict[str, Any]], sticky: bool = False
) -> tuple[NexusTradeClient, ScriptedTransport]:
    transport = ScriptedTransport(pages, sticky=sticky)
    return NexusTradeClient(transport=transport), transport


class AgentIterationTests(unittest.TestCase):
    def test_yields_events_until_terminal(self) -> None:
        client, transport = _client(
            [
                _page([_event("a", "first")]),
                _page([_event("b", "second")], terminal=True),
            ]
        )
        run = client.create_agent("do a thing", idempotency_key="k1")
        run.poll_interval_seconds = 0

        texts = [event.text for event in run]

        self.assertEqual(texts, ["first", "second"])
        self.assertTrue(run.terminal)
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(run.events), 2)

    def test_does_not_redeliver_an_unchanged_event(self) -> None:
        # The server re-sends rather than risk skipping, so the same id arrives
        # twice. The caller must see it once.
        client, _ = _client(
            [
                _page([_event("a", "first")]),
                _page([_event("a", "first"), _event("b", "second")]),
                _page([], terminal=True),
            ]
        )
        run = client.create_agent("x", idempotency_key="k")
        run.poll_interval_seconds = 0

        self.assertEqual([e.text for e in run], ["first", "second"])

    def test_redelivers_an_edited_event_as_superseding(self) -> None:
        # Same id, new digest: the message was rewritten in place and the caller
        # must see the final text.
        client, _ = _client(
            [
                _page([_event("a", "partial", digest="d1")]),
                _page([_event("a", "final", digest="d2")]),
                _page([], terminal=True),
            ]
        )
        run = client.create_agent("x", idempotency_key="k")
        run.poll_interval_seconds = 0

        events = list(run)
        self.assertEqual([e.text for e in events], ["partial", "final"])
        self.assertFalse(events[0].supersedes)
        self.assertTrue(events[1].supersedes)

    def test_flags_a_pending_approval_on_the_tail_event(self) -> None:
        client, transport = _client(
            [
                _page([_event("a", "history"), _event("b", "buy 100 SPY")],
                      pending="action"),
                _page([_event("c", "done")], terminal=True),
            ]
        )
        run = client.create_agent("x", idempotency_key="k")
        run.poll_interval_seconds = 0

        seen: list[str] = []
        for event in run:
            seen.append(event.text)
            if event.needs_approval:
                self.assertEqual(event.approval_kind, "action")
                self.assertTrue(event.blocked)
                run.approve()

        self.assertEqual(seen, ["history", "buy 100 SPY", "done"])
        # The approval must have been POSTed, not just observed.
        self.assertIn(
            ("POST", "agents/agent-1/approve", {}),
            transport.calls,
        )

    def test_only_the_tail_event_carries_the_blocked_flag(self) -> None:
        client, _ = _client(
            [_page([_event("a", "one"), _event("b", "two")], pending="plan"),
             _page([], terminal=True)]
        )
        run = client.create_agent("x", idempotency_key="k")
        run.poll_interval_seconds = 0

        events = list(run)
        self.assertFalse(events[0].needs_approval)
        self.assertTrue(events[1].needs_approval)

    def test_needs_input_is_surfaced(self) -> None:
        client, transport = _client(
            [_page([_event("a", "which sector?")], needs_input=True),
             _page([], terminal=True)]
        )
        run = client.create_agent("x", idempotency_key="k")
        run.poll_interval_seconds = 0

        for event in run:
            if event.needs_input:
                run.say("semis")

        self.assertIn(
            ("POST", "agents/agent-1/messages", {"content": "semis"}),
            transport.calls,
        )

    def test_a_stalled_run_raises_rather_than_spinning(self) -> None:
        # A caller that ignores an approval must not loop forever in silence.
        client, _ = _client(
            [_page([_event("a", "approve me")], pending="action")],
            sticky=True,
        )
        run = client.create_agent("x", idempotency_key="k")
        run.poll_interval_seconds = 0.01
        run.timeout_seconds = 0.05

        with self.assertRaises(NexusTradeApiError) as raised:
            list(run)
        self.assertEqual(raised.exception.code, "agent_awaiting_input")

    def test_attach_resumes_from_a_cursor(self) -> None:
        client, transport = _client([_page([_event("z", "resumed")], terminal=True)])
        run = client.attach_agent("agent-1", cursor="opaque-cursor")
        run.poll_interval_seconds = 0

        self.assertEqual([e.text for e in run], ["resumed"])
        events_call = next(c for c in transport.calls if "/events" in c[1])
        self.assertIn("cursor=opaque-cursor", events_call[1])


if __name__ == "__main__":
    unittest.main()
