"""Database connection handling.

**No credentials live in this repository.** The connection URL is read from the
``TRADING_DATABASE_URL`` environment variable and has no default: a missing URL
raises rather than silently falling back to a local database that might contain
someone's real data.

:func:`redact_url` exists because a DSN is the single most commonly leaked
secret in a stack trace. Nothing in this module logs a raw URL.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_CREDENTIAL_PATTERN: Final[re.Pattern[str]] = re.compile(r"://([^:/@]+)(:[^@]*)?@")


def redact_url(url: str) -> str:
    """Strip user and password from a connection URL for safe logging."""
    return _CREDENTIAL_PATTERN.sub("://***:***@", url)


class DatabaseSettings(BaseSettings):
    """Connection configuration, sourced from the environment only."""

    model_config = SettingsConfigDict(
        env_prefix="TRADING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/trading. "
        "Supplied via environment; never committed."
    )
    echo_sql: bool = False
    pool_size: int = Field(default=5, ge=1, le=50)

    def safe_url(self) -> str:
        """The URL with credentials removed."""
        return redact_url(self.database_url)


def create_db_engine(settings: DatabaseSettings) -> Engine:
    """Build an engine from settings.

    ``pool_pre_ping`` is on because a ledger writer that fails on a stale
    connection loses an import run for no reason.
    """
    return create_engine(
        settings.database_url,
        echo=settings.echo_sql,
        pool_size=settings.pool_size,
        pool_pre_ping=True,
        future=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
