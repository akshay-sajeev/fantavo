import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown by Next.js while the server component above is fetching -- shape
 * matches the dashboard's standings + matchup cards + rosters grid.
 */
export default function DashboardLoading() {
  return (
    <div className="space-y-4 py-4" aria-live="polite" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Skeleton className="h-96 w-full rounded-lg lg:col-span-2" />
        <div className="flex flex-col gap-4">
          <Skeleton className="h-44 w-full rounded-lg" />
          <Skeleton className="h-44 w-full rounded-lg" />
        </div>
      </div>
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}
