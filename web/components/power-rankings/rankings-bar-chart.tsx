"use client";

import { Bar, BarChart, Cell, LabelList, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import type { ChartConfig } from "@/components/ui/chart";
import { teamColor } from "@/lib/chart-colors";
import { formatPercent } from "@/lib/format";
import type { TeamOutcome } from "@/lib/types";

/**
 * Horizontal bar chart, sorted descending by title probability --
 * design-system/MASTER.md's explicit recommendation for power rankings
 * ("Best chart type: Horizontal or vertical bar... always sort descending
 * by value... Value labels always visible on each bar"). `teams` must
 * already be sorted by the caller (the API's own title_probability field);
 * this component only renders, it never re-sorts by a client-computed
 * score.
 */
export function RankingsBarChart({ teams }: { teams: TeamOutcome[] }) {
  const chartConfig: ChartConfig = {
    title_probability: { label: "Title probability", color: "var(--color-primary)" },
  };

  const data = teams.map((t) => ({
    team_name: t.team_name,
    title_probability: t.title_probability,
  }));

  const rowHeight = 40;
  const maxValue = Math.max(...data.map((d) => d.title_probability), 0.01);

  return (
    <ChartContainer
      config={chartConfig}
      className="!aspect-auto w-full"
      style={{ height: Math.max(220, data.length * rowHeight) }}
    >
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 40, top: 4, bottom: 4 }}>
        <XAxis type="number" domain={[0, maxValue]} tickFormatter={(v) => formatPercent(v, 0)} />
        <YAxis
          type="category"
          dataKey="team_name"
          width={160}
          tick={{ fontSize: 12 }}
          interval={0}
        />
        <ChartTooltip
          cursor={{ fill: "var(--color-muted)" }}
          content={
            <ChartTooltipContent
              formatter={(value) => formatPercent(Number(value))}
              labelKey="team_name"
            />
          }
        />
        <Bar dataKey="title_probability" radius={4} isAnimationActive={false}>
          {data.map((_, index) => (
            <Cell key={index} fill={teamColor(index)} />
          ))}
          <LabelList
            dataKey="title_probability"
            position="right"
            formatter={(value: unknown) => (typeof value === "number" ? formatPercent(value) : "")}
            className="fill-foreground text-xs font-medium"
          />
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}
