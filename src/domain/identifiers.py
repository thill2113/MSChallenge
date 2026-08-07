"""Deterministic identifiers.

Domain models never mint a random id and never read the wall clock. Identity is
*derived* from the inputs that produced the record, so replaying the same inputs
produces byte-identical records — which is what makes the determinism tests in
``tests/property/test_strategy_determinism.py`` meaningful rather than
tautological.
"""

from __future__ import annotations

import uuid
from typing import Final

TRADING_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6f9c1a1e-6a2f-5f2a-9a52-9b6a6f9c1a1e")
"""Fixed UUIDv5 namespace for this system. Changing it re-keys every derived id
and must be treated as a breaking migration."""


class DeterministicId:
    """Factory for reproducible identifiers."""

    __slots__ = ()

    @staticmethod
    def derive(*parts: str) -> uuid.UUID:
        """Derive a stable UUIDv5 from ordered string parts.

        Parts are joined with a separator that cannot appear in a symbol,
        version string or ISO timestamp, so ``("AB", "CD")`` and ``("A", "BCD")``
        can never collide.
        """
        if not parts:
            raise ValueError("at least one part is required to derive an identifier")
        return uuid.uuid5(TRADING_NAMESPACE, "\x1f".join(parts))
