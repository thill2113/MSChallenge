"""Execution layer — transmits, never decides.

Boundary contract (ADR-001):

* **Accepts** an approved, immutable :class:`~execution.models.OrderIntent` and
  nothing else.
* **Produces** an :class:`~execution.models.ExecutionResult`.
* **Never** computes, rounds, resizes or re-prices anything. Every parameter is
  copied verbatim from the candidate the risk engine approved.

Live order placement is not implemented in Phase 0/1.
"""

from execution.gateway import ExecutionGateway, ExecutionMode, SimulatedBroker
from execution.models import ExecutionResult, OrderIntent
from execution.protocols import BrokerAdapter

__all__ = [
    "BrokerAdapter",
    "ExecutionGateway",
    "ExecutionMode",
    "ExecutionResult",
    "OrderIntent",
    "SimulatedBroker",
]
