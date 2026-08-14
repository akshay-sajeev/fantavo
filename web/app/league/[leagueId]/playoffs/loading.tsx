import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown by Next.js while the server component above is fetching -- shape
 * matches the per-team playoff cards + bracket + seeding odds cards.
 */
export default function PlayoffsLoading() {
  return (
    <div className="space-y-4 py-4" aria-live="polite" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-96" />
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-40 w-full rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-72 w-full rounded-lg" />
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}
