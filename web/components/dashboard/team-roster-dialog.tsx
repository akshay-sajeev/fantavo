import { ChevronRight } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { formatPoints } from "@/lib/format";
import type { RosterPlayer, TeamRoster } from "@/lib/types";

/**
 * Full roster for one team, as a modal: starters and bench together, which
 * the dashboard card itself has no room for. Per-player `mean` is a
 * per-game projection and is labeled "pts/gm" to match how the same field
 * is already rendered in the what-if player picker -- never re-derived here.
 *
 * A bench player can legitimately have `has_projection: false` and a null
 * `mean` (see RosterPlayer's docstring); those render an em dash rather
 * than a fabricated 0. Starters always have one.
 *
 * Deliberately uncontrolled -- base-ui owns the open state, so this needs
 * no hooks and stays a Server Component, which is what lets RostersGrid
 * remain one too.
 */
function PlayerRow({ player }: { player: RosterPlayer }) {
  return (
    <li className="flex items-center justify-between gap-3 py-1.5 text-sm">
      <span className="min-w-0 truncate">{player.name}</span>
      <span className="flex shrink-0 items-center gap-3">
        <span className="w-14 text-right text-xs text-muted-foreground">
          {player.lineup_slot}
        </span>
        <span className="w-24 text-right tabular-nums text-muted-foreground">
          {player.mean != null ? `${formatPoints(player.mean)} pts/gm` : "—"}
        </span>
      </span>
    </li>
  );
}

function RosterSection({
  label,
  players,
  emptyLabel,
}: {
  label: string;
  players: RosterPlayer[];
  emptyLabel: string;
}) {
  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {label}
        <span className="ml-2 font-normal normal-case">{players.length}</span>
      </h3>
      {players.length === 0 ? (
        <p className="py-1.5 text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <ul className="divide-y divide-border/70">
          {players.map((p) => (
            <PlayerRow key={p.player_id} player={p} />
          ))}
        </ul>
      )}
    </section>
  );
}

export function TeamRosterDialog({ team }: { team: TeamRoster }) {
  return (
    <Dialog>
      <DialogTrigger className="mt-2 flex w-full items-center justify-between gap-2 rounded-md border border-border/70 px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
        View Full Roster
        <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      </DialogTrigger>
      <DialogContent>
        <DialogTitle className="pr-8">{team.team_name}</DialogTitle>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          <RosterSection
            label="Starters"
            players={team.starters}
            emptyLabel="No roster ingested yet."
          />
          <RosterSection
            label="Bench"
            players={team.bench}
            emptyLabel="No bench players."
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
