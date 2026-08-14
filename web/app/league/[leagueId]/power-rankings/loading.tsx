import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown by Next.js while the server component above is fetching -- shape
 * matches the championship-probability chart card + full breakdown table.
 */
export default function PowerRankingsLoading() {
  return (
    <div className="space-y-4 py-4" aria-live="polite" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-72 w-full rounded-lg" />
      <div className="space-y-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-full max-w-2xl" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    </div>
  );
}
