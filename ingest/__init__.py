"""ESPN fixture ingest: parse saved ESPN payloads into engine-ready params,
and persist them to Postgres (ingest/db.py).

This package never makes a network call to ESPN. Everything here reads from
/fixtures. The only script allowed to touch the ESPN API is
scripts/fetch_fixture.py. ingest/db.py does talk to a local Postgres
instance -- that's local infra, not the ESPN API, and is unaffected by that
rule.
"""

from __future__ import annotations
