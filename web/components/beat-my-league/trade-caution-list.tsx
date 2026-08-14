import { HandCoins } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { TradeCaution } from "@/lib/types";

/**
 * League-specific trade strategy: which positions NOT to help a rival fix
 * -- the brand-accent (amber) treatment `RecommendationCallout` already
 * uses for an actionable note, per PLAN.md's "lead with the action."
 * Real, honest empty state when `cautions` is empty (no rival currently
 * needs a position this team has real surplus at) -- never a fabricated
 * warning to fill the space.
 */
export function TradeCautionList({ cautions }: { cautions: TradeCaution[] }) {
  if (cautions.length === 0) {
    return (
      <Card className="border-l-4 border-l-brand-accent bg-brand-accent/5">
        <CardContent className="flex items-start gap-2.5">
          <HandCoins
            className="mt-0.5 h-4 w-4 shrink-0 text-brand-accent"
            aria-hidden="true"
          />
          <div className="space-y-1">
            <p className="text-xs font-semibold tracking-wide text-brand-accent uppercase">
              Trade strategy
            </p>
            <p className="text-sm leading-relaxed text-foreground/90">
              No rival in the league currently has a real bench-depth need that lines up with
              spare depth on this roster -- no specific &ldquo;don&apos;t trade this away&rdquo;
              leverage to flag right now.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-2">
      {cautions.map((caution) => (
        <Card key={caution.position} className="border-l-4 border-l-brand-accent bg-brand-accent/5">
          <CardContent className="flex items-start gap-2.5">
            <HandCoins
              className="mt-0.5 h-4 w-4 shrink-0 text-brand-accent"
              aria-hidden="true"
            />
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <p className="text-xs font-semibold tracking-wide text-brand-accent uppercase">
                  Don&apos;t trade away your {caution.position} depth
                </p>
                <Badge variant="outline" className="text-[10px]">
                  {caution.team_bench_depth_at_position} bench {caution.position}
                  {caution.team_bench_depth_at_position !== 1 ? "s" : ""}
                </Badge>
              </div>
              <p className="text-sm leading-relaxed text-foreground/90">{caution.reasoning}</p>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
