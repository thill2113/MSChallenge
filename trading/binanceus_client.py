"""Binance.US REST API client.

Auth model: static API key sent in the X-MBX-APIKEY header; every signed
(private) request appends a timestamp and an HMAC-SHA256 signature of the
query string, computed with the API secret.

REST base:      https://api.binance.us
WebSocket feed: wss://stream.binance.us:9443
Rate limits:    weight-based — 1,200 request weight per minute per IP, plus
                order-count limits. Every response returns the running
                X-MBX-USED-WEIGHT-1M header; HTTP 429 means back off (honor
                Retry-After), and continuing to hammer after 429 escalates
                to an HTTP 418 IP ban.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from urllib.parse import urlencode

import requests

from .base import Balance, ExchangeClient, OrderResult, Side, TokenBucketRateLimiter
from .config import BinanceUSCredentials

_BASE = "https://api.binance.us"


class BinanceUSClient(ExchangeClient):
    name = "binanceus"

    # request weights from the API docs, used to pace against the 1200/min cap
    _WEIGHTS = {"/api/v3/account": 20, "/api/v3/order": 1}

    def __init__(self, creds: BinanceUSCredentials):
        self._creds = creds
        self._session = requests.Session()
        self._session.headers["X-MBX-APIKEY"] = creds.api_key
        # 1200 weight/min == 20 weight/sec
        self._limiter = TokenBucketRateLimiter(rate_per_second=20, burst=100)

    def _signed_request(self, method: str, path: str, params: dict) -> dict:
        weight = self._WEIGHTS.get(path, 1)
        self._limiter.acquire(cost=weight)
        params = {**params, "timestamp": int(time.time() * 1000),
                  "recvWindow": 5000}
        query = urlencode(params)
        signature = hmac.new(self._creds.api_secret.encode(), query.encode(),
                             hashlib.sha256).hexdigest()
        resp = self._session.request(
            method, f"{_BASE}{path}?{query}&signature={signature}", timeout=10)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            raise RuntimeError(
                f"Binance.US rate limit hit; back off {retry_after}s before retrying")
        resp.raise_for_status()
        return resp.json()

    # -- interface ------------------------------------------------------

    def get_balances(self) -> list[Balance]:
        data = self._signed_request("GET", "/api/v3/account", {})
        return [
            Balance(asset=b["asset"], free=Decimal(b["free"]),
                    locked=Decimal(b["locked"]))
            for b in data.get("balances", [])
            if Decimal(b["free"]) or Decimal(b["locked"])
        ]

    def place_limit_order(self, symbol: str, side: Side, quantity: Decimal,
                          limit_price: Decimal) -> OrderResult:
        """symbol uses Binance format, e.g. 'BTCUSD' (no separator)."""
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": str(quantity),
            "price": str(limit_price),
            "newOrderRespType": "RESULT",
        }
        data = self._signed_request("POST", "/api/v3/order", params)
        return OrderResult(
            exchange=self.name, order_id=str(data["orderId"]), symbol=symbol,
            side=side, quantity=quantity, limit_price=limit_price,
            status=data.get("status", "NEW"), raw=data,
        )

    def close(self) -> None:
        self._session.close()
