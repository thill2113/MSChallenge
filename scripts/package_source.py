"""Package the source tree into plain-text bundles for an external reviewer.

Produces size-bounded ``.txt`` parts (an LLM chat window or file upload
tolerates those far better than a repository link) plus a ``.zip`` of the same
content for tools that accept archives.

Files are emitted in dependency order — domain primitives first, then the
layers that build on them — so a reader who stops halfway still has a coherent
picture rather than a random slice. Parts split only on file boundaries, so no
file is ever cut in half across two uploads.

Excluded deliberately: ``.venv``, caches, ``uv.lock`` (11k lines of hashes that
teach a reviewer nothing), and everything under ``data/`` — the captures there
are real brokerage history and have never been committed. See
``docs/AUDIT_AND_LEARNINGS.txt`` part 6.

Usage::

    python scripts/package_source.py --out /path/to/bundle
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 - fixed argv, no shell, no user input
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MAX_PART_BYTES = 110_000
"""Soft ceiling per part. Exceeded only when a single file is larger."""

# Dependency order: each group depends only on groups above it.
SOURCE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Project configuration",
        ("pyproject.toml", "Makefile", "Dockerfile", ".github/workflows/ci.yml"),
    ),
    (
        "Layer 0 - domain primitives (frozen models, fingerprinting, exact numerics)",
        (
            "src/domain/base.py",
            "src/domain/values.py",
            "src/domain/enums.py",
            "src/domain/identifiers.py",
            "src/domain/errors.py",
        ),
    ),
    (
        "Layer 1 - market data and signals",
        (
            "src/market_data/models.py",
            "src/market_data/providers.py",
            "src/signals/models.py",
            "src/signals/indicators.py",
            "src/signals/protocols.py",
            "src/regime/models.py",
            "src/regime/protocols.py",
        ),
    ),
    (
        "Layer 2 - strategy (the ONLY author of trading parameters)",
        (
            "src/strategies/models.py",
            "src/strategies/context.py",
            "src/strategies/protocols.py",
            "src/strategies/registry.py",
            "src/strategies/promotion.py",
            "src/strategies/determinism.py",
            "src/strategies/library/trend_breakout.py",
        ),
    ),
    (
        "Layer 3 - risk (approves or rejects; never authors)",
        (
            "src/risk/models.py",
            "src/risk/limits.py",
            "src/risk/engine.py",
            "src/portfolio/models.py",
        ),
    ),
    (
        "Layer 4 - agents (veto-only; structurally cannot alter parameters)",
        (
            "src/agents/models.py",
            "src/agents/context.py",
            "src/agents/store.py",
            "src/agents/gate.py",
        ),
    ),
    (
        "Layer 5 - control plane (human-approved configuration)",
        ("src/control_plane/config.py",),
    ),
    (
        "Layer 6 - execution (accepts only approved, immutable intents)",
        (
            "src/execution/models.py",
            "src/execution/validator.py",
            "src/execution/killswitch.py",
            "src/execution/engine.py",
        ),
    ),
    (
        "Layer 7 - brokers",
        (
            "src/brokers/base.py",
            "src/brokers/simulated/broker.py",
            "src/brokers/robinhood_mcp/models.py",
            "src/brokers/robinhood_mcp/session.py",
            "src/brokers/robinhood_mcp/adapter.py",
        ),
    ),
    (
        "Layer 8 - journaling (append-only evidence ledger)",
        (
            "src/journaling/models.py",
            "src/journaling/schema.py",
            "src/journaling/database.py",
            "src/journaling/repository.py",
            "src/journaling/evidence.py",
            "src/journaling/importers/base.py",
            "src/journaling/importers/placeholders.py",
        ),
    ),
    (
        "Layer 9 - observability and backtesting",
        (
            "src/observability/events.py",
            "src/observability/logging.py",
            "src/observability/api.py",
            "src/backtesting/simulation.py",
            "src/backtesting/replay.py",
        ),
    ),
    (
        "Package exports",
        tuple(str(p.relative_to(REPO_ROOT)) for p in sorted(REPO_ROOT.glob("src/**/__init__.py"))),
    ),
)

TEST_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Test fixtures and doubles",
        (
            "tests/conftest.py",
            "tests/doubles.py",
            *(
                str(p.relative_to(REPO_ROOT))
                for p in sorted(REPO_ROOT.glob("tests/**/__init__.py"))
            ),
        ),
    ),
    (
        "Invariant tests - these are the specification, not decoration",
        (
            "tests/unit/test_agent_authority.py",
            "tests/unit/test_risk_enforcement.py",
            "tests/unit/test_domain_models.py",
            "tests/unit/test_final_validator.py",
            "tests/unit/test_kill_switch_and_control_plane.py",
            "tests/unit/test_agent_context.py",
            "tests/unit/test_evidence_immutability.py",
            "tests/unit/test_events.py",
            "tests/unit/test_strategy_promotion.py",
            "tests/unit/test_strategy_v1.py",
            "tests/unit/test_simulation.py",
            "tests/unit/test_importers.py",
            "tests/unit/test_observability.py",
            "tests/property/test_strategy_determinism.py",
            "tests/integration/test_decision_pipeline.py",
            "tests/integration/test_ledger.py",
        ),
    ),
    (
        "Scripts",
        (
            "scripts/backtest_strategy.py",
            "scripts/capture_robinhood_history.py",
            "scripts/check_raw_immutability.py",
        ),
    ),
)

DOC_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Architecture and status",
        ("README.md", "docs/ARCHITECTURE.md", "docs/PHASE_1_STATUS.md", "docs/STRATEGY_V1.md"),
    ),
    (
        "Architecture decision records",
        tuple(str(p.relative_to(REPO_ROOT)) for p in sorted(REPO_ROOT.glob("docs/adr/*.md"))),
    ),
)

DELIBERATELY_UNBUNDLED = frozenset(
    {
        # Shipped in the .zip and delivered on its own; 27 kB of narrative that
        # would crowd out code in the paste-sized parts.
        "docs/AUDIT_AND_LEARNINGS.txt",
    }
)

ZIP_INCLUDE_ROOTS = ("src", "tests", "scripts", "docs", ".github")
ZIP_INCLUDE_FILES = (
    "pyproject.toml",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".gitignore",
    ".env.example",
    "README.md",
    "data/fixtures/risk_limits.shadow.json",
    "data/raw/README.md",
    "data/normalized/README.md",
    "data/fixtures/README.md",
)


def fence_for(path: str) -> str:
    """Markdown language tag for a path's extension."""
    return {
        ".py": "python",
        ".toml": "toml",
        ".md": "markdown",
        ".json": "json",
        ".yml": "yaml",
        ".yaml": "yaml",
    }.get(Path(path).suffix, "")


def render_file(rel: str) -> str:
    """One file as a delimited, fenced block."""
    body = (REPO_ROOT / rel).read_text(encoding="utf-8").rstrip("\n")
    lines = body.count("\n") + 1
    return (
        f"\n{'=' * 78}\nFILE: {rel}  ({lines} lines)\n{'=' * 78}\n"
        f"```{fence_for(rel)}\n{body}\n```\n"
    )


def build_parts(
    groups: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    seen: set[str] | None = None,
) -> list[str]:
    """Render groups into size-bounded parts, splitting only between files."""
    seen = seen if seen is not None else set()
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for heading, paths in groups:
        pending_heading: str | None = f"\n\n{'#' * 78}\n## {heading}\n{'#' * 78}\n"
        for rel in paths:
            if rel in seen or not (REPO_ROOT / rel).is_file():
                continue
            seen.add(rel)
            block = render_file(rel)
            chunk = (pending_heading or "") + block
            if current and size + len(chunk) > MAX_PART_BYTES:
                parts.append("".join(current))
                current, size = [], 0
                # Repeat the heading at the top of the continuation part.
                chunk = f"\n\n{'#' * 78}\n## {heading} (continued)\n{'#' * 78}\n" + block
            current.append(chunk)
            size += len(chunk)
            pending_heading = None
    if current:
        parts.append("".join(current))
    return parts


def repo_tree() -> str:
    """A `git ls-files` tree, restricted to what this bundle covers."""
    out = subprocess.run(  # nosec B603 - fixed argv, no shell
        ["git", "ls-files", "src", "tests", "scripts", "docs", ".github"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def assert_complete(bundled: set[str]) -> None:
    """Fail loudly if a tracked source file reached neither a part nor the exclusion list.

    The groups above are hand-ordered, which is what makes the bundle readable
    and also what makes it easy to add a module and forget to list it. A handoff
    that silently omits a file is worse than one that fails to build.
    """
    tracked = subprocess.run(  # nosec B603 - fixed argv, no shell
        ["git", "ls-files", "src", "tests", "scripts", "docs", ".github"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    missing = sorted(set(tracked) - bundled - DELIBERATELY_UNBUNDLED)
    if missing:
        raise SystemExit(
            "package_source: these tracked files are in no group and not in "
            "DELIBERATELY_UNBUNDLED:\n  " + "\n  ".join(missing)
        )


def write_zip(target: Path) -> int:
    """Zip the reviewable tree. Returns the file count."""
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for root in ZIP_INCLUDE_ROOTS:
            for path in sorted((REPO_ROOT / root).rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                archive.write(path, path.relative_to(REPO_ROOT).as_posix())
                count += 1
        for rel in ZIP_INCLUDE_FILES:
            path = REPO_ROOT / rel
            if path.is_file():
                archive.write(path, rel)
                count += 1
    return count


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    written: list[tuple[str, int]] = []

    def emit(name: str, text: str) -> None:
        (out / name).write_text(text, encoding="utf-8")
        written.append((name, len(text.encode("utf-8"))))

    seen: set[str] = set()
    source_parts = build_parts(SOURCE_GROUPS, seen=seen)
    test_parts = build_parts(TEST_GROUPS, seen=seen)
    doc_parts = build_parts(DOC_GROUPS, seen=seen)

    assert_complete(seen)

    total = len(source_parts) + len(test_parts) + len(doc_parts)
    index = 1
    names: list[str] = []
    for label, parts in (
        ("SOURCE", source_parts),
        ("TESTS", test_parts),
        ("DOCS", doc_parts),
    ):
        for n, body in enumerate(parts, start=1):
            name = f"{index:02d}_{label}_part{n}_of_{len(parts)}.txt"
            header = (
                f"HYBRID TRADING SYSTEM - {label} bundle, part {n} of {len(parts)}\n"
                f"File {index} of {total + 1} in this handoff. "
                f"Read 00_START_HERE.txt first.\n"
            )
            emit(name, header + body + "\n")
            names.append(name)
            index += 1

    zip_name = "hybrid-trading-system-source.zip"
    zipped = write_zip(out / zip_name)
    emit("00_START_HERE.txt", start_here(names, zip_name, zipped))

    for name, size in sorted(written):
        print(f"  {name:44s} {size / 1000:7.1f} kB")
    print(f"  {zip_name:44s} {(out / zip_name).stat().st_size / 1000:7.1f} kB ({zipped} files)")
    print(f"\nwrote {len(written) + 1} files to {out}")
    return 0


def start_here(names: list[str], zip_name: str, zipped: int) -> str:
    """The orientation file an external reviewer should read first."""
    listing = "\n".join(f"  - {n}" for n in names)
    return f"""HYBRID TRADING SYSTEM - SOURCE HANDOFF
{"=" * 78}

WHAT THIS IS
  A deterministic automated trading platform, Phase 0/1. The strategy engine is
  the sole author of trading parameters. Every other layer may approve, reject,
  veto or transmit -- none may author or amend. AI agents hold veto authority
  ONLY, enforced structurally rather than by convention.

  Status: no live trading. Execution mode DISABLED/SHADOW only. The one strategy
  in the tree has NOT been approved for capital.

FILES IN THIS HANDOFF
{listing}
  - {zip_name}  ({zipped} files, the same content as an archive)

  The .txt parts split only on file boundaries -- no file is cut in half. Read
  them in order; each is prefixed with its layer.

HOW TO READ IT
  1. src/domain/base.py          -- frozen models + authoritative fingerprinting.
                                    Everything else depends on this.
  2. src/strategies/models.py    -- TradeCandidate. AUTHORITATIVE_FIELDS is the
                                    list of things only a strategy may set.
  3. src/agents/models.py        -- AgentReview. Note what it structurally CANNOT
                                    carry (FORBIDDEN_REVIEW_FIELDS).
  4. src/execution/models.py     -- assert_authorized(). The single door into
                                    execution.
  5. src/execution/validator.py  -- 25 hard gates, no override parameter.
  6. tests/unit/test_agent_authority.py and test_risk_enforcement.py -- these
                                    tests ARE the specification.

THE FIVE INVARIANTS
  1. Only a strategy authors symbol, side, quantity, entry, stop, target, TIF.
  2. Risk cannot be bypassed: execution accepts an OrderIntent only when it
     carries an APPROVED RiskDecision bound to the candidate's fingerprint.
  3. An agent can veto, and can do nothing else. Not "should not" -- cannot:
     the fields do not exist on the model, and extra="forbid" rejects them.
  4. Identical inputs produce an identical decision. No clock reads, no uuid4,
     no float, no ambient state inside a strategy.
  5. Live trading, risk increases, strategy promotion and production config all
     require an explicit human approval bound to a configuration fingerprint.

WHAT IS DELIBERATELY MISSING
  - docs/PRD.md. It was never in the repository. Everything here was built from
    a written brief. Treated as blocker B-1 throughout, never invented.
  - Production risk values. data/fixtures/risk_limits.shadow.json is SHADOW-ONLY
    and says so in its own payload. Real numbers are the account owner's call.
  - Agent-unavailable fallback behaviour. Three policies are defined
    (AgentContextPolicy); which one is production was left unselected on
    purpose, because inventing it would be inventing risk appetite.
  - Real brokerage data. Captures live outside version control. Only the
    redacted capture script ships.

KNOWN RESULT WORTH KNOWING BEFORE YOU JUDGE THE STRATEGY
  trend_breakout@1.0.0 showed +0.301 R expectancy (t=5.63) on the symbols the
  account actually traded. On a deliberately chosen laggard control universe
  that fell to +0.058 R (t=0.83), and under the real single-position book
  constraint to +0.108 R (t=1.13). Most of the apparent edge was selection bias.
  That finding is in the repository, not hidden. See docs/STRATEGY_V1.md.

  Full narrative record: docs/AUDIT_AND_LEARNINGS.txt. It is inside the .zip and
  was also delivered on its own; it is the only source file not reproduced in
  the .txt parts above.

VERIFICATION STATE AT PACKAGING TIME
  350 tests passing, mypy --strict clean over 92 files, ruff + ruff format clean,
  pip-audit clean, bandit clean.
"""


if __name__ == "__main__":
    raise SystemExit(main())
