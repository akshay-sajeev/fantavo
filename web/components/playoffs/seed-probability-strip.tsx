import { formatPercent, ordinal } from "@/lib/format";

/**
 * Segmented horizontal bar of `seed_probabilities` -- the same "per-rank
 * stacked/horizontal bar" pattern `FinishDistributionStrip` already
 * established for `finish_distribution` (docs/MASTER.md's chart
 * recommendation for a discretized probability mass function), applied
 * here to which seed a team ends up holding rather than which place it
 * finishes. The trailing "missed playoffs" segment is
 * `1 - sum(seedProbabilities)`, the same class of pure display arithmetic
 * `FinishDistributionStrip` already does with its own last bucket -- not a
 * new probability computed here, just 1 minus numbers the API returned.
 */
export function SeedProbabilityStrip({
  seedProbabilities,
  className,
}: {
  seedProbabilities: number[];
  className?: string;
}) {
  const missedPlayoffs = Math.max(
    0,
    1 - seedProbabilities.reduce((sum, p) => sum + p, 0),
  );
  const segments = [...seedProbabilities, missedPlayoffs];
  const lastIndex = segments.length - 1;

  return (
    <div className={className}>
      <div
        className="flex h-3 w-full overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={segments
          .map((p, i) =>
            i === lastIndex
              ? `missed playoffs ${formatPercent(p)}`
              : `seed ${i + 1} ${formatPercent(p)}`,
          )
          .join(", ")}
      >
        {segments.map((p, i) => {
          if (p <= 0) return null;
          const isTopSeed = i === 0;
          const isMissedPlayoffs = i === lastIndex;
          return (
            <div
              key={i}
              title={`${isMissedPlayoffs ? "Missed playoffs" : `Seed ${i + 1} (${ordinal(i)} seed)`}: ${formatPercent(p)}`}
              style={{ width: `${p * 100}%` }}
              className={
                isMissedPlayoffs
                  ? "h-full bg-muted-foreground/25"
                  : isTopSeed
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
