import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown by Next.js while the server component above is fetching -- shape
 * matches the Trade / Roster Swap / Season Replay tabs.
 */
export default function WhatIfLoading() {
  return (
    <div className="space-y-4 py-4" aria-live="polite" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-9 w-72" />
      <Skeleton className="h-96 w-full rounded-lg" />
    </div>
  );
}
