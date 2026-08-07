"""Structured JSON logging.

Every log line is a single JSON object on one line. That is not a stylistic
choice — an incident review of a trading system means correlating a strategy
decision, a risk verdict and a broker response that happened milliseconds apart,
and grep over prose does not do that.

Two properties are enforced here:

* **Correlation.** A ``correlation_id`` set once with :func:`correlation_scope`
  appears on every line emitted inside it, across modules.
* **Redaction.** Field names that commonly carry secrets are replaced with
  ``"***"`` before serialisation. This is a backstop, not a licence to log
  credentials.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Final

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

REDACTED: Final[str] = "***"

SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "cookie",
        "session_id",
        "database_url",
        "dsn",
        "private_key",
        "mfa_code",
        "account_number",
    }
)

_RESERVED: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively replace values held under sensitive keys."""
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def set_correlation_id(value: str | None) -> None:
    """Set the correlation id for the current context."""
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    """Correlation id currently in force, if any."""
    return _correlation_id.get()


@contextmanager
def correlation_scope(value: str) -> Iterator[str]:
    """Bind ``value`` as the correlation id for the duration of the block."""
    token = _correlation_id.set(value)
    try:
        yield value
    finally:
        _correlation_id.reset(token)


class JsonFormatter(logging.Formatter):
    """Renders a log record as one line of JSON."""

    def __init__(self, *, service: str = "hybrid-trading", environment: str = "local") -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        """Serialise ``record``. Never raises: a broken log line must not stop a trade."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
            "environment": self._environment,
            "module": record.module,
            "line": record.lineno,
        }

        correlation_id = get_correlation_id()
        if correlation_id is not None:
            payload["correlation_id"] = correlation_id

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        if extras:
            payload["context"] = redact(extras)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return json.dumps(
                {
                    "timestamp": payload["timestamp"],
                    "level": "ERROR",
                    "logger": record.name,
                    "message": "log record could not be serialised",
                }
            )


def configure_logging(
    *,
    level: int | str = logging.INFO,
    service: str = "hybrid-trading",
    environment: str = "local",
    stream: Any = None,
) -> logging.Logger:
    """Install the JSON formatter on the root logger.

    Existing handlers are replaced rather than appended to, so calling this twice
    does not produce doubled output.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service=service, environment=environment))
    root.addHandler(handler)
    root.setLevel(level)
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a module logger. Configuration is global; call sites do not set it."""
    return logging.getLogger(name)
