from decimal import Decimal
from unittest.mock import patch

import pytest

from trading.base import Side
from trading.config import RobinhoodCredentials
from trading.robinhood_client import RobinhoodClient

CREDS = RobinhoodCredentials(username="user", password="pass",
                             totp_secret="JBSWY3DPEHPK3PXP")


def make_client():
    with patch("trading.robinhood_client.rh") as rh_mock:
        client = RobinhoodClient(CREDS)
        rh_mock.login.assert_called_once()
        assert rh_mock.login.call_args.kwargs["mfa_code"]
    return client


def test_balances_combine_cash_and_crypto_positions():
    client = make_client()
    with patch("trading.robinhood_client.rh") as rh_mock:
        rh_mock.profiles.load_account_profile.return_value = {"cash": "100.50"}
        rh_mock.crypto.get_crypto_positions.return_value = [
            {"currency": {"code": "BTC"},
             "quantity_available": "0.5", "quantity_held_for_sell": "0.1"},
            {"currency": {"code": "ETH"},
             "quantity_available": "0", "quantity_held_for_sell": "0"},
        ]
        balances = {b.asset: b for b in client.get_balances()}
    assert balances["USD"].free == Decimal("100.50")
    assert balances["BTC"].total == Decimal("0.6")
    assert "ETH" not in balances


def test_limit_order_routes_by_side_and_wraps_result():
    client = make_client()
    with patch("trading.robinhood_client.rh") as rh_mock:
        rh_mock.orders.order_buy_crypto_limit.return_value = {
            "id": "ord-1", "state": "unconfirmed"}
        result = client.place_limit_order("BTC", Side.BUY,
                                          Decimal("0.001"), Decimal("50000"))
        rh_mock.orders.order_buy_crypto_limit.assert_called_once_with(
            "BTC", 0.001, 50000.0)
    assert result.order_id == "ord-1"


def test_rejected_order_raises():
    client = make_client()
    with patch("trading.robinhood_client.rh") as rh_mock:
        rh_mock.orders.order_sell_crypto_limit.return_value = {
            "detail": "insufficient holdings"}
        with pytest.raises(RuntimeError, match="rejected"):
            client.place_limit_order("BTC", Side.SELL,
                                     Decimal("1"), Decimal("1"))
