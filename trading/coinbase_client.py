"""Coinbase Advanced Trade API client.

Auth model: CDP API keys. Each REST request carries a Bearer JWT signed with
the key's EC private key (ES256). JWTs are single-use in practice — they
embed the request method+path and expire after 120 seconds, so one is minted
per request.

REST base:      https://api.coinbase.com/api/v3/brokerage
WebSocket feed: wss://advanced-trade-ws.coinbase.com
Rate limits:    ~30 req/s private REST per key (public endpoints ~10 req/s).
"""

from __future__ import annotations

import secrets
import time
import uuid
from decimal import Decimal

import jwt  # PyJWT with cryptography extra, for ES256
import requests

from .base import Balance, ExchangeClient, OrderResult, Side, TokenBucketRateLimiter
from .config import CoinbaseCredentials

_HOST = "api.coinbase.com"
_BASE = f"https://{_HOST}/api/v3/brokerage"


class CoinbaseClient(ExchangeClient):
    name = "coinbase"

    def __init__(self, creds: CoinbaseCredentials):
        self._creds = creds
        self._session = requests.Session()
        self._limiter = TokenBucketRateLimiter(rate_per_second=30)

    # -- auth -----------------------------------------------------------

    def _build_jwt(self, method: str, path: str) -> str:
        uri = f"{method} {_HOST}{path}"
        now = int(time.time())
        payload = {
            "sub": self._creds.api_key_name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "uri": uri,
        }
        headers = {
            "kid": self._creds.api_key_name,
            "nonce": secrets.token_hex(16),
        }
        return jwt.encode(payload, self._creds.private_key_pem,
                          algorithm="ES256", headers=headers)

    def _request(self, method: str, path: str, json_body: dict | None = None,
                 params: dict | None = None) -> dict:
        self._limiter.acquire()
        token = self._build_jwt(method, f"/api/v3/brokerage{path}")
        resp = self._session.request(
            method, f"{_BASE}{path}", json=json_body, params=params,
            headers={"Authorization": f"Bearer {token}"}, timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # -- interface ------------------------------------------------------

    def get_balances(self) -> list[Balance]:
        balances: list[Balance] = []
        cursor = None
        while True:
            params = {"limit": 250}
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", "/accounts", params=params)
            for acct in data.get("accounts", []):
                free = Decimal(acct["available_balance"]["value"])
                locked = Decimal(acct.get("hold", {}).get("value", "0"))
                if free or locked:
                    balances.append(Balance(asset=acct["currency"],
                                            free=free, locked=locked))
            if not data.get("has_next"):
                return balances
            cursor = data.get("cursor")

    def place_limit_order(self, symbol: str, side: Side, quantity: Decimal,
                          limit_price: Decimal) -> OrderResult:
        """symbol uses Coinbase product format, e.g. 'BTC-USD'."""
        body = {
            "client_order_id": str(uuid.uuid4()),  # idempotency key
            "product_id": symbol,
            "side": side.value,
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": str(quantity),
                    "limit_price": str(limit_price),
                }
            },
        }
        data = self._request("POST", "/orders", json_body=body)
        if not data.get("success", False):
            raise RuntimeError(f"Coinbase order rejected: {data.get('error_response')}")
        return OrderResult(
            exchange=self.name,
            order_id=data["success_response"]["order_id"],
            symbol=symbol, side=side, quantity=quantity,
            limit_price=limit_price, status="OPEN", raw=data,
        )

    def close(self) -> None:
        self._session.close()
