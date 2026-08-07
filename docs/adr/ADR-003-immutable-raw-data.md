# ADR-003: Immutable raw data

- **Status:** Accepted
- **Date:** 2026-08-07
- **Phase:** 0/1

## Context

The system will import history from sources it does not control: Claude
conversation recommendations, Robinhood orders and fills, MCP tool calls. That
history is the only record of what was actually recommended and what actually
happened. It cannot be re-derived — a broker's API returns today's view, not
last March's.

The specific danger is well-intentioned cleanup. Someone fixes a malformed
timestamp, normalises a symbol, redacts a field, reformats JSON for
readability. Each edit is small and each is reasonable. Collectively they mean
the "source of truth" has been quietly rewritten to agree with whatever the
parser expected, and a disagreement between the ledger and the brokerage can no
longer be adjudicated.

## Decision

**`data/raw/` is write-once and content-addressed.**

1. A stored document's filename is the SHA-256 of its bytes:
   `data/raw/<source_system>/<sha256>.<ext>`. Content and address cannot drift
   apart without detection.
2. `RawDocumentStore.store()` writes via a staging file and `replace()`, then
   sets mode `0444`. A crash mid-write cannot leave truncated bytes at a valid
   digest path.
3. Storing identical bytes twice is a **no-op** returning the original record —
   so re-running an import is safe.
4. Storing different bytes at an existing digest path raises
   `ImmutableEvidenceError` rather than picking a winner. Two different answers
   to the same question is a fact to surface, not a conflict to resolve
   silently.
5. `store.verify()` re-hashes everything and reports any file whose content no
   longer matches its name.
6. **CI enforces it.** `scripts/check_raw_immutability.py` fails the build if a
   commit modifies, deletes or renames an existing file under `data/raw`, or if
   any digest check fails. Adding new evidence is always allowed.
7. **Redaction happens before storage, never after.** A payload that may carry
   credentials or account identifiers is redacted in the capture step. Once it
   lands, it cannot be changed.
8. Derived output goes to `data/normalized/`, which is git-ignored and freely
   regenerable. `TradeRecord` rows from external sources are *required* to carry
   a `raw_document_sha256`, so every normalized fact points back to the evidence
   it came from.

## Consequences

**Good**

- A ledger row can always be traced to the bytes that produced it.
- Re-running an import after a parser fix reprocesses the original evidence, not
  a previously-cleaned version of it.
- A disagreement with the brokerage is adjudicable.

**Costs**

- Storage grows monotonically. Nothing is ever reclaimed. At the volumes this
  system deals in that is negligible, and the tradeoff would need revisiting
  only at a scale we are nowhere near.
- Fixing a bad capture means storing a second document and marking the first
  superseded in the ledger, which is more ceremony than editing a file.
- Contributors must not `chmod +w` and edit. CI catches it; the friction is the
  point.

## Enforcement

- `tests/unit/test_evidence_immutability.py` — including a test that simulates
  someone forcing an edit past the read-only bit and confirms `verify()` and
  `read()` both catch it.
- `scripts/check_raw_immutability.py`, wired into the `data-integrity` CI job.
