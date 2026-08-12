"""ESPN fixture ingest: parse saved ESPN payloads into engine-ready params.

This package never makes a network call. Everything here reads from
/fixtures. The only script allowed to touch the ESPN API is
scripts/fetch_fixture.py.
"""

from __future__ import annotations
