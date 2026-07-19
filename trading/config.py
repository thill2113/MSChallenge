"""Credential loading.

All secrets come from environment variables (see .env.example). Nothing is
ever hard-coded or committed. In production, populate the environment from a
secrets manager (Azure Key Vault, AWS Secrets Manager, Vault) at process
start rather than shipping .env files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _require(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {var}")
    return value


@dataclass(frozen=True)
class CoinbaseCredentials:
    """Coinbase Advanced Trade uses CDP API keys: a key name plus an EC
    (ES256) private key in PEM format, used to sign short-lived JWTs."""
    api_key_name: str
    private_key_pem: str

    @classmethod
    def from_env(cls) -> "CoinbaseCredentials":
        return cls(
            api_key_name=_require("COINBASE_API_KEY_NAME"),
            # PEM newlines are usually stored escaped in env vars
            private_key_pem=_require("COINBASE_PRIVATE_KEY").replace("\\n", "\n"),
        )


@dataclass(frozen=True)
class BinanceUSCredentials:
    """Binance.US uses an API key header plus HMAC-SHA256 request signing."""
    api_key: str
    api_secret: str

    @classmethod
    def from_env(cls) -> "BinanceUSCredentials":
        return cls(
            api_key=_require("BINANCEUS_API_KEY"),
            api_secret=_require("BINANCEUS_API_SECRET"),
        )


@dataclass(frozen=True)
class RobinhoodCredentials:
    """Robinhood (via robin_stocks) uses username/password login with TOTP
    two-factor. Store the TOTP seed, not one-time codes."""
    username: str
    password: str
    totp_secret: str

    @classmethod
    def from_env(cls) -> "RobinhoodCredentials":
        return cls(
            username=_require("ROBINHOOD_USERNAME"),
            password=_require("ROBINHOOD_PASSWORD"),
            totp_secret=_require("ROBINHOOD_TOTP_SECRET"),
        )
