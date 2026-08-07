"""Market data acquisition and normalisation.

Phase 1 defines the *shape* of a market observation and the provider interface.
No live feed is wired up: strategies are exercised against fixtures so that
their behaviour is reproducible.
"""

from market_data.models import Bar, MarketSnapshot, Quote
from market_data.providers import InMemoryMarketDataProvider, MarketDataProvider

__all__ = [
    "Bar",
    "InMemoryMarketDataProvider",
    "MarketDataProvider",
    "MarketSnapshot",
    "Quote",
]
