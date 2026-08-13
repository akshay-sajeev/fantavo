import { ShieldCheck } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { TeamAdvantage } from "@/lib/types";

/**
 * The mirror-image of ThreatCard -- primary-tinted (the same treatment
 * `StructuralFindingCard` already uses for a positive/informational
 * finding), placed directly alongside the threat so the head-to-head
 * comparison reads as one coherent pair, per PLAN.md's "the user's own
 * advantage... surfaced clearly." Reasoning is entirely server-computed.
 */
export function AdvantageCard({ advantage }: { advantage: TeamAdvantage }) {
  return (
    <Card className="border-l-4 border-l-primary bg-primary/5">
      <CardContent className="space-y-2">
        <div className="flex items-center gap-1.5">
          <ShieldCheck className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <p className="text-xs font-semibold tracking-wide text-primary uppercase">
            Your advantage
          </p>
        </div>
        <p className="text-sm leading-relaxed text-foreground/90">{advantage.reasoning}</p>
      </CardContent>
    </Card>
  );
}
