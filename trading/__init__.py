"""Multi-agent crypto trading architecture.

Venue adapters (Coinbase Advanced Trade, Binance.US, Robinhood) implement a
common ExchangeClient interface; the Orchestrator runs one isolated client
per agent. See docs/TECHNICAL_SETUP.md for the architecture walkthrough and
docs/INTEGRATION_PROTOCOL.md for the security/operations protocol.
"""

from .agents import AgentContext, Orchestrator, TradingAgent
from .base import Balance, ExchangeClient, OrderResult, Side, TokenBucketRateLimiter

__all__ = [
    "Balance", "ExchangeClient", "OrderResult", "Side",
    "TokenBucketRateLimiter", "AgentContext", "Orchestrator", "TradingAgent",
]
