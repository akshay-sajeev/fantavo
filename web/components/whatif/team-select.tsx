"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { TeamRoster } from "@/lib/types";

/**
 * Shared team dropdown for the what-if builders (trade + roster swap),
 * replacing the raw <select> elements those files used before the shared
 * Select primitive existed. Owns the three things all its call sites would
 * otherwise duplicate: the team -> item mapping, resolving the current
 * team_id back to a team_name for the trigger label, and guarding
 * base-ui's nullable single-select callback so `onChange` only ever fires
 * with a real team id.
 *
 * `alignItemWithTrigger={false}` because these triggers are full-width card
 * titles: base-ui's default aligns the selected item over the trigger,
 * which would cover the card header. Dropping below reads correctly here.
 *
 * Named TeamSelect, not TeamPicker -- the `team-picker.tsx` files in
 * analyst/, beat-my-league/, lineup/ and waivers/ are URL-driven
 * server-navigated link lists, a different interaction entirely.
 */
export function TeamSelect({
  teams,
  value,
  onChange,
  label,
  className,
}: {
  teams: TeamRoster[];
  value: number;
  onChange: (teamId: number) => void;
  label: string;
  className?: string;
}) {
  return (
    <Select
      value={value}
      onValueChange={(next) => {
        if (next != null && next !== value) onChange(next);
      }}
    >
      <SelectTrigger aria-label={label} className={cn("font-semibold", className)}>
        <SelectValue>
          {(current: number | null) => teams.find((t) => t.team_id === current)?.team_name ?? ""}
        </SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {teams.map((t) => (
          <SelectItem key={t.team_id} value={t.team_id}>
            {t.team_name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
