"""Execution layer — validates and transmits, never decides.

Boundary contract (ADR-001, ADR-008):

* **Accepts** an approved, immutable :class:`~execution.models.OrderIntent` and
  nothing else.
* **Validates** it against every hard gate immediately before submission
  (:class:`~execution.validator.FinalValidator`). There is no override.
* **Translates** it for the configured broker without recomputing any price or
  size.
* **Produces** an :class:`~execution.models.ExecutionResult`.

Per-trade human confirmation is deliberately absent: human authority moved to
the control plane (:mod:`control_plane`), where it bounds what an approved
strategy may do rather than approving each thing it does.
"""

from execution.engine import ExecutionEngine
from execution.killswitch import (
    KillSwitch,
    KillSwitchRegistry,
    KillSwitchTrigger,
)
from execution.models import ExecutionResult, OrderIntent, assert_authorized
from execution.validator import (
    FinalValidator,
    GateFailure,
    RejectionCode,
    ValidationOutcome,
    ValidationRequest,
)

__all__ = [
    "ExecutionEngine",
    "ExecutionResult",
    "FinalValidator",
    "GateFailure",
    "KillSwitch",
    "KillSwitchRegistry",
    "KillSwitchTrigger",
    "OrderIntent",
    "RejectionCode",
    "ValidationOutcome",
    "ValidationRequest",
    "assert_authorized",
]
