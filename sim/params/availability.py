"""Per-player weekly availability, derived from ESPN's own projected games count.

`PlayerProjection.games_projected` (see `ingest/models.py`) is not invented --
Phase 1 recovered it by inverting ESPN's own `appliedTotal / appliedAverage`
for a player's season projection block. It already encodes ESPN's own view of
how many games this player will actually play this season, folding in bye
weeks, established injury risk, and backup/timeshare roles. This module turns
that games count into a per-week probability of being active:

    availability = games_projected / NFL_REGULAR_SEASON_GAMES

`NFL_REGULAR_SEASON_GAMES = 17` is a structural fact about the season being
modeled (every NFL team has played a 17-game regular season since 2021), not
a fitted statistic or a guessed placeholder. It is cross-checked directly
against this fixture in sim/tests/test_params_availability.py: the
overwhelming majority of usable players in the pool have `games_projected ==
17` (ESPN's own projected ceiling), and the handful of exceptions are players
ESPN itself projects to miss games -- consistent with 17 being the right
denominator, not a coincidence borrowed from the fantasy league's own
`matchupPeriodCount` (docs/decisions.md flagged that exact conflation risk in
Phase 1 and deliberately left it unresolved for this module to settle).
"""

from __future__ import annotations

NFL_REGULAR_SEASON_GAMES = 17


def derive_availability(games_projected: int) -> float:
    """Convert a projected season games count into a per-week availability
    probability in (0, 1].

    Raises rather than clips if `games_projected` exceeds the season length
    -- that would mean either NFL_REGULAR_SEASON_GAMES is wrong for the
    season being modeled, or upstream data is corrupt, and either way this
    should fail loudly instead of silently capping at 1.0.
    """
    if games_projected <= 0:
        raise ValueError(f"games_projected must be > 0, got {games_projected}")
    if games_projected > NFL_REGULAR_SEASON_GAMES:
        raise ValueError(
            f"games_projected ({games_projected}) exceeds "
            f"NFL_REGULAR_SEASON_GAMES ({NFL_REGULAR_SEASON_GAMES}) -- this "
            "indicates a wrong denominator or corrupt upstream data, not a "
            "value to silently clip"
        )
    return games_projected / NFL_REGULAR_SEASON_GAMES
