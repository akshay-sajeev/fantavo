import { Badge } from "@/components/ui/badge";
import { formatPoints } from "@/lib/format";
import type { RosterPlayer } from "@/lib/types";

/**
 * A checklist of a team's players (starters for a trade, or starters+bench
 * for a roster swap) the user toggles on to send away / bench / start.
 * Presentation only -- selection state and the resulting roster_overrides
 * list are computed by the parent client component, never here.
 */
export function PlayerPicker({
  players,
  selected,
  onToggle,
  emptyLabel,
}: {
  players: RosterPlayer[];
  selected: number[];
  onToggle: (playerId: number) => void;
  emptyLabel?: string;
}) {
  if (players.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyLabel ?? "No players."}</p>;
  }

  return (
    <ul className="space-y-1">
      {players.map((player) => {
        const isSelected = selected.includes(player.player_id);
        // A player with no usable ESPN projection can't be validly fed into
        // a live simulation (the API rejects it with a clear 422 if tried
        // anyway -- see sim/api/app.py's post_whatif) -- disabled here
        // rather than left selectable into a submit-time error.
        const disabled = !player.has_projection;
        return (
          <li key={player.player_id}>
            <button
              type="button"
              aria-pressed={isSelected}
              disabled={disabled}
              title={disabled ? "No ESPN projection available for this player" : undefined}
              onClick={() => onToggle(player.player_id)}
              className={
                "flex w-full items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-left text-sm transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring " +
                (disabled
                  ? "cursor-not-allowed border-border bg-muted/30 opacity-60"
                  : "cursor-pointer " +
                    (isSelected
                      ? "border-primary bg-primary/10"
                      : "border-border hover:bg-muted/60"))
              }
            >
              <span className="flex min-w-0 items-center gap-2">
                <span
                  aria-hidden="true"
                  className={
                    "flex h-4 w-4 shrink-0 items-center justify-center rounded border " +
                    (isSelected
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-background")
                  }
                >
                  {isSelected && (
                    <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" aria-hidden="true">
                      <path
                        d="M3.5 8.5l3 3 6-7"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </span>
                <span className="truncate font-medium">{player.name}</span>
                <Badge variant="outline" className="shrink-0">
                  {player.position}
                </Badge>
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {player.has_projection && player.mean !== null
                  ? `${formatPoints(player.mean)} pts/gm`
                  : "No projection"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
