import { formatPercent } from "@/lib/format";
import type { SlotPlayoffStrength } from "@/lib/types";

/**
 * One starting slot's floor ratio, full season vs. the compressed playoff
 * window, as two stacked bars on a shared 0-100% scale -- the visual
 * equivalent of the sentence "over a full season this floor holds at X%,
 * but in the playoffs it drops to Y%." The playoff bar is destructive-
 * colored only when the API already flagged this exact slot as a genuine
 * playoff-specific weakness (`is_playoff_specific_weakness`); the ratio
 * math itself is nearly identical across every slot in the league (see
 * sim.api.playoff_planner_view's module docstring), so color is reserved
 * for the one case that's actually team-specific: this slot also having no
 * same-position bench depth. The "no bench depth" note below only ever
 * shows when `bench_depth_relevant` is also true -- K and D/ST are
 * single-slot positions a real roster normally carries exactly one of, so
 * having no backup there is normal, not a note-worthy gap.
 */
export function SlotStrengthBar({ slot }: { slot: SlotPlayoffStrength }) {
  const isWeak = slot.is_playoff_specific_weakness;

  return (
    <div className="flex flex-col gap-1 py-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold text-foreground">{slot.slot_label}</span>
        <span className="tabular-nums text-muted-foreground">
          Rank {slot.league_rank}/{slot.league_team_count} for the playoff weeks
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className="w-14 shrink-0 text-[11px] text-muted-foreground">Full season</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-secondary"
            style={{ width: `${slot.regular_floor_ratio * 100}%` }}
          />
        </div>
        <span className="w-10 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
          {formatPercent(slot.regular_floor_ratio, 0)}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className="w-14 shrink-0 text-[11px] text-muted-foreground">Playoffs</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full rounded-full ${isWeak ? "bg-destructive" : "bg-primary"}`}
            style={{ width: `${slot.playoff_floor_ratio * 100}%` }}
          />
        </div>
        <span
          className={`w-10 shrink-0 text-right text-[11px] font-medium tabular-nums ${
            isWeak ? "text-destructive" : "text-muted-foreground"
          }`}
        >
          {formatPercent(slot.playoff_floor_ratio, 0)}
        </span>
      </div>

      {slot.bench_depth_relevant && !slot.has_bench_depth && (
        <p className="text-[11px] text-muted-foreground">No same-position bench depth</p>
      )}
    </div>
  );
}
