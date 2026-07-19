"""End-to-end wiring example: three agents, three venues, isolated clients.

Requires the environment variables in .env.example. Balances are read on all
three venues; the order-placement lines are left commented so running this
never trades by accident.
"""

import logging
from decimal import Decimal

from trading import Orchestrator, Side
from trading.base import ExchangeClient
from trading.agents import AgentContext
from trading.config import (BinanceUSCredentials, CoinbaseCredentials,
                            RobinhoodCredentials)
from trading.coinbase_client import CoinbaseClient
from trading.binanceus_client import BinanceUSClient
from trading.robinhood_client import RobinhoodClient

logging.basicConfig(level=logging.INFO)


def report_balances(client: ExchangeClient, ctx: AgentContext) -> None:
    ctx.publish({"balances": client.get_balances()})
    # To place a real order, uncomment and adjust:
    # result = client.place_limit_order(
    #     symbol="BTC-USD",           # venue-specific format — see each adapter
    #     side=Side.BUY,
    #     quantity=Decimal("0.001"),
    #     limit_price=Decimal("50000"),
    # )
    # ctx.publish({"order": result})


def main() -> None:
    orch = Orchestrator()
    orch.add_agent("coinbase-1",
                   lambda: CoinbaseClient(CoinbaseCredentials.from_env()),
                   report_balances)
    orch.add_agent("binanceus-1",
                   lambda: BinanceUSClient(BinanceUSCredentials.from_env()),
                   report_balances)
    orch.add_agent("robinhood-1",
                   lambda: RobinhoodClient(RobinhoodCredentials.from_env()),
                   report_balances)

    orch.start()
    orch.stop(timeout=60)

    for agent_id, item in orch.drain_results():
        print(f"[{agent_id}] {item}")


if __name__ == "__main__":
    main()
