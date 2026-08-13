import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown by Next.js while the server component above is fetching -- this
 * page reuses Playoff Planner's full simulate_seasons() + per-slot sampling
 * pipeline, a real, non-trivial computation, so a real loading state
 * matters here the same way it does for Lineup Optimizer.
 */
export default function BeatMyLeagueLoading() {
  return (
    <div className="space-y-4 py-4" aria-live="polite" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-9 w-full" />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Skeleton className="h-32 w-full rounded-lg" />
        <Skeleton className="h-32 w-full rounded-lg" />
      </div>
      <Skeleton className="h-20 w-full rounded-lg" />
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}
