"""Structured JSON logging, redaction, and the operations API."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from observability.api import create_app
from observability.logging import (
    REDACTED,
    configure_logging,
    correlation_scope,
    get_correlation_id,
    redact,
)


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging(level=logging.INFO, service="test", environment="test", stream=stream)
    yield stream
    logging.getLogger().handlers.clear()


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


class TestJsonLogging:
    def test_every_record_is_one_json_object(self, log_stream):
        logger = logging.getLogger("test.one")
        logger.info("first")
        logger.warning("second")
        records = _lines(log_stream)
        assert [r["message"] for r in records] == ["first", "second"]
        assert all(r["service"] == "test" for r in records)

    def test_extra_context_is_included(self, log_stream):
        logging.getLogger("test.ctx").info(
            "candidate evaluated", extra={"symbol": "ACME", "verdict": "APPROVED"}
        )
        context = _lines(log_stream)[0]["context"]
        assert context == {"symbol": "ACME", "verdict": "APPROVED"}

    def test_correlation_id_propagates(self, log_stream):
        with correlation_scope("run-42"):
            logging.getLogger("test.corr").info("inside")
            assert get_correlation_id() == "run-42"
        logging.getLogger("test.corr").info("outside")

        records = _lines(log_stream)
        assert records[0]["correlation_id"] == "run-42"
        assert "correlation_id" not in records[1]

    def test_exceptions_are_serialised(self, log_stream):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logging.getLogger("test.exc").exception("failed")
        assert "RuntimeError: boom" in str(_lines(log_stream)[0]["exception"])

    def test_configure_logging_twice_does_not_duplicate_output(self):
        stream = io.StringIO()
        configure_logging(stream=stream)
        configure_logging(stream=stream)
        logging.getLogger("test.dup").info("once")
        assert len(_lines(stream)) == 1
        logging.getLogger().handlers.clear()


class TestRedaction:
    @pytest.mark.parametrize(
        "key", ["password", "token", "api_key", "authorization", "database_url", "account_number"]
    )
    def test_sensitive_keys_are_redacted(self, key):
        assert redact({key: "hunter2"})[key] == REDACTED

    def test_redaction_is_recursive(self):
        payload = {"outer": {"token": "abc", "safe": 1}, "list": [{"secret": "s"}]}
        assert redact(payload) == {
            "outer": {"token": REDACTED, "safe": 1},
            "list": [{"secret": REDACTED}],
        }

    def test_sensitive_values_in_log_extras_are_redacted(self, log_stream):
        logging.getLogger("test.redact").info(
            "connecting", extra={"database_url": "postgres://u:p@h/d"}
        )
        context = _lines(log_stream)[0]["context"]
        assert isinstance(context, dict)
        assert context["database_url"] == REDACTED

    def test_url_redaction_strips_credentials(self):
        from journaling.database import redact_url

        assert redact_url("postgresql+psycopg://alice:s3cret@db:5432/trading") == (
            "postgresql+psycopg://***:***@db:5432/trading"
        )


class TestOperationsApi:
    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(create_app())

    def test_healthz(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readyz_reports_unconfigured_database(self, client):
        body = client.get("/readyz").json()
        assert body["ready"] is False
        assert body["checks"]["database"] == "not_configured"

    def test_capabilities_declares_the_authority_boundaries(self, client):
        body = client.get("/capabilities").json()
        assert body["live_trading_implemented"] is False
        assert body["agent_in_execution_path"] is False
        assert body["agent_authority"] == "veto-only, asynchronous"
        # Automatic per-trade execution is the amended design, stated plainly
        # so an operator can verify it from outside the process.
        assert body["per_trade_human_approval_required"] is False
        assert "LIVE" not in body["implemented_execution_modes"]

    def test_there_are_no_trading_routes(self, client):
        paths = set(client.app.openapi()["paths"])
        assert paths == {"/healthz", "/readyz", "/capabilities"}
