import { TrendingDown, TrendingUp } from "lucide-react";
import { formatValueGap } from "@/components/draft/grade-visuals";
import type { DraftPickGrade } from "@/lib/types";

function PickCallout({
  pick,
  variant,
}: {
  pick: DraftPickGrade;
  variant: "best" | "worst";
}) {
  const isBest = variant === "best";
  const Icon = isBest ? TrendingUp : TrendingDown;

  return (
    <div
      className={
        isBest
          ? "flex-1 rounded-lg border border-primary/25 bg-primary/5 p-3"
          : "flex-1 rounded-lg border border-destructive/25 bg-destructive/5 p-3"
      }
    >
      <div className="flex items-center gap-1.5">
        <Icon
          className={isBest ? "h-4 w-4 text-primary" : "h-4 w-4 text-destructive"}
          aria-hidden="true"
        />
        <span
          className={
            isBest
              ? "text-xs font-semibold tracking-wide text-primary uppercase"
              : "text-xs font-semibold tracking-wide text-destructive uppercase"
          }
        >
          {isBest ? "Best decision" : "Worst decision"}
        </span>
      </div>

      <p className="mt-1.5 text-sm font-medium text-foreground">
        {pick.player_name}{" "}
        <span className="font-normal text-muted-foreground">
          ({pick.position}, rank {pick.player_rank}) — pick {pick.overall_pick_number}, round{" "}
          {pick.round_id}
        </span>
      </p>

      {pick.alternative_player_name && pick.alternative_player_rank ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {isBest ? (
            <>
              Best {pick.position} still on the board:{" "}
              <span className="font-medium text-foreground">
                {pick.alternative_player_name}
              </span>{" "}
              (rank {pick.alternative_player_rank}) — you took the better player.
            </>
          ) : (
            <>
              <span className="font-medium text-foreground">
                {pick.alternative_player_name}
              </span>{" "}
              (rank {pick.alternative_player_rank}), a better {pick.position}, was still on the
              board when you took {pick.player_name}.
            </>
          )}
        </p>
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">
          No other tracked {pick.position} remained on the board at this pick.
        </p>
      )}

      <p className="mt-2 text-xs tabular-nums text-muted-foreground">
        Value gap:{" "}
        <span className={isBest ? "font-semibold text-primary" : "font-semibold text-destructive"}>
          {formatValueGap(pick.value_gap)}
        </span>{" "}
        rank-spots
      </p>
    </div>
  );
}

export function BestWorstPicks({
  best,
  worst,
}: {
  best: DraftPickGrade;
  worst: DraftPickGrade;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row">
      <PickCallout pick={best} variant="best" />
      <PickCallout pick={worst} variant="worst" />
    </div>
  );
}
