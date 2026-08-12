"""Projection parameters: the mean -> (mean, sd, availability) mapping from
ingest.models.PlayerProjection into sim.engine.PlayerParams.

Phase 1 deliberately stopped at PlayerProjection.mean_points_per_game (a real,
ESPN-sourced number) and left sd/availability to this package, per
docs/decisions.md's "STOP decision (sd / availability)". See:

- sim/params/variance.py    -- fits a position-level coefficient of
                                variation from real historical data.
- sim/params/availability.py -- derives per-player availability from ESPN's
                                own projected games count.
- sim/params/derive.py      -- combines both into PlayerParams.
- sim/params/mock_rosters.py -- SYNTHETIC team groupings for pipeline
                                validation only (this league is pre-draft
                                and has no real rosters yet).
- sim/params/validate.py    -- `python -m sim.params.validate`, a printed
                                sanity check against the mock league.
"""

from __future__ import annotations
