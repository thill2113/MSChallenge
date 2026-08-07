# `data/raw` — immutable source evidence

Everything in this directory is **write-once**. See [ADR-003](../../docs/adr/ADR-003-immutable-raw-data.md).

## Rules

1. **Never edit a file here.** Not to fix a typo, not to redact a field, not to
   reformat JSON. If a capture was wrong, store a *new* capture and record why
   the old one is superseded in the ledger.
2. **Never delete a file here.** Deleting evidence destroys the only record of
   what a source actually said at the time.
3. **Files are content-addressed.** The filename is the SHA-256 of the bytes.
   `journaling.evidence.RawDocumentStore` enforces this and sets files to `0444`.
4. **Normalisation output goes to `data/normalized/`,** which is regenerable and
   git-ignored. Nothing derived belongs here.

CI enforces rules 1 and 2 via `scripts/check_raw_immutability.py`, which fails
the build if a commit modifies or deletes an existing file under this path, or
if any file's content no longer hashes to its own name.

## Layout

```
data/raw/<source_system>/<sha256>.<ext>
```

`<source_system>` is the lower-cased `domain.enums.SourceSystem` value, e.g.
`robinhood_order/`, `claude_recommendation/`, `mcp_tool_call/`.

## Redaction

Redaction happens **before** a payload is stored, never after. If a capture may
contain credentials or account identifiers, redact it in the capture step and
store the redacted bytes — because once it lands here, it cannot be changed.
This is an open item for the MCP tool-call source; see
`journaling/importers/placeholders.py`.

## Current contents

Empty. No historical data has been captured yet. Populating this directory is
the next development task — see `docs/PHASE_1_STATUS.md`.
