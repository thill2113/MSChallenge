# `data/fixtures` — test data

Small, hand-authored inputs used by the test suite and by local development.

**Every number here is fake.** In particular, `risk_limits.example.json` is a
shape reference, not a recommendation. No production risk values exist anywhere
in this repository — choosing them is a human decision recorded as open decision
D-1 in `docs/PHASE_1_STATUS.md`.

Fixtures derived from real captures must be copied out of `data/raw` **with any
account identifiers removed**, and the source digest recorded in the fixture so
the lineage stays traceable.
