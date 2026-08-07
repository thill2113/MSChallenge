# `data/normalized` — derived output

Regenerable artifacts produced by importing `data/raw` into the trade ledger.

**Git-ignored on purpose.** Anything here can be rebuilt from `data/raw` plus
the importer code at a known commit. If something in this directory cannot be
regenerated, it is misplaced — it belongs in `data/raw` (if it is evidence) or
in the database (if it is ledger state).

Deleting this directory's contents must never lose information.
