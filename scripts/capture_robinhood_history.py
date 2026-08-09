"""Capture Robinhood history into the immutable evidence store (ADR-003).

This script does not call MCP itself — the MCP session belongs to the host, and
this repository never holds a credential. The operator (or an agent with the
Robinhood MCP tools available) invokes the read-only tools, writes each raw
response to a file, and points this script at that directory.

What it does:

1. **Redacts before storing.** Account numbers are replaced with a stable
   pseudonym derived from a salted hash, so the same account is recognisable
   across files without the number being recoverable. Redaction happens *before*
   the write because ``data/raw`` is write-once — once it lands it cannot be
   cleaned up.
2. **Stores content-addressed.** Each redacted payload goes through
   :class:`~journaling.evidence.RawDocumentStore`, so it is hashed, written
   read-only, and de-duplicated.
3. **Emits a manifest** listing what was captured, for the ingestion run record.

Usage::

    python scripts/capture_robinhood_history.py --input captures/ --dry-run
    python scripts/capture_robinhood_history.py --input captures/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from domain.enums import SourceSystem  # noqa: E402
from journaling.evidence import RawDocumentStore  # noqa: E402

TOOL_TO_SOURCE: dict[str, SourceSystem] = {
    "get_accounts": SourceSystem.MCP_TOOL_CALL,
    "get_portfolio": SourceSystem.ROBINHOOD_POSITION,
    "get_equity_positions": SourceSystem.ROBINHOOD_POSITION,
    "get_option_positions": SourceSystem.ROBINHOOD_POSITION,
    "get_equity_orders": SourceSystem.ROBINHOOD_ORDER,
    "get_option_orders": SourceSystem.ROBINHOOD_ORDER,
    "get_pnl_trade_history": SourceSystem.ROBINHOOD_FILL,
    "get_realized_pnl": SourceSystem.ROBINHOOD_FILL,
}

SENSITIVE_KEYS = frozenset({"account_number", "rhs_account_number", "rhc_account_number"})


def pseudonym(value: str, salt: str) -> str:
    """Stable, non-reversible stand-in for an account identifier.

    A pseudonym rather than a blank: analysis needs to tell two accounts apart,
    and it does not need to know which is which.
    """
    digest = hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
    return f"ACCT-{digest[:8].upper()}"


def redact(node: Any, salt: str) -> Any:
    """Recursively replace account identifiers with pseudonyms."""
    if isinstance(node, dict):
        return {
            key: (
                pseudonym(str(value), salt)
                if key in SENSITIVE_KEYS and isinstance(value, str | int)
                else redact(value, salt)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [redact(item, salt) for item in node]
    return node


def tool_name_from(path: Path) -> str:
    """Infer the originating tool from a capture filename."""
    stem = path.stem
    for tool in TOOL_TO_SOURCE:
        if tool in stem:
            return tool
    raise ValueError(
        f"cannot infer the tool for {path.name}; name captures like "
        f"'<tool>-<timestamp>.json' using one of: {sorted(TOOL_TO_SOURCE)}"
    )


def main() -> int:
    """Redact and store every capture in the input directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Directory of raw MCP responses.")
    parser.add_argument(
        "--salt",
        default="hybrid-trading",
        help="Pseudonym salt. Keep it consistent across captures or the same account "
        "will appear as two.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = parser.parse_args()

    store = RawDocumentStore(REPO_ROOT / "data" / "raw")
    manifest: list[dict[str, Any]] = []

    for path in sorted(args.input.glob("*.json")):
        tool = tool_name_from(path)
        payload = json.loads(path.read_text())
        cleaned = json.dumps(
            redact(payload, args.salt), sort_keys=True, separators=(",", ":")
        ).encode()

        if args.dry_run:
            digest = hashlib.sha256(cleaned).hexdigest()
            print(f"would store {path.name} -> {TOOL_TO_SOURCE[tool].value}/{digest[:16]}…")
            continue

        stored = store.store(
            cleaned, source_system=TOOL_TO_SOURCE[tool], content_type="application/json"
        )
        manifest.append(
            {
                "capture": path.name,
                "tool": tool,
                "source_system": stored.source_system.value,
                "sha256": stored.sha256,
                "bytes": stored.byte_size,
            }
        )
        print(f"stored {path.name} -> {stored.relative_path}")

    if manifest and not args.dry_run:
        print(f"\n{len(manifest)} document(s) captured.")
        print("Next: inspect them and answer RobinhoodOrderImporter.open_questions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
