"use client";

import { useState, type FormEvent } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { TeamOption } from "@/lib/types";

/**
 * Posts to /api/leagues/team. A hard navigation on success (matching
 * web/components/auth/login-form.tsx's pattern) so the destination
 * /league/{id} layout's getCurrentUser()/league data re-renders
 * server-side against the just-picked team, instead of risking a stale
 * client-cached RSC payload.
 */
export function TeamPickerForm({ leagueId, teams }: { leagueId: number; teams: TeamOption[] }) {
  const [selectedTeamId, setSelectedTeamId] = useState<number | null>(teams[0]?.team_id ?? null);
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (selectedTeamId === null) return;
    setStatus("loading");
    setErrorMessage(null);
    try {
      const res = await fetch("/api/leagues/team", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_id: selectedTeamId }),
      });
      if (!res.ok) {
        const body = await res.json();
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      window.location.href = `/league/${leagueId}`;
    } catch (error) {
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium text-foreground">Which team is yours?</legend>
        {teams.map((team) => (
          <label
            key={team.team_id}
            className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm has-[:checked]:border-primary"
          >
            <input
              type="radio"
              name="team_id"
              value={team.team_id}
              checked={selectedTeamId === team.team_id}
              onChange={() => setSelectedTeamId(team.team_id)}
              disabled={status === "loading"}
            />
            {team.name}
          </label>
        ))}
      </fieldset>
      {status === "error" && errorMessage && (
        <p className="flex items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {errorMessage}
        </p>
      )}
      <Button
        type="submit"
        disabled={status === "loading" || selectedTeamId === null}
        className="cursor-pointer"
      >
        {status === "loading" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
        Confirm
      </Button>
    </form>
  );
}
