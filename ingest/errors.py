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


class DraftNotAvailableError(IngestError):
    """A league has no completed draft to autopsy.

    Covers two distinct, legitimate cases: a genuinely pre-draft league
    (draftDetail.drafted is False), and the SYNTHETIC validation league
    (scripts/ingest_synthetic_league.py), whose mock draft fabricates
    rosters directly onto teams without ever recording a pick sequence --
    draftDetail.picks is an empty list there by construction, not a bug.
    Raised instead of inventing a pick order from roster composition.
    """


class MissingRankDataError(IngestError):
    """A player referenced by a real draft pick, or considered as a
    same-position/board-wide alternative to one, has no usable ESPN
    consensus rank (draftRanksByRankType.PPR.rank) in the fixture.

    Raised rather than silently excluding that player from the "who else
    was available" comparison draft-autopsy grading depends on -- quietly
    dropping one player would change every other pick's alternative and
    value_gap around them without any visible signal that data was missing.
    """
