"""Promotion of strategy versions between lifecycle stages.

``PAPER`` is the first stage that reaches an external venue and ``LIMITED_LIVE``
is the first that reaches money. Both transitions are gated on an explicit,
recorded human approval that binds to the exact version fingerprint being
promoted — so approving version ``1.2.0`` cannot be replayed to promote
``1.2.1`` (ADR-005).

Stages advance one step at a time. Skipping from ``DEVELOPMENT`` straight to
``LIMITED_LIVE`` is rejected even with a valid approval, because the
intermediate stages are where the evidence for that approval is supposed to come
from.

Note that individual *trades* need no approval once a version reaches
``LIMITED_LIVE`` and the control plane enables it. Human authority moved to the
control plane; it did not disappear (ADR-008).
"""

from __future__ import annotations

from typing import Final

from pydantic import Field

from domain.base import FrozenModel
from domain.enums import PromotionStage
from domain.errors import PromotionAuthorityError
from domain.values import NonEmptyText, TimestampUTC

ALLOWED_TRANSITIONS: Final[dict[PromotionStage, frozenset[PromotionStage]]] = {
    PromotionStage.DEVELOPMENT: frozenset({PromotionStage.BACKTEST, PromotionStage.RETIRED}),
    PromotionStage.BACKTEST: frozenset({PromotionStage.OUT_OF_SAMPLE, PromotionStage.RETIRED}),
    PromotionStage.OUT_OF_SAMPLE: frozenset({PromotionStage.WALK_FORWARD, PromotionStage.RETIRED}),
    PromotionStage.WALK_FORWARD: frozenset({PromotionStage.SHADOW, PromotionStage.RETIRED}),
    PromotionStage.SHADOW: frozenset({PromotionStage.PAPER, PromotionStage.RETIRED}),
    PromotionStage.PAPER: frozenset({PromotionStage.LIMITED_LIVE, PromotionStage.RETIRED}),
    PromotionStage.LIMITED_LIVE: frozenset({PromotionStage.PAPER, PromotionStage.RETIRED}),
    PromotionStage.RETIRED: frozenset(),
}
"""One step at a time, plus retirement from anywhere.

``LIMITED_LIVE`` may fall back to ``PAPER`` — demoting a misbehaving strategy
must never require the same ceremony as promoting one.
"""

HUMAN_APPROVAL_REQUIRED: Final[frozenset[PromotionStage]] = frozenset(
    {PromotionStage.PAPER, PromotionStage.LIMITED_LIVE}
)
"""Stages that no automated process may enter on its own.

Everything up to ``SHADOW`` is analysis against recorded or simulated data and
can be driven by an automated pipeline. ``PAPER`` is the first stage that touches
an external venue, and ``LIMITED_LIVE`` is the first that touches money — the two
points where the consequences change kind rather than degree.
"""


class HumanApproval(FrozenModel):
    """A named person's recorded consent to promote one exact version.

    ``version_fingerprint`` is what makes this an approval of a *thing* rather
    than of a *name*. If the code or parameters change, the fingerprint changes
    and the approval no longer applies.
    """

    approver: str = Field(
        min_length=1,
        max_length=128,
        description="Identity of the human granting approval. Never a service account.",
    )
    version_key: str = Field(min_length=3, max_length=128, description="strategy_id@version")
    version_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_at: TimestampUTC
    evidence_uri: str = Field(
        min_length=1,
        max_length=512,
        description="Where the approval is auditable: PR URL, signed commit, ticket.",
    )
    note: NonEmptyText | None = None


class PromotionRecord(FrozenModel):
    """Append-only audit entry for one stage transition."""

    version_key: str
    version_fingerprint: str
    from_stage: PromotionStage
    to_stage: PromotionStage
    occurred_at: TimestampUTC
    approval: HumanApproval | None = None


def authorize_promotion(
    *,
    version_key: str,
    version_fingerprint: str,
    from_stage: PromotionStage,
    to_stage: PromotionStage,
    occurred_at: TimestampUTC,
    approval: HumanApproval | None = None,
) -> PromotionRecord:
    """Validate a stage transition and return its audit record.

    Raises :class:`~domain.errors.PromotionAuthorityError` rather than returning
    a status, so a caller that ignores the result cannot promote by accident.
    """
    if to_stage not in ALLOWED_TRANSITIONS[from_stage]:
        raise PromotionAuthorityError(
            f"illegal promotion {from_stage} -> {to_stage} for {version_key}; "
            f"allowed: {sorted(ALLOWED_TRANSITIONS[from_stage])}"
        )

    if to_stage in HUMAN_APPROVAL_REQUIRED:
        if approval is None:
            raise PromotionAuthorityError(
                f"promotion of {version_key} to {to_stage} requires a recorded human "
                "approval (ADR-005); none was supplied"
            )
        if approval.version_key != version_key:
            raise PromotionAuthorityError(
                f"approval is for {approval.version_key}, not {version_key}"
            )
        if approval.version_fingerprint != version_fingerprint:
            raise PromotionAuthorityError(
                f"approval fingerprint {approval.version_fingerprint} does not match "
                f"version fingerprint {version_fingerprint}; the version changed after approval"
            )

    return PromotionRecord(
        version_key=version_key,
        version_fingerprint=version_fingerprint,
        from_stage=from_stage,
        to_stage=to_stage,
        occurred_at=occurred_at,
        approval=approval,
    )
