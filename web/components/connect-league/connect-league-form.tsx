"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Posts to /api/leagues/connect. On success, navigates to
 * /connect-league/pick-team -- that page independently fetches the team
 * list via getLeagueConnection() (GET /leagues/me), so no client-side
 * state needs to be threaded through the navigation.
 */
export function ConnectLeagueForm() {
  const router = useRouter();
  const [leagueId, setLeagueId] = useState("");
  const [espnS2, setEspnS2] = useState("");
  const [swid, setSwid] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setErrorMessage(null);
    try {
      const res = await fetch("/api/leagues/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          league_id: Number(leagueId),
          espn_s2: espnS2 || undefined,
          swid: swid || undefined,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      router.push("/connect-league/pick-team");
    } catch (error) {
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="connect-league-id" className="text-sm font-medium text-foreground">
          League ID
        </label>
        <Input
          id="connect-league-id"
          type="text"
          inputMode="numeric"
          required
          value={leagueId}
          onChange={(e) => setLeagueId(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="connect-espn-s2" className="text-sm font-medium text-foreground">
          espn_s2 <span className="text-muted-foreground">(private leagues only)</span>
        </label>
        <Input
          id="connect-espn-s2"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={espnS2}
          onChange={(e) => setEspnS2(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="connect-swid" className="text-sm font-medium text-foreground">
          SWID <span className="text-muted-foreground">(private leagues only)</span>
        </label>
        <Input
          id="connect-swid"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={swid}
          onChange={(e) => setSwid(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Public leagues need no cookies. For a private league, copy espn_s2 and
        SWID from your browser&apos;s cookies while signed into espn.com.
      </p>
      {status === "error" && errorMessage && (
        <p className="flex items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {errorMessage}
        </p>
      )}
      <Button type="submit" disabled={status === "loading"} className="cursor-pointer">
        {status === "loading" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
        Connect league
      </Button>
    </form>
  );
}
