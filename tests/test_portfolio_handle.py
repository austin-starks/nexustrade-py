"""Portfolio handle — dict duality + save/deploy/backtest wiring."""

from __future__ import annotations

import json
import unittest

from nexustrade.portfolio_handle import Portfolio
from nexustrade.portfolio import portfolio as build_portfolio
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

    def test_builder_emits_cash_and_buying_power(self) -> None:
        book = build_portfolio("Seed", [{"name": "s"}], initial_value=100000)
        self.assertEqual(book["initialValue"], 100000)
        self.assertEqual(book["cash"], 100000)
        self.assertEqual(book["buyingPower"], 100000)

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

    def test_fetched_policy_is_readable_but_never_authored(self) -> None:
        policy = {
            "schemaVersion": 2,
            "revision": 4,
            "stockEligibility": {
                "minimumMarketCapUsd": 500_000_000,
                "maximumMarketCapUsd": None,
                "industryFilter": {
                    "mode": "INCLUDE_ONLY",
                    "match": "ALL",
                    "industries": ["artificialIntelligence", "biotechnology"],
                },
                "missingMarketCapBehavior": "EXCLUDE",
                "missingIndustryBehavior": "EXCLUDE_WHEN_FILTER_SET",
                "appliesTo": "DYNAMIC_STOCK_UNIVERSES",
            },
            "automatedApproval": {
                "enabled": False,
                "maxAutomatedTradesPerDay": 2,
                "countingUnit": "TRADE_ACTION",
                "dailyWindow": "AMERICA_NEW_YORK_CALENDAR_DAY",
            },
        }
        transport = FakeTransport(
            [{"portfolio": {"portfolioId": "chat-1", "portfolioName": "Policy"}}]
        )
        client = client_module.NexusTradeClient(transport=transport)
        book = Portfolio(
            {"name": "Policy", "strategies": [], "policy": policy},
            client=client,
        )

        self.assertEqual(book.policy, policy)
        book.save(idempotency_key="policy-v1")
        self.assertNotIn("policy", transport.calls[0]["body"])

    def test_fetched_policy_can_keep_names_without_a_market_cap(self) -> None:
        # The politician copy bots: no floor, and ETFs and unsized filers kept.
        policy = {
            "schemaVersion": 2,
            "revision": 1,
            "stockEligibility": {
                "minimumMarketCapUsd": 0,
                "maximumMarketCapUsd": None,
                "industryFilter": {"mode": "ALL", "match": "ANY", "industries": []},
                "missingMarketCapBehavior": "INCLUDE",
                "missingIndustryBehavior": "EXCLUDE_WHEN_FILTER_SET",
                "appliesTo": "DYNAMIC_STOCK_UNIVERSES",
            },
            "automatedApproval": {
                "enabled": False,
                "maxAutomatedTradesPerDay": 2,
                "countingUnit": "TRADE_ACTION",
                "dailyWindow": "AMERICA_NEW_YORK_CALENDAR_DAY",
            },
        }
        book = Portfolio({"name": "Copy Nancy Pelosi", "strategies": [], "policy": policy})

        self.assertEqual(book.policy, policy)
        self.assertEqual(book.policy["stockEligibility"]["missingMarketCapBehavior"], "INCLUDE")

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
