import { TrendingUp } from "lucide-react";
import type { AnalystCitation } from "@/lib/types";

/**
 * Renders one cited number as a real inline component, not a number
 * embedded in a paragraph of prose (PLAN.md's own words for this feature:
 * "When the analyst mentions a probability, show it."). The number itself
 * (`citation.display`) is always plainly visible -- never hover-only, per
 * MASTER.md's compact-label accessibility guidance -- the `title` attribute
 * only adds supplementary context (which real tool produced this number),
 * not the number itself.
 */
export function StatChip({ citation }: { citation: AnalystCitation }) {
  return (
    <span
      className="mx-0.5 inline-flex shrink-0 items-center gap-1 rounded-full border border-brand-accent/40 bg-brand-accent/15 px-1.5 py-0.5 align-baseline text-[0.8em] leading-none font-semibold text-brand-accent-foreground"
      title={`${citation.subject} — ${citation.kind.replace(/_/g, " ")} (from ${citation.source_tool})`}
    >
      <TrendingUp className="h-3 w-3 shrink-0" aria-hidden="true" />
      {citation.display}
    </span>
  );
}
