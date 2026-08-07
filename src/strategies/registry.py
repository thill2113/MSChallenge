"""In-process registry of strategy versions.

Phase 1 keeps the registry in memory. It is the single place that knows which
version of which strategy exists and what stage it has reached, and it refuses
to hand out a production strategy that was never promoted by a human.
"""

from __future__ import annotations

from collections.abc import Iterator

from domain.enums import PromotionStage
from domain.errors import PromotionAuthorityError
from domain.values import TimestampUTC
from strategies.models import StrategyVersion
from strategies.promotion import HumanApproval, PromotionRecord, authorize_promotion


class StrategyRegistry:
    """Tracks registered strategy versions and their promotion history."""

    def __init__(self) -> None:
        self._versions: dict[str, StrategyVersion] = {}
        self._history: list[PromotionRecord] = []

    def register(self, version: StrategyVersion) -> StrategyVersion:
        """Register a new version.

        Re-registering an existing key is rejected: a version is immutable by
        definition, so "updating" one would silently invalidate every backtest
        and ledger row that already references it (ADR-004).
        """
        if version.key in self._versions:
            existing = self._versions[version.key]
            if existing.authoritative_fingerprint() == version.authoritative_fingerprint():
                return existing
            raise ValueError(
                f"{version.key} is already registered with a different fingerprint; "
                "publish a new version instead of redefining an existing one"
            )
        if version.stage is not PromotionStage.DEVELOPMENT:
            raise PromotionAuthorityError(
                f"{version.key} must be registered at {PromotionStage.DEVELOPMENT}; "
                f"got {version.stage}. Stages are earned through promote(), not declared."
            )
        self._versions[version.key] = version
        return version

    def get(self, key: str) -> StrategyVersion:
        """Return a registered version by ``strategy_id@version``."""
        try:
            return self._versions[key]
        except KeyError:
            raise KeyError(f"no strategy version registered as {key!r}") from None

    def promote(
        self,
        key: str,
        to_stage: PromotionStage,
        *,
        occurred_at: TimestampUTC,
        approval: HumanApproval | None = None,
    ) -> StrategyVersion:
        """Advance a version one stage, recording who authorised it."""
        current = self.get(key)
        record = authorize_promotion(
            version_key=key,
            version_fingerprint=current.authoritative_fingerprint(),
            from_stage=current.stage,
            to_stage=to_stage,
            occurred_at=occurred_at,
            approval=approval,
        )
        promoted = current.model_copy(update={"stage": to_stage})
        self._versions[key] = promoted
        self._history.append(record)
        return promoted

    def production_versions(self) -> tuple[StrategyVersion, ...]:
        """Every version currently cleared to trade real money."""
        return tuple(v for v in self._versions.values() if v.stage is PromotionStage.LIMITED_LIVE)

    def versions_at(self, stage: PromotionStage) -> tuple[StrategyVersion, ...]:
        """Every version currently at ``stage``."""
        return tuple(v for v in self._versions.values() if v.stage is stage)

    def history(self) -> tuple[PromotionRecord, ...]:
        """Append-only promotion audit trail, oldest first."""
        return tuple(self._history)

    def __contains__(self, key: object) -> bool:
        return key in self._versions

    def __iter__(self) -> Iterator[StrategyVersion]:
        return iter(self._versions.values())

    def __len__(self) -> int:
        return len(self._versions)
