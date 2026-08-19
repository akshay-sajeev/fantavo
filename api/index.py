"""Vercel serverless entry point for the /sim FastAPI service. Vercel's
Python builder is documented to look for a WSGI/ASGI `app` object at
exactly this path (api/index.py, repo root -- matching where
requirements.txt/pyproject.toml already sit for the same build to install
from). See docs/decisions.md's Vercel Serverless Migration entry for the
full reasoning, and its "known gaps" note: this exact file path and
vercel.json's "functions" key shape are best-effort against Vercel's
documented conventions, not verified against a real deploy.

Deliberately a plain re-export, not a re-instantiated FastAPI() app --
every route, dependency, and the conditional lifespan() defined in
sim/api/app.py must be reused completely unmodified."""

from __future__ import annotations

from sim.api.app import app

__all__ = ["app"]
