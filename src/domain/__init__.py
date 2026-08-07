"""Shared kernel for the Hybrid Trading System.

This package holds the small set of primitives every bounded context needs:
value types, enumerations, deterministic identifiers and the immutable model
base class. It deliberately depends on nothing else in ``src/`` so that the
dependency graph between contexts stays acyclic (see docs/ARCHITECTURE.md).
"""

from domain.base import AuthoritativeModel, FrozenModel
from domain.enums import (
    AssetClass,
    ExecutionStatus,
    LedgerEventType,
    OrderType,
    PromotionStage,
    ReviewVerdict,
    RiskVerdict,
    Side,
    SourceSystem,
    TimeInForce,
)
from domain.errors import (
    AuthorityViolationError,
    DomainError,
    ImmutableEvidenceError,
    ImporterNotImplementedError,
    PromotionAuthorityError,
    RiskBypassError,
)
from domain.identifiers import DeterministicId
from domain.values import (
    ExactDecimal,
    NonEmptyText,
    Price,
    Quantity,
    Ratio,
    SignedAmount,
    Symbol,
    TimestampUTC,
)

__all__ = [
    "AssetClass",
    "AuthoritativeModel",
    "AuthorityViolationError",
    "DeterministicId",
    "DomainError",
    "ExactDecimal",
    "ExecutionStatus",
    "FrozenModel",
    "ImmutableEvidenceError",
    "ImporterNotImplementedError",
    "LedgerEventType",
    "NonEmptyText",
    "OrderType",
    "Price",
    "PromotionAuthorityError",
    "PromotionStage",
    "Quantity",
    "Ratio",
    "ReviewVerdict",
    "RiskBypassError",
    "RiskVerdict",
    "Side",
    "SignedAmount",
    "SourceSystem",
    "Symbol",
    "TimeInForce",
    "TimestampUTC",
]
