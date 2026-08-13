import {
  AlertTriangle,
  ClipboardList,
  Layers,
  Sparkles,
  Swords,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import type { RoastFact } from "@/lib/types";

/**
 * Every roast sentence has a real fact behind it (`sim.api.roast_view`) --
 * this is where those receipts get shown, one row per fact, never
 * truncated. Per the ui-ux-pro-max UX guidance on compact labels ("preserve
 * labels ... don't clip with a hover-only tooltip"), a fact's full citation
 * text is always fully visible rather than an ellipsis + title-attribute
 * tooltip, since the citation IS the content that makes the joke land.
 */
const KIND_META: Record<string, { label: string; icon: LucideIcon }> = {
  title_rank: { label: "Title odds", icon: TrendingUp },
  draft_reach: { label: "Draft reach", icon: AlertTriangle },
  draft_best_pick: { label: "Draft steal", icon: Sparkles },
  draft_structural: { label: "Draft notes", icon: ClipboardList },
  bench_depth: { label: "Bench depth", icon: Layers },
  rival_threat: { label: "Rival watch", icon: Swords },
};

export function RoastFacts({ facts }: { facts: RoastFact[] }) {
  if (facts.length === 0) return null;
  return (
    <div className="space-y-1.5 rounded-lg border border-border/70 bg-muted/30 p-2.5">
      <p className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
        The receipts
      </p>
      <div className="space-y-1">
        {facts.map((fact, i) => {
          const meta = KIND_META[fact.kind] ?? { label: fact.kind, icon: ClipboardList };
          const Icon = meta.icon;
          return (
            <div key={`${fact.kind}-${i}`} className="flex items-start gap-1.5 text-[11px] leading-snug">
              <Icon className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
              <p className="text-muted-foreground">
                <span className="font-medium text-foreground/80">{meta.label}:</span> {fact.text}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
