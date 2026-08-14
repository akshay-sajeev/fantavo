import type { LucideIcon } from "lucide-react";
import { ArrowLeftRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RangeBar } from "@/components/risk/range-bar";
import { FinishDistributionStrip } from "@/components/shared/finish-distribution-strip";
import { formatPercent, formatPoints } from "@/lib/format";
import type { LineupProjection } from "@/lib/types";

/**
 * One of the three lineups (Current / Safest / Highest upside), laid out
 * narrative-first the same way this app's other synthesized-finding cards
 * do (StructuralFindingCard, RecommendationCallout): the "why pick this"
 * one-liner leads, then the weekly range and season odds -- distributions,
 * never bare point estimates, per design-system/MASTER.md -- and the full
 * roster last, with swapped slots highlighted so the difference from
 * Current is legible without re-reading every row.
 */
export function LineupCard({
  projection,
  icon: Icon,
  accentClassName,
  subtitle,
  scaleMax,
}: {
  projection: LineupProjection;
  icon: LucideIcon;
  /** Tailwind classes for the label badge -- distinguishes the three cards
   * at a glance without relying on position alone. */
  accentClassName: string;
  /** One-line "why you'd pick this" sentence, server-derived numbers only. */
  subtitle: string;
  /** Shared weekly-points scale across all three cards in the row, so bar
   * lengths are directly comparable card to card (same convention
   * RangeBar's own docstring establishes for a single team's roster). */
  scaleMax: number;
}) {
  const swapCount = projection.assignments.filter((a) => a.is_swap).length;

  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="font-heading flex items-center gap-2 text-base">
            <span
              className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${accentClassName}`}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
            </span>
            {projection.label}
          </CardTitle>
          {swapCount > 0 && (
            <Badge variant="outline" className="gap-1 border-border text-muted-foreground">
              <ArrowLeftRight className="h-3 w-3" aria-hidden="true" />
              {swapCount} swap{swapCount > 1 ? "s" : ""}
            </Badge>
          )}
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">{subtitle}</p>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-4">
        <div>
          <div className="mb-1 flex items-center justify-between text-xs">
            <span className="font-medium text-foreground">Projected weekly score</span>
            <span className="tabular-nums text-muted-foreground">
              mean {formatPoints(projection.weekly_mean)}
            </span>
          </div>
          <RangeBar
            floor={projection.weekly_floor}
            mean={projection.weekly_mean}
            ceiling={projection.weekly_ceiling}
            scaleMax={scaleMax}
          />
        </div>

        <div className="space-y-1.5 rounded-lg bg-muted/60 p-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Title odds</span>
            <span className="font-semibold tabular-nums text-foreground">
              {formatPercent(projection.title_probability)}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Playoff odds</span>
            <span className="font-semibold tabular-nums text-foreground">
              {formatPercent(projection.playoff_probability)}
            </span>
          </div>
          <FinishDistributionStrip
            finishDistribution={projection.finish_distribution}
            className="pt-1"
          />
        </div>

        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Slot</TableHead>
                <TableHead>Player</TableHead>
                <TableHead className="text-right">Pos</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {projection.assignments.map((assignment) => (
                <TableRow
                  key={`${assignment.slot_label}-${assignment.player_id}`}
                  className={assignment.is_swap ? "bg-brand-accent/10" : undefined}
                >
                  <TableCell className="font-medium text-muted-foreground">
                    {assignment.slot_label}
                  </TableCell>
                  <TableCell
                    className={assignment.is_swap ? "font-semibold text-foreground" : undefined}
                  >
                    {assignment.player_name}
                    {assignment.is_swap && (
                      <ArrowLeftRight
                        className="ml-1.5 inline-block h-3 w-3 text-brand-accent"
                        aria-hidden="true"
                      />
                    )}
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground">
                    {assignment.position}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
