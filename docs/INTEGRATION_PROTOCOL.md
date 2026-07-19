# Programmatic Trading System — Integration & Security Protocol

**Document class:** Formal operating protocol
**Scope:** Programmatic integration with Coinbase Advanced Trade, Binance.US, and Robinhood
**Frameworks referenced:** ISO 27001 / CISM practices (credential & risk management), ITIL 4 (service operation), Azure Well-Architected (deployment)
**Companion document:** [`docs/TECHNICAL_SETUP.md`](TECHNICAL_SETUP.md) (implementation)

---

## 1. API integration scope

### 1.1 Endpoint inventory

| Venue | Transport | Purpose | Endpoints in scope |
|---|---|---|---|
| Coinbase Advanced Trade | REST (`api.coinbase.com/api/v3/brokerage`) | Account state, order lifecycle | `GET /accounts`, `POST /orders`, `GET /orders/historical/*`, `POST /orders/batch_cancel` |
| Coinbase Advanced Trade | WebSocket (`advanced-trade-ws.coinbase.com`) | Market data (`ticker`, `level2`), private `user` channel for fills | subscribe-after-connect model |
| Binance.US | REST (`api.binance.us`) | Account state, order lifecycle | `GET /api/v3/account`, `POST/DELETE /api/v3/order`, `GET /api/v3/exchangeInfo` |
| Binance.US | WebSocket (`stream.binance.us:9443`) | Market data (URL-named streams); user-data stream via listenKey for fills | 24 h max socket lifetime |
| Robinhood (unofficial, via `robin_stocks`) | REST only | Account state, crypto order entry | login/OAuth, account profile, crypto positions, crypto limit orders |

### 1.2 Segregation of concerns

- **Market data** is consumed exclusively over WebSocket where available;
  REST quota is reserved for execution and reconciliation. Robinhood has no
  WebSocket — its REST polling is rate-capped in the client and excluded
  from latency-sensitive paths.
- **Wallet/withdrawal endpoints are explicitly out of scope.** API keys are
  provisioned without withdrawal permission (§2), making fund exfiltration
  impossible at the credential level. Trading and treasury operations are
  separated duties.
- Robinhood's API is private and unsupported; it is classified **best-effort
  tier** in this protocol: no SLO is attached to it, and no control depends
  on its availability.

## 2. Credential management (CISM-aligned)

### 2.1 Generation — least privilege at issuance

| Venue | Credential type | Mandatory restrictions at creation |
|---|---|---|
| Coinbase | CDP API key name + EC (ES256) private key | Permissions: *view*, *trade* only. Per-request 120 s JWTs; no static bearer token exists |
| Binance.US | API key + HMAC secret | *Enable Reading* + *Enable Spot Trading* only; **withdrawals disabled**; IP allowlist bound to NAT egress IPs |
| Robinhood | Username/password + TOTP seed | Dedicated account for programmatic use; TOTP mandatory; seed treated as a secret equal to the password |

One credential set **per agent/service identity, per environment**
(dev/staging/prod). Credentials are never shared across environments and
never reused across agents where the venue supports multiple keys — this
gives per-agent revocation and per-agent audit attribution.

### 2.2 Storage

- All secrets live in a managed vault — **Azure Key Vault** in the reference
  deployment (§4) — never in code, images, CI variables marked non-secret,
  or `.env` files outside local development.
- Application access uses **managed identities** (no vault credentials on
  disk). Secrets are injected into process environment at start; they are
  not written to logs, crash dumps are scrubbed, and `.gitignore` excludes
  `.env`, `*.pem`, and the `robin_stocks` token cache.
- Key Vault soft-delete + purge protection enabled; access policies follow
  least privilege (read-only for the trading runtime, write for the
  rotation pipeline only).

### 2.3 Rotation & revocation

| Control | Standard |
|---|---|
| Scheduled rotation | 90 days (Coinbase, Binance.US); Robinhood password/TOTP per corporate password policy |
| Event-driven rotation | Immediately on: personnel change, suspected exposure, vault audit anomaly, vendor breach notice |
| Method | Blue/green: issue new key → deploy to vault under new version → verify with a read-only call → disable old key. Zero-downtime; old key is revoked, not deleted, for audit trail |
| Break-glass | Documented per-venue "kill" procedure: exchange-side key deletion is the authoritative revocation and takes effect ahead of vault changes |

### 2.4 Monitoring & audit

- Vault access logs shipped to the SIEM; alert on reads from unexpected
  identities or geographies.
- Exchange-side API usage reviewed against expected agent behavior weekly;
  any order or IP not attributable to a known agent triggers the
  event-driven rotation path above.
- Secret scanning (pre-commit + org-level) enforced on every repository.

## 3. Operational resiliency (ITIL-aligned)

### 3.1 Availability & failover

- **Venue independence:** each venue adapter is an isolated service unit
  (own client, own limiter, own supervisor). A Coinbase outage degrades
  only Coinbase agents; the incident model treats each venue as a separate
  CI (configuration item).
- **Failure posture is fail-safe, not fail-over:** on loss of a venue, the
  system cancels resting orders where reachable, freezes new order entry on
  that venue, and alerts — it does not automatically re-route orders to
  another venue (different symbols, balances, and fees make silent
  re-routing a financial risk requiring human change approval).
- **Reconnection:** WebSocket streams reconnect with jittered exponential
  backoff (cap 60 s) and resubscribe; Binance.US's 24 h socket termination
  is handled as routine. Stale-data watchdog: if no tick arrives within a
  venue-specific window, mark data stale and suspend strategies consuming it.

### 3.2 Latency management

- NTP sync is a hard dependency (Binance.US `recvWindow` rejects skewed
  requests); chrony with cloud NTP sources, alert at >250 ms drift.
- Order path budgets are measured (client → venue ack) with p50/p99
  dashboards per venue; breaching p99 targets raises an operational event,
  and Robinhood is exempt from latency SLOs (best-effort tier).
- Co-locate compute in a region with favorable RTT to venue endpoints
  (§4); keep market-data fan-out in-process to avoid serialization hops.

### 3.3 Error handling for trade execution

| Failure | Handling standard |
|---|---|
| Timeout / unknown outcome on order submit | **Never blind-retry.** Reconcile first: query open orders by idempotency key (`client_order_id` on Coinbase, `newClientOrderId` on Binance.US); resubmit only if provably absent |
| HTTP 429 | Halt the venue's token bucket, honor `Retry-After`, resume at reduced rate. On Binance.US, repeated 429s escalate to an HTTP 418 IP ban — treat 418 as a P2 incident |
| 5xx / venue degradation | Circuit breaker per venue: open after N consecutive failures, half-open probe with read-only call, close on success |
| Order rejection (business rule) | No retry; log with full venue response, surface to strategy layer |
| Partial fills at shutdown | Drain procedure: stop signal → cancel resting orders → wait for terminal states → snapshot positions |

### 3.4 Incident & change management

- **Incident:** venue-down and 418-ban runbooks with severity matrix;
  post-incident reviews feed the known-error database.
- **Change:** strategy and adapter changes flow through CI with mandatory
  review; production credential or venue-scope changes require change
  approval (standard change for rotation, normal change for new scopes).
- **Continuity:** the system's safe state is *flat or frozen* — recovery
  procedures always prefer canceling exposure over resuming automation.

## 4. Infrastructure deployment (Azure reference)

### 4.1 Topology

```
Internet (exchange APIs)
      ▲
      │  TLS 1.2+ egress only, via static NAT IPs
┌─────┴──────────────────────────────────────────────┐
│ Azure VNet (hub-spoke, prod subscription)          │
│  ┌──────────────┐   ┌───────────────────────────┐  │
│  │ NAT Gateway  │◄──│ Spoke subnet: trading      │  │
│  │ (static IPs, │   │  AKS pool / VMSS           │  │
│  │  allowlisted │   │  - venue adapter pods      │  │
│  │  at venues)  │   │  - orchestrator            │  │
│  └──────────────┘   └────────────┬──────────────┘  │
│                                  │ Private Endpoints│
│                     ┌────────────┴──────────────┐  │
│                     │ Key Vault · Log Analytics │  │
│                     │ Storage (state/journal)   │  │
│                     └───────────────────────────┘  │
└────────────────────────────────────────────────────┘
```

### 4.2 Network configuration

- **Egress:** all outbound traffic leaves through a **NAT Gateway with
  static public IPs**; those IPs are the ones registered in the
  Binance.US key allowlist (and any venue allowlist offered). Azure
  Firewall/NSG restricts egress to the venue FQDNs and ports 443/9443 —
  no general internet egress from trading subnets.
- **Ingress: none.** Trading workloads accept no inbound connections;
  operator access is via Azure Bastion + PIM-elevated, MFA-enforced
  accounts. Control-plane surfaces (dashboards, Grafana) sit behind a
  private endpoint + Entra ID auth.
- **Internal:** Key Vault, Storage, and Log Analytics reachable only via
  Private Endpoints; public network access disabled on those resources.

### 4.3 Environment separation & hardening

- Separate subscriptions (or at minimum resource groups + VNets) for
  dev/staging/prod, each with its own vault and its own venue credentials
  (sandbox keys where the venue offers them).
- Immutable, minimal container images; no SSH daemons in pods; workload
  identity (federated managed identity) instead of secrets for Azure
  resource access.
- Disk and storage encryption at rest (platform keys minimum, CMK where
  policy requires); TLS certificate validation never disabled — the
  corporate CA bundle is baked into images.

### 4.4 Observability & compliance

- Structured logs (order events with idempotency keys, auth events, rate
  limiter saturation) → Log Analytics; 1-year retention or per regulatory
  requirement.
- Alerts: venue circuit-breaker open, stale market data, 418 ban, vault
  anomaly, NTP drift, NAT IP change (which silently breaks allowlists).
- Azure Policy enforces: no public IPs on trading subnets, Key Vault
  firewall on, diagnostic settings mandatory. Quarterly access reviews on
  vault and subscription RBAC.

---

*Robinhood note:* because the integration relies on an unofficial API,
this venue is documented as a **risk-accepted exception** in the risk
register — reduced availability guarantees, potential unilateral breakage,
and terms-of-service exposure should be formally acknowledged by the
system owner before production use.
