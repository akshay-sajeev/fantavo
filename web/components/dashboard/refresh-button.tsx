"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

type RefreshState =
  | { kind: "idle" }
  | { kind: "refreshing" }
  | { kind: "cooldown"; secondsRemaining: number }
  | { kind: "error"; message: string };

const DEFAULT_COOLDOWN_SECONDS = 5 * 60;

function formatCountdown(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/**
 * Manual, on-demand counterpart to the daily Cron-triggered re-ingest +
 * precompute (see docs/decisions.md's Vercel Serverless Migration entry
 * and docs/superpowers/specs/2026-08-19-manual-league-refresh-design.md).
 * Posts to the Route Handler (never lib/api.ts directly -- that's
 * server-only), which forwards to sim's POST /league/{id}/refresh.
 */
export function RefreshButton({ leagueId }: { leagueId: number }) {
  const router = useRouter();
  const [state, setState] = useState<RefreshState>({ kind: "idle" });
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  function startCountdown(seconds: number) {
    if (intervalRef.current) clearInterval(intervalRef.current);
    setState({ kind: "cooldown", secondsRemaining: seconds });
    intervalRef.current = setInterval(() => {
      setState((prev) => {
        if (prev.kind !== "cooldown") return prev;
        if (prev.secondsRemaining <= 1) {
          if (intervalRef.current) clearInterval(intervalRef.current);
          return { kind: "idle" };
        }
        return { kind: "cooldown", secondsRemaining: prev.secondsRemaining - 1 };
      });
    }, 1000);
  }

  async function handleClick() {
    setState({ kind: "refreshing" });
    try {
      const res = await fetch(`/api/league/${leagueId}/refresh`, { method: "POST" });
      const body = await res.json();

      if (res.status === 429) {
        const seconds =
          typeof body.retry_after_seconds === "number"
            ? body.retry_after_seconds
            : DEFAULT_COOLDOWN_SECONDS;
        startCountdown(seconds);
        return;
      }
      if (!res.ok) {
        setState({
          kind: "error",
          message: res.status === 502 ? "Couldn't reach ESPN, try again shortly" : "Refresh failed",
        });
        return;
      }

      router.refresh();
      if (body.ingested_at) {
        startCountdown(DEFAULT_COOLDOWN_SECONDS);
      } else {
        setState({ kind: "idle" });
      }
    } catch {
      setState({ kind: "error", message: "Refresh failed" });
    }
  }

  const disabled = state.kind === "refreshing" || state.kind === "cooldown";
  const label =
    state.kind === "refreshing"
      ? "Refreshing…"
      : state.kind === "cooldown"
        ? `Refresh (${formatCountdown(state.secondsRemaining)})`
        : "Refresh";

  return (
    <div className="flex flex-col items-end gap-1.5">
      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={handleClick}
        className="cursor-pointer"
      >
        {state.kind === "refreshing" ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
        )}
        {label}
      </Button>
      {state.kind === "error" && (
        <p className="flex items-center gap-1.5 text-xs text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {state.message}
        </p>
      )}
    </div>
  );
}
