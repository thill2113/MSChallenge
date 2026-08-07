"""In-process simulated broker.

The reference implementation of :class:`~brokers.base.Broker`. It is not a stub:
it maintains order state, produces fills, honours idempotency, and can be told
to time out or reject on demand — because the failure paths in ADR-008 need
something to exercise them, and a broker that only ever succeeds proves nothing
about how the engine behaves when one does not.
"""

from brokers.simulated.broker import FailureMode, SimulatedBroker

__all__ = ["FailureMode", "SimulatedBroker"]
