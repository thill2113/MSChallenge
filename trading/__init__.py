"""Multi-agent crypto trading architecture.

Venue adapters (Coinbase Advanced Trade, Binance.US, Robinhood) implement a
common ExchangeClient interface; the Orchestrator runs one isolated client
per agent. See docs/TECHNICAL_SETUP.md for the architecture walkthrough and
docs/INTEGRATION_PROTOCOL.md for the security/operations protocol.
"""

from .base import Balance, ExchangeClient, OrderResult, Side, TokenBucketRateLimiter
from .agents import AgentContext, Orchestrator, TradingAgent

__all__ = [
    "Balance", "ExchangeClient", "OrderResult", "Side",
    "TokenBucketRateLimiter", "AgentContext", "Orchestrator", "TradingAgent",
]
