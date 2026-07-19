# MSChallenge

Multi-agent crypto trading architecture targeting **Coinbase Advanced
Trade**, **Binance.US**, and **Robinhood** (via `robin_stocks`).

- **Code:** `trading/` — common `ExchangeClient` interface, per-venue
  adapters with auth + rate limiting, agent orchestration, and WebSocket
  market-data streams. `example.py` wires three isolated agents together.
- **Docs:**
  - [`docs/TECHNICAL_SETUP.md`](docs/TECHNICAL_SETUP.md) — architecture,
    authentication workflows, boilerplate map, rate-limit strategy.
  - [`docs/INTEGRATION_PROTOCOL.md`](docs/INTEGRATION_PROTOCOL.md) —
    formal security/operations protocol (credential management,
    resiliency, Azure deployment).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in credentials — never commit .env
python example.py      # reads balances on all three venues; no orders placed
```
