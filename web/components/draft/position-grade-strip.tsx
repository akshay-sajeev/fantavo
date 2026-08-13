import { gradeLabelClassName } from "@/components/draft/grade-visuals";
import type { PositionGrade } from "@/lib/types";

/** Scannable positional-strategy-grade summary -- one badge per bucket
 * PLAN.md names (QB/RB/WR/TE/Bench), each labeled against the league
 * average the API already computed. */
export function PositionGradeStrip({ grades }: { grades: PositionGrade[] }) {
  if (grades.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {grades.map((grade) => (
        <div
          key={grade.position}
          className={`flex flex-col gap-0.5 rounded-md border px-2.5 py-1.5 text-xs ${gradeLabelClassName(grade.label)}`}
        >
          <span className="font-semibold">
            {grade.position}{" "}
            <span className="font-normal opacity-70">
              ({grade.pick_count} pick{grade.pick_count === 1 ? "" : "s"})
            </span>
          </span>
          <span>{grade.label}</span>
        </div>
      ))}
    </div>
  );
}
