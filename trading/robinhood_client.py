"""Robinhood client built on the robin_stocks library.

Caveats that shape this adapter:
- robin_stocks wraps Robinhood's private web API — it is unofficial and
  unsupported by Robinhood. Endpoints and login flows can change without
  notice; treat this venue as best-effort and never as the sole execution
  path for anything latency- or reliability-sensitive.
- Login is username/password + TOTP two-factor (pyotp generates the code
  from the stored seed). robin_stocks caches the OAuth token in a local
  pickle (~/.tokens) so subsequent logins reuse it until expiry (~24h).
- robin_stocks holds module-level session state, so unlike the REST
  clients, two RobinhoodClient instances in one process share a login.
  Run one Robinhood agent per process if isolation matters.
- Robinhood publishes no official rate limits and no public WebSocket;
  poll REST conservatively (the limiter here caps at ~1 req/s) and expect
  429s if you exceed unpublished thresholds.
"""

from __future__ import annotations

from decimal import Decimal

import pyotp
import robin_stocks.robinhood as rh

from .base import Balance, ExchangeClient, OrderResult, Side, TokenBucketRateLimiter
from .config import RobinhoodCredentials


class RobinhoodClient(ExchangeClient):
    name = "robinhood"

    def __init__(self, creds: RobinhoodCredentials):
        self._creds = creds
        self._limiter = TokenBucketRateLimiter(rate_per_second=1, burst=5)
        totp = pyotp.TOTP(creds.totp_secret).now()
        rh.login(creds.username, creds.password, mfa_code=totp,
                 store_session=True)

    # -- interface ------------------------------------------------------

    def get_balances(self) -> list[Balance]:
        balances: list[Balance] = []

        self._limiter.acquire()
        profile = rh.profiles.load_account_profile()
        cash = Decimal(profile.get("cash", "0") or "0")
        if cash:
            balances.append(Balance(asset="USD", free=cash))

        self._limiter.acquire()
        for pos in rh.crypto.get_crypto_positions() or []:
            qty = Decimal(pos.get("quantity_available", "0") or "0")
            held = Decimal(pos.get("quantity_held_for_sell", "0") or "0")
            if qty or held:
                balances.append(Balance(
                    asset=pos["currency"]["code"], free=qty, locked=held))
        return balances

    def place_limit_order(self, symbol: str, side: Side, quantity: Decimal,
                          limit_price: Decimal) -> OrderResult:
        """symbol is the bare crypto ticker, e.g. 'BTC'."""
        self._limiter.acquire()
        if side is Side.BUY:
            data = rh.orders.order_buy_crypto_limit(
                symbol, float(quantity), float(limit_price))
        else:
            data = rh.orders.order_sell_crypto_limit(
                symbol, float(quantity), float(limit_price))
        if not data or "id" not in data:
            raise RuntimeError(f"Robinhood order rejected: {data}")
        return OrderResult(
            exchange=self.name, order_id=data["id"], symbol=symbol,
            side=side, quantity=quantity, limit_price=limit_price,
            status=data.get("state", "unconfirmed"), raw=data,
        )

    def close(self) -> None:
        # rh.logout() would also delete the cached token; keep the session
        # so other processes can reuse it. Nothing to release per-instance.
        pass
