"""CI guard for ADR-003: raw source evidence is append-only.

Two checks:

1. **Content matches address.** Every file under ``data/raw`` whose name is a
   64-character hex digest must hash to that digest. A mismatch means the file
   was edited after it was captured.
2. **No modifications in the diff.** Against a base ref, any raw file that was
   modified, deleted or renamed fails the build. Adding new evidence is always
   fine; changing what was already recorded is not.

Usage::

    python scripts/check_raw_immutability.py
    python scripts/check_raw_immutability.py --base origin/main
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = REPO_ROOT / "data" / "raw"
MUTATING_STATUSES = {"M", "D", "R", "C", "T"}

sys.path.insert(0, str(REPO_ROOT / "src"))

from journaling.evidence import RawDocumentStore  # noqa: E402 - needs the path above


def check_digests() -> list[str]:
    """Report files whose content no longer matches their digest name."""
    store = RawDocumentStore(RAW_ROOT)
    return [
        f"content no longer matches its digest: {p.relative_to(REPO_ROOT)}" for p in store.verify()
    ]


def check_diff(base: str) -> list[str]:
    """Report raw files altered relative to ``base``."""
    try:
        output = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "diff", "--name-status", f"{base}...HEAD", "--", "data/raw"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        ).stdout
    except subprocess.CalledProcessError as exc:
        return [f"could not diff against {base}: {exc.stderr.strip()}"]

    problems: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        status, _, rest = line.partition("\t")
        if status[:1] in MUTATING_STATUSES:
            problems.append(f"raw evidence was {status[:1]}-changed: {rest}")
    return problems


def main() -> int:
    """Run the checks and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Git ref to compare against, e.g. origin/main.")
    args = parser.parse_args()

    problems = check_digests()
    if args.base:
        problems.extend(check_diff(args.base))

    if problems:
        print("data/raw immutability check FAILED (ADR-003):")
        for problem in problems:
            print(f"  - {problem}")
        print("\nRaw evidence is append-only. Store a corrected capture as a new document.")
        return 1

    print("data/raw immutability check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
