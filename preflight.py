"""Trade-readiness preflight: read-only connectivity checks per venue.

Runs only against venues whose credentials are present in the environment,
skipping the rest. Makes NO orders — balances/auth checks only. Exit code 0
means every configured venue is reachable and authenticated.

Usage:
    python preflight.py            # check all configured venues
    python preflight.py robinhood  # check one venue
"""

from __future__ import annotations

import os
import sys

VENUES = {
    "coinbase": (
        ["COINBASE_API_KEY_NAME", "COINBASE_PRIVATE_KEY"],
        lambda: _check_coinbase(),
    ),
    "binanceus": (
        ["BINANCEUS_API_KEY", "BINANCEUS_API_SECRET"],
        lambda: _check_binanceus(),
    ),
    "robinhood": (
        ["ROBINHOOD_USERNAME", "ROBINHOOD_PASSWORD", "ROBINHOOD_TOTP_SECRET"],
        lambda: _check_robinhood(),
    ),
}


def _check_coinbase() -> str:
    from trading.coinbase_client import CoinbaseClient
    from trading.config import CoinbaseCredentials
    with CoinbaseClient(CoinbaseCredentials.from_env()) as client:
        balances = client.get_balances()
    return f"auth OK, {len(balances)} non-zero balances"


def _check_binanceus() -> str:
    from trading.binanceus_client import BinanceUSClient
    from trading.config import BinanceUSCredentials
    with BinanceUSClient(BinanceUSCredentials.from_env()) as client:
        balances = client.get_balances()
    return f"auth OK, {len(balances)} non-zero balances"


def _check_robinhood() -> str:
    from trading.config import RobinhoodCredentials
    from trading.robinhood_client import RobinhoodClient
    with RobinhoodClient(RobinhoodCredentials.from_env()) as client:
        balances = client.get_balances()
    return f"login OK, {len(balances)} non-zero balances"


def main() -> int:
    requested = sys.argv[1:] or list(VENUES)
    failures = 0
    for venue in requested:
        env_vars, check = VENUES[venue]
        missing = [v for v in env_vars if not os.environ.get(v)]
        if missing:
            print(f"[skip] {venue}: missing {', '.join(missing)}")
            continue
        try:
            print(f"[ ok ] {venue}: {check()}")
        except Exception as exc:  # noqa: BLE001 - report every venue, then exit nonzero
            failures += 1
            print(f"[FAIL] {venue}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
