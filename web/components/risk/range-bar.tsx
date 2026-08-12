import { formatPoints } from "@/lib/format";

/**
 * Floor/mean/ceiling as a range on a shared scale, per MASTER.md's
 * "Confidence ranges... Line with Confidence Band" guidance adapted to a
 * compact per-row bar (a full line+band chart per player would be far too
 * tall at dashboard density for a roster table). `scaleMax` is passed by
 * the caller (the team's own highest ceiling) so every row in one team's
 * table shares one scale and bar lengths are directly comparable.
 */
export function RangeBar({
  floor,
  mean,
  ceiling,
  scaleMax,
}: {
  floor: number;
  mean: number;
  ceiling: number;
  scaleMax: number;
}) {
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / scaleMax) * 100))}%`;

  return (
    <div className="flex min-w-[9rem] flex-col gap-1">
      <div className="relative h-2 w-full rounded-full bg-muted">
        <div
          className="absolute h-2 rounded-full bg-secondary"
          style={{ left: pct(floor), width: `calc(${pct(ceiling)} - ${pct(floor)})` }}
        />
        <div
          className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded-full bg-primary"
          style={{ left: pct(mean) }}
          title={`Mean: ${formatPoints(mean)} pts/game`}
        />
      </div>
      <div className="flex justify-between text-[11px] tabular-nums text-muted-foreground">
        <span>{formatPoints(floor)}</span>
        <span>{formatPoints(ceiling)}</span>
      </div>
    </div>
  );
}
