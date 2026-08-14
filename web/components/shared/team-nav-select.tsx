"use client";

import { useRouter } from "next/navigation";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * Team switcher as a dropdown that navigates to `?team=<id>` on selection --
 * a real server navigation per team (each of this app's per-team analysis
 * pages runs a live, non-trivial computation, not a cheap client-side
 * toggle), matching the URL-driven-page pattern those pages already
 * document. Replaces the horizontal row of <Link> pills every one of them
 * used to render (components/{lineup,waivers,beat-my-league,analyst}/
 * team-picker.tsx), which forced horizontal scrolling once a league had
 * more than a handful of teams.
 *
 * The `next !== selectedTeamId` guard exists because base-ui's Select fires
 * onValueChange even when re-selecting the item that's already selected (no
 * native-<select> "only on actual change" semantics) -- without it,
 * dismissing the dropdown on the current team would still push a
 * navigation and reload data that hasn't changed.
 */
export function TeamNavSelect({
  leagueId,
  path,
  teams,
  selectedTeamId,
  label,
}: {
  leagueId: number;
  path: string;
  teams: { team_id: number; team_name: string }[];
  selectedTeamId: number;
  label: string;
}) {
  const router = useRouter();

  return (
    <Select
      value={selectedTeamId}
      onValueChange={(next) => {
        if (next != null && next !== selectedTeamId) {
          router.push(`/league/${leagueId}/${path}?team=${next}`);
        }
      }}
    >
      <SelectTrigger aria-label={label} className="w-full max-w-xs font-semibold">
        <SelectValue>
          {(current: number | null) =>
            teams.find((t) => t.team_id === current)?.team_name ?? ""
          }
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
