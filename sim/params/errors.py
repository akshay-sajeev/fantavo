"""Exceptions raised by sim.params.

Per CLAUDE.md's "no invented numbers" rule: when a parameter this package
needs is genuinely absent or the sample backing it is too small to trust,
raise one of these rather than substituting a default or a guessed constant.
"""

from __future__ import annotations


class ParamsError(Exception):
    """Base class for all sim.params failures."""


class InsufficientHistoricalDataError(ParamsError):
    """A position has too few historical samples to fit a coefficient of
    variation from real data.

    Raised instead of falling back to a hardcoded or borrowed CV -- see
    sim/params/variance.py for the fitting methodology and rationale.
    """


class MissingVarianceParameterError(ParamsError):
    """A player's position has no fitted coefficient of variation.

    This should not happen for any position present in
    ingest.slots.DEFAULT_POSITION_TO_SLOT once fit_position_cv has run
    successfully over the fixture, but derive_player_params checks and
    raises explicitly rather than silently producing an sd of 0 or None.
    """
