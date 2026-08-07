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


class ImporterNotImplementedError(DomainError):
    """An import interface exists but its source format has not been inspected.

    Phase 1 defines the import *contract* only. Parsers are written after the
    real payloads have been captured under ``data/raw/`` and inspected by a
    human. See docs/PHASE_1_STATUS.md.
    """
