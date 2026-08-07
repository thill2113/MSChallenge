"""Broker abstraction.

Nothing above this package knows what venue it is talking to. Strategies, risk,
portfolio and agents operate entirely on internal domain models; a
:class:`~brokers.base.Broker` adapter translates those into one venue's dialect
and translates the response back.

Adapters translate. They do not reinterpret. See ADR-007.
"""

from brokers.base import (
    AccountState,
    Broker,
    BrokerHealth,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
    Fill,
    select_protection_style,
)

__all__ = [
    "AccountState",
    "Broker",
    "BrokerHealth",
    "BrokerOrder",
    "BrokerOrderRequest",
    "BrokerPosition",
    "Fill",
    "select_protection_style",
]
