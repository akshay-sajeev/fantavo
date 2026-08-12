"""Exceptions raised by the ingest package.

Per CLAUDE.md's "no invented numbers" rule: when a value the engine needs is
genuinely absent from the fixture, ingest raises one of these rather than
substituting a plausible default.
"""

from __future__ import annotations


class IngestError(Exception):
    """Base class for all ingest failures."""


class MissingProjectionError(IngestError):
    """A player has no usable season projection in the fixture.

    Covers two distinct cases, both surfaced with a specific reason: the
    projection stat block (statSourceId=1, statSplitTypeId=0, matching
    seasonId) is entirely absent, or it is present but its appliedTotal is
    zero (e.g. a player projected for zero games played).
    """


class RosterNotAvailableError(IngestError):
    """A team has no roster to build TeamParams.starters from.

    Raised instead of fabricating a lineup from the free-agent pool or ADP.
    A common, legitimate cause: the fixture was captured before the league's
    draft (draftDetail.drafted is False), in which case every team's
    roster.entries is empty.
    """
