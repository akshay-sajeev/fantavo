import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown by Next.js while the server component above is fetching -- matches
 * `app/league/[leagueId]/waivers/loading.tsx`'s pattern for this app's
 * other URL-driven per-team analysis pages.
 */
export default function AnalystLoading() {
  return (
    <div className="space-y-4 py-4" aria-live="polite" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-96 w-full rounded-xl" />
    </div>
  );
}
