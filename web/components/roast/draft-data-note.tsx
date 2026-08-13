import { Info } from "lucide-react";

/**
 * Shown only when `has_draft_data` is false (see
 * sim.api.roast_view.PowerRankingRoastResult / docs/decisions.md Phase 12)
 * -- same "surface the caveat in the product, not just the docs" discipline
 * `components/waivers/ownership-note.tsx` already established.
 */
export function DraftDataNote() {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
      <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <p className="leading-relaxed">
        This league has no completed draft to grade yet, so these roasts skip the draft-pick jokes.
        Everything else -- simulated title-odds rank, real bench-depth weaknesses, real rival threats
        -- is still real.
      </p>
    </div>
  );
}
