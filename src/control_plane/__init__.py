"""Control plane — the configuration only a human may change.

Per-trade human confirmation is gone. What replaces it is *this*: a frozen,
fingerprinted configuration that bounds everything an approved strategy is
allowed to do, and which cannot be changed without a recorded human approval.

The trade is automatic. The envelope it runs inside is not.

Nothing in the hot path writes here. Strategies, the risk engine, the execution
engine and every agent read this configuration and none of them can alter it —
:class:`ControlPlaneConfig` is frozen, and :func:`apply_configuration_change`
demands a :class:`~strategies.promotion.HumanApproval` bound to the exact
fingerprint being adopted.
"""

from control_plane.config import (
    AccountAllocation,
    ControlPlaneConfig,
    InstrumentPermission,
    TradingSession,
    apply_configuration_change,
)

__all__ = [
    "AccountAllocation",
    "ControlPlaneConfig",
    "InstrumentPermission",
    "TradingSession",
    "apply_configuration_change",
]
