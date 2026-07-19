from decimal import Decimal
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from trading.base import Side
from trading.coinbase_client import CoinbaseClient
from trading.config import CoinbaseCredentials

KEY = ec.generate_private_key(ec.SECP256R1())
PEM = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC_PEM = KEY.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

CREDS = CoinbaseCredentials(
    api_key_name="organizations/test-org/apiKeys/test-key",
    private_key_pem=PEM,
)


def make_client(response_json):
    client = CoinbaseClient(CREDS)
    resp = MagicMock()
    resp.json.return_value = response_json
    client._session.request = MagicMock(return_value=resp)
    return client


def sent_jwt(client):
    kwargs = client._session.request.call_args.kwargs
    return kwargs["headers"]["Authorization"].removeprefix("Bearer ")


def test_jwt_is_es256_signed_and_bound_to_request_uri():
    client = make_client({"accounts": [], "has_next": False})
    client.get_balances()

    token = sent_jwt(client)
    claims = jwt.decode(token, PUBLIC_PEM, algorithms=["ES256"])
    assert claims["iss"] == "cdp"
    assert claims["sub"] == CREDS.api_key_name
    assert claims["uri"] == "GET api.coinbase.com/api/v3/brokerage/accounts"
    assert claims["exp"] - claims["nbf"] == 120
    header = jwt.get_unverified_header(token)
    assert header["kid"] == CREDS.api_key_name
    assert header["nonce"]


def test_get_balances_paginates_and_skips_empty_accounts():
    client = CoinbaseClient(CREDS)
    pages = [
        {"accounts": [{"currency": "BTC",
                       "available_balance": {"value": "1.0"},
                       "hold": {"value": "0.25"}}],
         "has_next": True, "cursor": "c1"},
        {"accounts": [{"currency": "DUST",
                       "available_balance": {"value": "0"},
                       "hold": {"value": "0"}}],
         "has_next": False},
    ]
    resps = []
    for page in pages:
        r = MagicMock()
        r.json.return_value = page
        resps.append(r)
    client._session.request = MagicMock(side_effect=resps)

    balances = client.get_balances()
    assert [b.asset for b in balances] == ["BTC"]
    assert balances[0].total == Decimal("1.25")
    # second call carried the cursor
    assert client._session.request.call_args.kwargs["params"]["cursor"] == "c1"


def test_place_limit_order_success_and_rejection():
    ok = make_client({"success": True,
                      "success_response": {"order_id": "abc-123"}})
    result = ok.place_limit_order("BTC-USD", Side.BUY,
                                 Decimal("0.001"), Decimal("50000"))
    assert result.order_id == "abc-123"
    body = ok._session.request.call_args.kwargs["json"]
    assert body["order_configuration"]["limit_limit_gtc"] == {
        "base_size": "0.001", "limit_price": "50000"}
    assert body["client_order_id"]  # idempotency key always present

    bad = make_client({"success": False, "error_response": {"error": "nope"}})
    with pytest.raises(RuntimeError, match="rejected"):
        bad.place_limit_order("BTC-USD", Side.SELL,
                              Decimal("1"), Decimal("1"))
