import { formatPercent, ordinal } from "@/lib/format";

/**
 * Segmented horizontal bar showing the probability mass at each discrete
 * finish place -- docs/MASTER.md's "per-rank stacked/horizontal
 * bar" recommendation for finish distributions, chosen over a box plot
 * specifically because `finish_distribution` is an already-discretized
 * probability mass function (one float per finish place) returned by the
 * API, not a set of raw per-simulation samples a box plot needs (see
 * docs/decisions.md Phase 5b for the full reasoning).
 *
 * The last entry in `finishDistribution` is always "missed the playoffs" --
 * matches sim.engine.SimulationResult.finish_distribution's shape
 * (n_playoff_teams + 1 columns).
 */
export function FinishDistributionStrip({
  finishDistribution,
  className,
}: {
  finishDistribution: number[];
  className?: string;
}) {
  const lastIndex = finishDistribution.length - 1;

  return (
    <div className={className}>
      <div
        className="flex h-3 w-full overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={finishDistribution
          .map((p, i) =>
            i === lastIndex
              ? `missed playoffs ${formatPercent(p)}`
              : `finished ${ordinal(i)} ${formatPercent(p)}`,
          )
          .join(", ")}
      >
        {finishDistribution.map((p, i) => {
          if (p <= 0) return null;
          const isChampion = i === 0;
          const isMissedPlayoffs = i === lastIndex;
          return (
            <div
              key={i}
              title={`${isMissedPlayoffs ? "Missed playoffs" : ordinal(i) + " place"}: ${formatPercent(p)}`}
              style={{ width: `${p * 100}%` }}
              className={
                isMissedPlayoffs
                  ? "h-full bg-muted-foreground/25"
                  : isChampion
                    ? "h-full bg-brand-accent"
                    : "h-full bg-primary"
              }
            />
          );
        })}
      </div>
    </div>
  );
}
