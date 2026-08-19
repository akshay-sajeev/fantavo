"""Confirms api/index.py (the Vercel serverless entry point -- see
docs/decisions.md's Vercel Serverless Migration entry) actually exposes the
real FastAPI app object Vercel's Python builder needs, not a stale copy or
a re-instantiated app that would silently drop all routes, dependencies,
and the lifespan wiring."""

from __future__ import annotations

from api.index import app as vercel_app
from sim.api.app import app as real_app


def test_vercel_entrypoint_exposes_the_real_app_object() -> None:
    assert vercel_app is real_app
