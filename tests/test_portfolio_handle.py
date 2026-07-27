"""Portfolio handle — dict duality + save/deploy/backtest wiring."""

from __future__ import annotations

import json
import unittest

from nexustrade.portfolio_handle import Portfolio
from nexustrade import client as client_module


class FakeTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, path, *, body=None, idempotency_key=None):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "idempotency_key": idempotency_key,
            }
        )
        return self.responses.pop(0)


class PortfolioHandleTests(unittest.TestCase):
    def test_dict_duality(self) -> None:
        book = Portfolio(
            {
                "name": "Momentum",
                "initialValue": 10000,
                "strategies": [{"name": "s"}],
            }
        )
        self.assertIsInstance(book, dict)
        self.assertEqual(book["name"], "Momentum")
        self.assertIsNone(book.id)
        self.assertEqual(json.loads(json.dumps(book))["name"], "Momentum")
        self.assertNotIn("id", json.loads(json.dumps(book)))

    def test_save_sets_id_without_leaking_into_body(self) -> None:
        transport = FakeTransport(
            [{"portfolio": {"portfolioId": "chat-1", "portfolioName": "Momentum"}}]
        )
        client = client_module.NexusTradeClient(transport=transport)
        book = Portfolio(
            {"name": "Momentum", "strategies": [{"name": "s"}]},
            client=client,
        )

        book.save(idempotency_key="mom-v1")

        self.assertEqual(book.id, "chat-1")
        self.assertEqual(transport.calls[0]["body"]["name"], "Momentum")
        self.assertNotIn("id", transport.calls[0]["body"])
        self.assertNotIn("portfolioId", transport.calls[0]["body"])

    def test_backtest_uses_portfolio_id_once_saved(self) -> None:
        transport = FakeTransport(
            [
                {
                    "operations": [
                        {"id": "bt-1", "kind": "backtest", "status": "running"}
                    ]
                }
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)
        book = Portfolio(
            {"name": "Momentum", "strategies": [{"name": "s"}]},
            id="chat-1",
            client=client,
        )

        book.backtest(
            start_date="2024-01-01",
            end_date="2024-12-31",
            idempotency_key="bt-v1",
        )

        body = transport.calls[0]["body"]["backtests"][0]
        self.assertEqual(body["portfolioId"], "chat-1")
        self.assertNotIn("portfolio", body)

    def test_deploy_returns_distinct_id(self) -> None:
        transport = FakeTransport(
            [
                {
                    "deployment": {
                        "portfolioId": "paper-9",
                        "chatPortfolioId": "chat-1",
                        "name": "Momentum",
                        "outcome": "minted",
                    }
                }
            ]
        )
        client = client_module.NexusTradeClient(transport=transport)
        book = Portfolio({"name": "Momentum"}, id="chat-1", client=client)

        result = book.deploy(frequency="Constant")

        self.assertEqual(result.portfolio_id, "paper-9")
        self.assertEqual(result.chat_portfolio_id, "chat-1")
        self.assertEqual(book.id, "chat-1")
        self.assertEqual(
            transport.calls[0]["path"],
            "portfolios/chat-1/deploy",
        )


if __name__ == "__main__":
    unittest.main()
