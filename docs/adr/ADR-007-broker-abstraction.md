# ADR-007: Broker abstraction

- **Status:** Accepted
- **Date:** 2026-08-07
- **Phase:** Architecture amendment

## Context

Robinhood MCP was the only venue in the original design, and the execution layer
was shaped around it. That is a liability for reasons that have nothing to do
with Robinhood specifically:

- A venue can be unavailable, change its API, or turn out to be wrong for the
  instruments being traded. Rebuilding execution to switch is a large cost paid
  at exactly the moment there is least time.
- Venues differ in what they *can express*. Some support native bracket orders;
  some support only a stop child; some support neither. Code that assumes one
  venue's capabilities silently drops protective legs on another.
- Paper trading requires a venue that offers it. Tying execution to one broker
  ties the entire test strategy to that broker's sandbox.

## Decision

**Nothing above `brokers/` knows what venue it is talking to.**

1. `Broker` is a protocol: `submit_order`, `cancel_order`, `replace_order`,
   `get_order`, `get_orders`, `get_positions`, `get_account_state`,
   `get_execution_updates`, `health_check`.
2. Strategies, risk, portfolio, validator and agents operate **entirely on
   internal domain models**. `BrokerOrderRequest` and `BrokerOrder` are the
   translation boundary.
3. **Adapters translate. They do not reinterpret.** An adapter may rename fields
   and convert units. It may not change what is being asked for. If a venue
   cannot express an intent faithfully, the adapter **fails** rather than
   approximating — an approximation is a trade nobody authorised.
4. **Capabilities are declared, not assumed.** `BrokerCapability` says what a
   venue can do; `select_protection_style` picks the strongest native protection
   the venue actually supports. `ExecutionEngine.build_request` checks for
   `SUBMIT` before building anything.
5. **Native protection is preferred where available.** A stop that lives at the
   venue survives this application crashing. One that lives in a Python process
   does not. Bracket → stop-child → none, in that order of preference.
6. **Errors are typed by what they tell you:**
   - `BrokerRejectedError` — a definite refusal. The order does not exist.
   - `BrokerUnavailableError` — nothing reached the venue. Safe to retry later.
   - `BrokerTimeoutError` — **the outcome is unknown.** The order may be working.

   Conflating the third with the first is how a timed-out order becomes two
   positions.
7. `SimulatedBroker` is a **full implementation**, not a stub: order state,
   fills, partial fills, idempotency, and switchable failure modes. The failure
   paths in ADR-008 need something to exercise them, and a broker that only ever
   succeeds proves nothing.
8. `RobinhoodMCPAdapter` declares **no capabilities at all**. The refusal is
   structural: the engine checks capabilities before building an order, so the
   adapter cannot reach a submission path even if someone deleted the raise in
   `submit_order`. Its read methods exist but do not parse, because Robinhood's
   payload shapes have not been inspected (ADR-003).

## Consequences

**Good**

- Switching or adding a venue touches one package.
- Paper trading is available through whichever venue supports it best.
- Capability differences surface as explicit failures rather than silently
  dropped protective orders.
- The simulator is a first-class venue for testing, so the failure paths are
  actually tested.

**Costs**

- One more translation layer between intent and wire format.
- The lowest-common-denominator interface will not expose every venue's exotic
  order types. That is intentional; strategies should not depend on one.
- Each adapter must implement idempotency in whatever way its venue supports
  (client order id, idempotency key, dedupe on submit). This is the hardest part
  of writing a new adapter and it cannot be shared.

## Enforcement

- `tests/integration/test_decision_pipeline.py::TestBrokerBoundary` — capability
  declarations, structural refusal, protection selection.
- `tests/integration/test_decision_pipeline.py::TestBrokerFailures` — timeout,
  outage, partial fill, idempotent replay.
