"""Risk engine — the sole gate between a proposal and an executable order.

Boundary contract (ADR-001):

* **Consumes** a :class:`~strategies.models.TradeCandidate`.
* **Produces** a :class:`~risk.models.RiskDecision` — ``APPROVED`` or
  ``REJECTED``, bound to the candidate's fingerprint.
* **Never** modifies the candidate. It has no authority to resize, re-stop or
  re-target; approving a *different* trade than the one proposed would make the
  risk engine a second strategy author.

No production limit values ship in this repository. See :mod:`risk.limits`.
"""

from risk.engine import RiskEngine
from risk.limits import RiskLimits
from risk.models import RiskDecision, RiskViolation, RiskViolationCode

__all__ = [
    "RiskDecision",
    "RiskEngine",
    "RiskLimits",
    "RiskViolation",
    "RiskViolationCode",
]
