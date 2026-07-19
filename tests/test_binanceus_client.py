import hashlib
import hmac
from decimal import Decimal
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from trading.base import Side
from trading.binanceus_client import BinanceUSClient
from trading.config import BinanceUSCredentials

CREDS = BinanceUSCredentials(api_key="test-key", api_secret="test-secret")


def make_client(response_json, status_code=200, headers=None):
    client = BinanceUSClient(CREDS)
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.json.return_value = response_json
    client._session.request = MagicMock(return_value=resp)
    return client


def test_signature_is_valid_hmac_of_query_string():
    client = make_client({"balances": []})
    client.get_balances()

    _, url = client._session.request.call_args.args
    parsed = urlparse(url)
    query, sig_part = parsed.query.rsplit("&signature=", 1)
    expected = hmac.new(b"test-secret", query.encode(), hashlib.sha256).hexdigest()
    assert sig_part == expected
    assert client._session.headers["X-MBX-APIKEY"] == "test-key"


def test_get_balances_filters_zero_and_parses_decimals():
    client = make_client({"balances": [
        {"asset": "BTC", "free": "0.5", "locked": "0.1"},
        {"asset": "DUST", "free": "0", "locked": "0"},
    ]})
    balances = client.get_balances()
    assert len(balances) == 1
    assert balances[0].asset == "BTC"
    assert balances[0].total == Decimal("0.6")


def test_place_limit_order_builds_gtc_limit_params():
    client = make_client({"orderId": 42, "status": "NEW"})
    result = client.place_limit_order("BTCUSD", Side.BUY,
                                     Decimal("0.001"), Decimal("50000"))
    _, url = client._session.request.call_args.args
    params = parse_qs(urlparse(url).query)
    assert params["type"] == ["LIMIT"]
    assert params["timeInForce"] == ["GTC"]
    assert params["side"] == ["BUY"]
    assert params["price"] == ["50000"]
    assert result.order_id == "42"
    assert result.status == "NEW"


def test_429_raises_with_retry_after():
    client = make_client({}, status_code=429, headers={"Retry-After": "7"})
    with pytest.raises(RuntimeError, match="back off 7s"):
        client.get_balances()
