# Multi-Agent Crypto Trading Architecture — Technical Setup

Target venues: **Coinbase Advanced Trade**, **Binance.US**, **Robinhood**
(via `robin_stocks`). Companion code lives in `trading/`; a wiring example
is in `example.py`. The security/operations counterpart to this document is
[`docs/INTEGRATION_PROTOCOL.md`](INTEGRATION_PROTOCOL.md).

---

## 1. Modular architecture

```
                        ┌──────────────────┐
                        │   Orchestrator   │  starts/stops agents,
                        │  (agents.py)     │  fans in results queue
                        └───────┬──────────┘
          ┌─────────────────────┼─────────────────────┐
   ┌──────┴──────┐       ┌──────┴──────┐       ┌──────┴──────┐
   │  Agent A    │       │  Agent B    │       │  Agent C    │   one thread each
   │  strategy   │       │  strategy   │       │  strategy   │
   ├─────────────┤       ├─────────────┤       ├─────────────┤
   │ Coinbase    │       │ Binance.US  │       │ Robinhood   │   one client
   │ Client      │       │ Client      │       │ Client      │   instance each
   │ · session   │       │ · session   │       │ · session   │
   │ · limiter   │       │ · limiter   │       │ · limiter   │
   └─────────────┘       └─────────────┘       └─────────────┘
          │                     │                     │
   REST + WS feed        REST + WS feed        REST only (no WS)
```

Design rules that make concurrent agents safe:

1. **One client instance per agent.** `ExchangeClient` subclasses hold all
   mutable state (HTTP session, rate limiter, auth material) on the
   instance. Agents never share a client, so there is no cross-agent
   locking beyond each limiter's internal lock.
2. **Common interface** (`trading/base.py`): `get_balances()`,
   `place_limit_order()`, `close()`. Strategies are written against the
   interface, so any strategy can run on any venue.
3. **Factory-based construction.** Agents receive a *factory*
   (`lambda: CoinbaseClient(creds)`), not a client. On crash/restart the
   supervisor builds a fresh client, guaranteeing clean session and
   limiter state.
4. **Supervised agent loop** (`trading/agents.py`): each agent thread
   restarts its strategy with exponential backoff up to a cap, then
   reports failure on the results queue instead of dying silently.
5. **Market data is decoupled from execution.** WebSocket streams
   (`trading/streams.py`) run on their own daemon threads and push into
   callbacks/queues; REST budget is reserved for order flow.
6. **Robinhood exception:** `robin_stocks` keeps module-level session
   state, so two Robinhood clients in one process share a login. Run at
   most one Robinhood agent per process (or shard by process).

Threads are the right default here because the workload is I/O-bound; the
same shapes port directly to `asyncio` if agent counts grow large.

## 2. Authentication setup

### Coinbase Advanced Trade (CDP keys + per-request JWT)

1. Create an API key at the [CDP portal](https://portal.cdp.coinbase.com/)
   with **view + trade** permissions only. You receive a key *name*
   (`organizations/{org}/apiKeys/{key}`) and an **EC private key (PEM)** —
   downloadable exactly once.
2. Every REST call mints a fresh **ES256-signed JWT** valid for 120 s whose
   `uri` claim binds it to that method + host + path
   (`CoinbaseClient._build_jwt`). There is no static bearer token to leak.
3. Header: `Authorization: Bearer <jwt>`.

### Binance.US (API key + HMAC signing)

1. Create a key under **API Management**; enable only *Reading* and *Spot
   Trading*, and restrict it to your egress IPs.
2. Static key goes in the `X-MBX-APIKEY` header. Every private request
   appends `timestamp` (+ optional `recvWindow`) and a
   **HMAC-SHA256 signature of the query string** computed with the secret
   (`BinanceUSClient._signed_request`).
3. Clock skew beyond `recvWindow` (5 s here) rejects requests — keep NTP
   healthy.

### Robinhood (username/password + TOTP, unofficial)

1. Enable an authenticator app on the account and store the **TOTP seed**.
2. `robin_stocks` logs in with username/password + a `pyotp`-generated
   code, then caches the OAuth token locally (`~/.tokens`, ~24 h reuse).
3. This is a private, unsupported API: expect breakage, don't parallelize
   logins, and treat the venue as best-effort.

Credentials are loaded exclusively from environment variables
(`trading/config.py`, template in `.env.example`); production should
inject them from a secrets manager at process start.

## 3. Boilerplate code map

| File | Contents |
|---|---|
| `trading/base.py` | `ExchangeClient` ABC, `Balance`/`OrderResult` models, thread-safe token-bucket limiter |
| `trading/coinbase_client.py` | JWT auth, paginated `/accounts` balances, `limit_limit_gtc` spot orders with idempotent `client_order_id` |
| `trading/binanceus_client.py` | HMAC signing, `/api/v3/account` balances, `LIMIT`/`GTC` orders, weight-aware pacing + 429 handling |
| `trading/robinhood_client.py` | TOTP login, cash + crypto-position balances, `order_buy/sell_crypto_limit` |
| `trading/agents.py` | `TradingAgent` supervisor thread, `Orchestrator` fan-in |
| `trading/streams.py` | Reconnecting WS ticker streams for Coinbase and Binance.US |
| `example.py` | Three isolated agents reporting balances (order call left commented) |

Install and run:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in credentials — never commit .env
python example.py
```

Note the symbol formats differ per venue: `BTC-USD` (Coinbase),
`BTCUSD` (Binance.US), `BTC` (Robinhood).

## 4. Rate limiting & streams

| | Coinbase Advanced Trade | Binance.US | Robinhood |
|---|---|---|---|
| REST limit | ~30 req/s private per key (~10 req/s public) | **Weight-based**: 1,200 weight/min/IP + order-count caps | Unpublished; assume low |
| Limiter setting | 30/s bucket, 80% utilization | 20 weight/s bucket, per-endpoint weight costs | 1 req/s, burst 5 |
| Overrun signal | HTTP 429 | HTTP 429 (`Retry-After`); persisting → HTTP 418 **IP ban** | HTTP 429 |
| WebSocket | `wss://advanced-trade-ws.coinbase.com` — subscribe after connect; one socket multiplexes products | `wss://stream.binance.us:9443` — streams named in URL; server closes sockets at 24 h | **None** — poll REST |

Strategies applied in the code:

- **Client-side token buckets before every call**, tuned to ~80% of the
  documented limit so retries and bursts don't tip over the edge.
- **Binance.US pacing is weight-aware**: `account` costs 20, `order`
  costs 1; the limiter charges the real cost, and the
  `X-MBX-USED-WEIGHT-1M` response header is the ground truth to reconcile
  against. On 429, stop the bucket and honor `Retry-After` — pushing
  through a 429 is how you earn a 418 IP ban.
- **WebSockets carry all market data** so the REST budget is reserved for
  execution. Streams reconnect with jittered exponential backoff (cap
  60 s) and resubscribe on connect; Binance's 24 h disconnect is treated
  as a routine reconnect.
- **Per-agent isolation means per-agent budgets.** If several agents hit
  the same venue *from the same IP/key*, either give each agent its own
  API key (Coinbase limits are per key) or share one process-wide limiter
  for that venue (Binance.US limits are per IP — instance isolation does
  not add budget).
- **Robinhood:** conservative polling only; back off immediately on any
  429 and never make it the latency-critical leg of a strategy.
