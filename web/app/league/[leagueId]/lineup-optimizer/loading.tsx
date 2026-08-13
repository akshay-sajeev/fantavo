import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown by Next.js while the server component above is fetching -- this
 * page's computation is non-trivial (a fresh search + several simulated
 * seasons per candidate lineup), so a real loading state matters here the
 * same way PLAN.md requires one for What-If's live scenarios.
 */
export default function LineupOptimizerLoading() {
  return (
    <div className="space-y-4 py-4" aria-live="polite" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-16 w-full rounded-lg" />
      <Skeleton className="h-20 w-full rounded-lg" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-[28rem] w-full rounded-lg" />
        ))}
      </div>
    </div>
  );
}
