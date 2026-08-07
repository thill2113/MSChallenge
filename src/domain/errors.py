"""Domain-level exceptions.

Each error below corresponds to a documented architectural invariant. They are
raised instead of returning a falsy value so that a violated invariant can never
be silently ignored by a caller.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every error raised by the trading domain."""


class AuthorityViolationError(DomainError):
    """A layer attempted an action outside its granted authority.

    See ADR-001 (deterministic trading authority) and ADR-002 (AI veto-only
    authority).
    """


class RiskBypassError(DomainError):
    """An order was constructed or submitted without a valid risk approval.

    See ADR-001. This is raised when a :class:`~risk.models.RiskDecision` is
    missing, rejected, or does not bind to the exact candidate being executed.
    """


class PromotionAuthorityError(DomainError):
    """A strategy version was promoted without a recorded human approval.

    See ADR-005 (human-controlled production promotion).
    """


class ImmutableEvidenceError(DomainError):
    """An attempt was made to alter raw source evidence.

    See ADR-003 (immutable raw data).
    """


class ValidationGateError(DomainError):
    """The final validator refused an order intent.

    Carries the failed gate codes so an operator sees every blocker at once
    rather than one per retry. See ADR-008.
    """

    def __init__(self, message: str, codes: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.codes = codes


class KillSwitchEngagedError(DomainError):
    """A kill switch covering this order is active.

    No agent, strategy or automated process may clear a kill switch that was
    engaged by a risk control. See ADR-008.
    """


class ControlPlaneError(DomainError):
    """An attempt to change protected configuration without human authority.

    Execution mode, risk ceilings, enabled instruments and broker configuration
    live in the control plane and change only by recorded human approval.
    """


class BrokerError(DomainError):
    """Base class for broker transport failures."""


class BrokerUnavailableError(BrokerError):
    """The venue could not be reached, or reported itself unhealthy."""


class BrokerRejectedError(BrokerError):
    """The venue explicitly refused the order. A definite, terminal answer."""


class BrokerTimeoutError(BrokerError):
    """A request timed out with the outcome unknown.

    This is *not* a failure. The order may be working. Callers must reconcile
    rather than assume nothing happened — re-sending here is how a duplicate
    position gets opened. See ADR-008.
    """


class DuplicateOrderError(DomainError):
    """An order with this idempotency key was already submitted.

    Raised rather than silently returning the prior result so that a caller
    which believes it is placing a new trade is corrected rather than confirmed.
    """


class ImporterNotImplementedError(DomainError):
    """An import interface exists but its source format has not been inspected.

    Phase 1 defines the import *contract* only. Parsers are written after the
    real payloads have been captured under ``data/raw/`` and inspected by a
    human. See docs/PHASE_1_STATUS.md.
    """
