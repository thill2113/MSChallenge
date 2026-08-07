"""Immutable model base classes and canonical fingerprinting.

Every record that crosses a layer boundary is frozen and closed:

* ``frozen=True`` — a downstream layer physically cannot mutate an upstream
  decision. Attribute assignment raises.
* ``extra="forbid"`` — a downstream layer cannot *smuggle* a field either. This
  is what stops an agent review from carrying a ``stop_loss`` override in a
  field the model never declared (ADR-002).

:meth:`AuthoritativeModel.authoritative_fingerprint` reduces the trading-critical
fields of a record to a single hash. Downstream layers bind to that hash, so
substituting a different candidate for an approved one is detectable rather than
merely discouraged (ADR-001).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """A hashable, immutable, closed record."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        validate_assignment=True,
        populate_by_name=False,
        use_enum_values=False,
    )


def canonical_form(value: Any) -> Any:
    """Reduce a value to a JSON-serialisable form with exactly one spelling.

    ``Decimal("1.50")`` and ``Decimal("1.5")`` are the same amount and must
    therefore produce the same fingerprint.
    """
    if isinstance(value, BaseModel):
        return {
            name: canonical_form(getattr(value, name)) for name in sorted(type(value).model_fields)
        }
    if isinstance(value, Decimal):
        normalized = value.normalize()
        _, _, exponent = normalized.as_tuple()
        if isinstance(exponent, int) and exponent > 0:
            normalized = normalized.quantize(Decimal(1))
        return format(normalized, "f")
    if isinstance(value, Enum):
        return canonical_form(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(k): canonical_form(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [canonical_form(v) for v in value]
        if isinstance(value, (set, frozenset)):
            items.sort(key=json.dumps)
        return items
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(
        f"no canonical form defined for {type(value)!r}; extend domain.base.canonical_form"
    )


class AuthoritativeModel(FrozenModel):
    """A frozen record whose trading-critical fields are hashed and bound.

    Subclasses declare :attr:`AUTHORITATIVE_FIELDS` — the fields that determine
    *what would actually happen in the market*. Commentary, identifiers of
    reviews, and audit timestamps are deliberately excluded so that annotating a
    decision does not change its economic identity.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        unknown = set(cls.AUTHORITATIVE_FIELDS) - set(cls.model_fields)
        if unknown:
            raise TypeError(
                f"{cls.__name__}.AUTHORITATIVE_FIELDS names undeclared fields: {sorted(unknown)}"
            )

    def authoritative_payload(self) -> dict[str, Any]:
        """The canonical, JSON-safe view of this record's authoritative fields."""
        if not self.AUTHORITATIVE_FIELDS:
            raise NotImplementedError(
                f"{type(self).__name__} must declare AUTHORITATIVE_FIELDS to be fingerprinted"
            )
        return {
            name: canonical_form(getattr(self, name)) for name in sorted(self.AUTHORITATIVE_FIELDS)
        }

    def authoritative_fingerprint(self) -> str:
        """SHA-256 over the authoritative payload.

        Two records with the same fingerprint would place the same order. Two
        records with different fingerprints must never be treated as
        interchangeable by the risk or execution layers.
        """
        encoded = json.dumps(
            self.authoritative_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
