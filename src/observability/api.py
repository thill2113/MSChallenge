"""Operational HTTP surface.

Health, readiness and build identity. **No trading endpoints.** There is no
route that creates a candidate, approves a risk decision or submits an order,
and there will not be one until the human-approval workflow in ADR-005 exists —
an HTTP endpoint that places trades is exactly the kind of thing that gets
called by accident.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI
from pydantic import BaseModel

from observability.logging import get_logger

SERVICE_NAME: Final[str] = "hybrid-trading"
API_VERSION: Final[str] = "0.1.0"

logger = get_logger(__name__)


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str
    service: str
    version: str


class ReadinessResponse(BaseModel):
    """Readiness payload, with per-dependency detail."""

    ready: bool
    checks: dict[str, str]


class CapabilitiesResponse(BaseModel):
    """What this build is and is not allowed to do.

    Exposed so an operator can confirm from the outside that a running instance
    cannot trade, rather than inferring it from the deployed commit.
    """

    phase: str
    live_trading_enabled: bool
    autonomous_execution_enabled: bool
    agent_authority: str
    notes: list[str]


def create_app() -> FastAPI:
    """Build the operational API."""
    app = FastAPI(
        title="Hybrid Trading System — operations",
        version=API_VERSION,
        description="Health and introspection only. This API cannot place trades.",
    )

    @app.get("/healthz", response_model=HealthResponse, tags=["ops"])
    def healthz() -> HealthResponse:
        """Liveness: the process is up."""
        return HealthResponse(status="ok", service=SERVICE_NAME, version=API_VERSION)

    @app.get("/readyz", response_model=ReadinessResponse, tags=["ops"])
    def readyz() -> ReadinessResponse:
        """Readiness.

        The database check is a placeholder until a connection is configured;
        it reports ``not_configured`` rather than ``ok`` so an unconfigured
        instance never claims to be ready.
        """
        checks: dict[str, str] = {"process": "ok", "database": "not_configured"}
        return ReadinessResponse(ready=all(v == "ok" for v in checks.values()), checks=checks)

    @app.get("/capabilities", response_model=CapabilitiesResponse, tags=["ops"])
    def capabilities() -> CapabilitiesResponse:
        """Declare the authority boundaries this build enforces."""
        return CapabilitiesResponse(
            phase="0/1",
            live_trading_enabled=False,
            autonomous_execution_enabled=False,
            agent_authority="veto-only",
            notes=[
                "Strategy engine is the sole author of trading parameters (ADR-001).",
                "Agent layer may veto only; it cannot alter any parameter (ADR-002).",
                "Raw source data under data/raw is immutable (ADR-003).",
                "Promotion to production requires recorded human approval (ADR-005).",
            ],
        )

    return app


app: Final[Any] = create_app()
"""Module-level app for ``uvicorn observability.api:app``."""
