import { Activity, CircleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";

const HARD_OUT_STATUSES = new Set(["OUT", "INJURY_RESERVE"]);

function label(status: string): string {
  return status
    .split("_")
    .map((word) => word[0] + word.slice(1).toLowerCase())
    .join(" ");
}

/** Renders nothing for "ACTIVE" or a missing status -- a badge only earns
 * its place when there's real uncertainty to flag. Red for OUT/INJURY_RESERVE
 * (matches `sim.api.waiver_intelligence_view._HARD_OUT_STATUSES`, the same
 * set that floors `opportunity_score` to 0 server-side); amber/secondary for
 * QUESTIONABLE, which does NOT zero out opportunity server-side -- the
 * color distinction here mirrors that real difference in how the backend
 * treats the two cases, not an arbitrary two-tone choice. */
export function InjuryBadge({ status }: { status: string | null }) {
  if (!status || status === "ACTIVE") return null;

  const isHardOut = HARD_OUT_STATUSES.has(status);
  return (
    <Badge
      variant="outline"
      className={
        isHardOut
          ? "border-destructive/40 bg-destructive/10 text-destructive"
          : "border-brand-accent/40 bg-brand-accent/10 text-brand-accent-foreground"
      }
    >
      {isHardOut ? (
        <CircleAlert className="size-3" aria-hidden="true" />
      ) : (
        <Activity className="size-3" aria-hidden="true" />
      )}
      {label(status)}
    </Badge>
  );
}
