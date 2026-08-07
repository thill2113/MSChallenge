"""Immutable raw evidence store (ADR-003).

Files under ``data/raw/`` are content-addressed and write-once. The store will
not overwrite an existing document, and re-storing identical bytes is a no-op
that returns the original record. If a digest exists with different content, the
store raises rather than picking a winner: two different answers to the same
question is a fact worth surfacing, not a conflict to resolve silently.

Normalisation writes to ``data/normalized/``. Nothing in this module writes
there, and nothing that reads raw evidence is permitted to edit it in place.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from pydantic import Field

from domain.base import FrozenModel
from domain.enums import SourceSystem
from domain.errors import ImmutableEvidenceError
from domain.values import TimestampUTC

READ_ONLY_MODE: Final[int] = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
"""0o444 — readable by all, writable by none."""

CONTENT_TYPE_SUFFIXES: Final[dict[str, str]] = {
    "application/json": ".json",
    "application/jsonl": ".jsonl",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/octet-stream": ".bin",
}


def sha256_hex(content: bytes) -> str:
    """Content digest used as the address of a raw document."""
    return hashlib.sha256(content).hexdigest()


class StoredDocument(FrozenModel):
    """A document that exists immutably on disk."""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_system: SourceSystem
    relative_path: str
    content_type: str
    byte_size: int = Field(ge=0)
    captured_at: TimestampUTC | None = None


class RawDocumentStore:
    """Write-once, content-addressed storage for source evidence."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """Directory holding the evidence tree."""
        return self._root

    def path_for(self, source_system: SourceSystem, digest: str, content_type: str) -> Path:
        """Location of a document, derived entirely from its identity."""
        suffix = CONTENT_TYPE_SUFFIXES.get(content_type, ".bin")
        return self._root / source_system.value.lower() / f"{digest}{suffix}"

    def store(
        self,
        content: bytes,
        *,
        source_system: SourceSystem,
        content_type: str = "application/json",
        captured_at: TimestampUTC | None = None,
    ) -> StoredDocument:
        """Persist ``content`` immutably and return its record.

        Storing the same bytes twice is idempotent. Storing different bytes
        under an existing digest is impossible by construction, so the only
        failure this raises is a corrupted or hand-edited store.
        """
        digest = sha256_hex(content)
        target = self.path_for(source_system, digest, content_type)

        if target.exists():
            existing = target.read_bytes()
            if existing != content:
                raise ImmutableEvidenceError(
                    f"{target} already exists with different content; raw evidence is "
                    "write-once (ADR-003)"
                )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temporary sibling then move, so a crash mid-write cannot
            # leave a truncated file sitting at a valid digest path.
            staging = target.with_name(f".{digest}.partial")
            staging.write_bytes(content)
            staging.replace(target)
            target.chmod(READ_ONLY_MODE)

        return StoredDocument(
            sha256=digest,
            source_system=source_system,
            relative_path=str(target.relative_to(self._root)),
            content_type=content_type,
            byte_size=len(content),
            captured_at=captured_at,
        )

    def read(self, source_system: SourceSystem, digest: str, content_type: str) -> bytes:
        """Return stored bytes, verifying they still match their address."""
        target = self.path_for(source_system, digest, content_type)
        if not target.exists():
            raise FileNotFoundError(f"no raw document {digest} for {source_system}")
        content = target.read_bytes()
        actual = sha256_hex(content)
        if actual != digest:
            raise ImmutableEvidenceError(
                f"{target} has been modified: content digest is {actual}, expected {digest}"
            )
        return content

    def iter_documents(self) -> Iterator[Path]:
        """Every stored document, in a stable order."""
        if not self._root.exists():
            return
        yield from sorted(p for p in self._root.rglob("*") if p.is_file() and p.name != ".gitkeep")

    def verify(self) -> tuple[Path, ...]:
        """Return every stored file whose content no longer matches its name.

        Run this in CI and after any manual touch of ``data/raw``. An empty
        result is the only acceptable outcome.
        """
        corrupted: list[Path] = []
        for path in self.iter_documents():
            expected = path.stem
            if len(expected) != 64:
                continue
            if sha256_hex(path.read_bytes()) != expected:
                corrupted.append(path)
        return tuple(corrupted)

    def assert_read_only(self) -> tuple[Path, ...]:
        """Return stored files that are still writable by someone."""
        writable: list[Path] = []
        for path in self.iter_documents():
            mode = path.stat().st_mode
            if mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                writable.append(path)
        return tuple(writable)

    def harden(self) -> int:
        """Re-apply read-only permissions to every stored document."""
        count = 0
        for path in self.iter_documents():
            if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                path.chmod(READ_ONLY_MODE)
                count += 1
        return count
