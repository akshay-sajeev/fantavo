import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatValueGap, valueGapClassName } from "@/components/draft/grade-visuals";
import type { DraftPickGrade, TeamDraftAutopsy } from "@/lib/types";

/** Reorganizes the already-fetched flat per-team pick lists into a
 * round x team lookup -- pure regrouping of data the API already returned
 * (same class of operation as sorting an already-returned array, see
 * docs/decisions.md Phase 5b's standings-sort precedent), not a new
 * derived number. */
function buildRoundTeamIndex(
  teams: TeamDraftAutopsy[],
): { maxRound: number; byKey: Map<string, DraftPickGrade> } {
  const byKey = new Map<string, DraftPickGrade>();
  let maxRound = 0;
  for (const team of teams) {
    for (const pick of team.picks) {
      byKey.set(`${pick.round_id}:${pick.team_id}`, pick);
      if (pick.round_id > maxRound) maxRound = pick.round_id;
    }
  }
  return { maxRound, byKey };
}

function DraftBoardCell({ pick }: { pick: DraftPickGrade | undefined }) {
  if (!pick) {
    return <TableCell className="text-center text-muted-foreground">—</TableCell>;
  }
  return (
    <TableCell className="min-w-[9.5rem] align-top">
      <div className="flex flex-col gap-0.5">
        <span className="text-xs font-medium text-foreground">{pick.player_name}</span>
        <span className="text-[11px] text-muted-foreground">
          {pick.position} · {pick.slot_label} · pick {pick.overall_pick_number}
        </span>
        <span className={`text-[11px] font-semibold tabular-nums ${valueGapClassName(pick.value_gap)}`}>
          {formatValueGap(pick.value_gap)}
        </span>
      </div>
    </TableCell>
  );
}

/**
 * The full league draft board: every real pick, laid out round-by-round
 * across every team, with each cell's value_gap visible directly (never
 * hover-only, per MASTER.md's chart a11y guidance). This is the single
 * "per-pick grading" view PLAN.md asks for -- kept as one league-wide
 * artifact below the per-team narrative cards, rather than repeated once
 * per team, so it never crowds out the structural finding above it.
 */
export function DraftBoardGrid({ teams }: { teams: TeamDraftAutopsy[] }) {
  const { maxRound, byKey } = buildRoundTeamIndex(teams);
  const rounds = Array.from({ length: maxRound }, (_, i) => i + 1);

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="sticky left-0 z-10 bg-card">Round</TableHead>
            {teams.map((team) => (
              <TableHead key={team.team_id} className="min-w-[9.5rem]">
                {team.team_name}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rounds.map((round) => (
            <TableRow key={round}>
              <TableCell className="sticky left-0 z-10 bg-card font-medium text-muted-foreground">
                {round}
              </TableCell>
              {teams.map((team) => (
                <DraftBoardCell key={team.team_id} pick={byKey.get(`${round}:${team.team_id}`)} />
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
