import { Repeat, ShieldCheck, Target } from "lucide-react";
import { CandidateRow } from "@/components/waivers/candidate-row";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { WaiverPositionGroup } from "@/lib/types";

/**
 * One position's ranked waiver targets for this team. The group_reasoning
 * sentence (League fit + Competition, server-synthesized) leads as a
 * headline callout -- the same "narrative leads, supporting data follows"
 * layout Draft Autopsy's StructuralFindingCard and Playoff Planner's
 * RecommendationCallout already established for this app -- styled
 * distinctly by what it's actually saying:
 *   - a real positional need -> brand-accent "act now" treatment, matching
 *     RecommendationCallout's own convention for an actionable finding
 *   - real bench-depth data but no current need -> a quieter primary tone
 *   - K/D-ST (bench depth isn't a real decision here) -> muted, framed as
 *     streaming options rather than a roster need
 * Ranked candidates (already sorted by expected_playable_points, the one
 * honest apples-to-apples number WITHIN a position -- see
 * sim.api.waiver_intelligence_view's module docstring for why never across
 * positions) follow below.
 */
export function PositionGroupCard({ group }: { group: WaiverPositionGroup }) {
  const tone = !group.bench_depth_relevant
    ? "streaming"
    : group.team_has_positional_need
      ? "need"
      : "stocked";

  const Icon = tone === "need" ? Target : tone === "streaming" ? Repeat : ShieldCheck;
  const calloutClassName =
    tone === "need"
      ? "border-l-4 border-l-brand-accent bg-brand-accent/5"
      : tone === "streaming"
        ? "border-l-4 border-l-border bg-muted/30"
        : "border-l-4 border-l-primary/40 bg-primary/5";
  const labelClassName =
    tone === "need"
      ? "text-brand-accent-foreground"
      : tone === "streaming"
        ? "text-muted-foreground"
        : "text-primary";
  const label = tone === "need" ? "Real need" : tone === "streaming" ? "Streaming options" : "Depth add";

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0">
        <CardTitle className="font-heading text-base">{group.position}</CardTitle>
        <span
          className={`shrink-0 text-xs font-semibold tracking-wide uppercase ${labelClassName}`}
        >
          {label}
        </span>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className={`flex items-start gap-2.5 rounded-lg p-3 ${calloutClassName}`}>
          <Icon
            className={`mt-0.5 h-4 w-4 shrink-0 ${labelClassName}`}
            aria-hidden="true"
          />
          <p className="text-sm leading-relaxed font-medium text-foreground">
            {group.group_reasoning}
          </p>
        </div>

        {group.candidates.length > 0 ? (
          <ul className="divide-y divide-border">
            {group.candidates.map((candidate, i) => (
              <CandidateRow key={candidate.player_id} candidate={candidate} rank={i + 1} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            No usable free-agent candidates at {group.position} right now.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
