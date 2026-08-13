"use client";

import { useEffect, useRef, useState } from "react";
import { toBlob, toPng } from "html-to-image";
import { Check, Copy, Download, Flame, Trophy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { TeamRoast } from "@/lib/types";
import { RoastFacts } from "@/components/roast/roast-facts";

/**
 * PLAN.md: "award cards designed to be screenshotted and dropped into a
 * group chat. Shareable image export." This is the shareable unit -- the
 * `captureRef` div below (and ONLY that div; the export buttons render
 * outside it so they aren't captured) is exported client-side via
 * html-to-image's toPng/toBlob, which clones the DOM node and rasterizes it
 * to a canvas -- no server rendering, no headless browser, works entirely in
 * the user's own browser. See docs/decisions.md Phase 12 for why this
 * approach was chosen and what its real limitations are.
 *
 * Built as a plain styled div rather than the shared shadcn `Card` primitive
 * specifically so this component fully owns the exact box being
 * rasterized (background, radius, padding) -- a wrapped-and-refed
 * third-party component would have made that harder to reason about.
 */
export function RoastCard({ team }: { team: TeamRoast }) {
  const captureRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"idle" | "working" | "downloaded" | "copied" | "error">(
    "idle",
  );

  // Feature-detected only, so it must start false (matching what the server
  // renders, since `navigator`/`window` don't exist during SSR) and flip
  // after mount -- computing this directly during render would make the
  // client's first paint disagree with the server-rendered HTML for this
  // "use client" component and trigger a hydration mismatch.
  const [canCopyImage, setCanCopyImage] = useState(false);
  useEffect(() => {
    setCanCopyImage(
      "clipboard" in navigator && typeof window.ClipboardItem !== "undefined",
    );
  }, []);

  function fileNameBase(): string {
    return `${team.team_name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "")}-roast`;
  }

  // navigator.clipboard.write() is a permission-gated browser API that can
  // sit pending indefinitely rather than resolving or rejecting (observed
  // directly in some automated/sandboxed browser contexts -- see
  // docs/decisions.md Phase 12) -- without a hard timeout, a stuck promise
  // would leave the button disabled forever with no feedback. This wraps
  // ANY promise passed to it, not just clipboard writes, so the same guard
  // covers toPng/toBlob too if DOM-to-canvas capture ever stalls.
  function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = window.setTimeout(
        () => reject(new Error(`timed out after ${ms}ms`)),
        ms,
      );
      promise.then(
        (value) => {
          window.clearTimeout(timer);
          resolve(value);
        },
        (error: unknown) => {
          window.clearTimeout(timer);
          reject(error instanceof Error ? error : new Error(String(error)));
        },
      );
    });
  }

  async function handleDownload() {
    if (!captureRef.current || status === "working") return;
    setStatus("working");
    try {
      const dataUrl = await withTimeout(
        toPng(captureRef.current, { pixelRatio: 2, backgroundColor: "#ffffff" }),
        8000,
      );
      const link = document.createElement("a");
      link.href = dataUrl;
      link.download = `${fileNameBase()}.png`;
      link.click();
      setStatus("downloaded");
    } catch {
      setStatus("error");
    } finally {
      window.setTimeout(() => setStatus("idle"), 2200);
    }
  }

  async function handleCopy() {
    if (!captureRef.current || status === "working") return;
    setStatus("working");
    try {
      const blob = await withTimeout(
        toBlob(captureRef.current, { pixelRatio: 2, backgroundColor: "#ffffff" }),
        8000,
      );
      if (!blob) throw new Error("image export produced no data");
      await withTimeout(
        navigator.clipboard.write([new window.ClipboardItem({ "image/png": blob })]),
        8000,
      );
      setStatus("copied");
    } catch {
      setStatus("error");
    } finally {
      window.setTimeout(() => setStatus("idle"), 2200);
    }
  }

  const isTop = team.title_rank === 1;
  const isBottom = team.title_rank === team.league_team_count;

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={captureRef}
        className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-[var(--shadow-md)]"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted font-heading text-xs font-semibold text-foreground">
              #{team.title_rank}
            </span>
            <div>
              <p className="font-heading text-base leading-snug font-semibold text-foreground">
                {team.team_name}
              </p>
              <p className="text-xs text-muted-foreground">
                {team.title_rank} of {team.league_team_count} in the power rankings
              </p>
            </div>
          </div>
          {isTop || isBottom ? (
            <span
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                isTop ? "bg-brand-accent/15 text-brand-accent" : "bg-destructive/10 text-destructive",
              )}
              aria-hidden="true"
            >
              {isTop ? <Trophy className="h-4 w-4" /> : <Flame className="h-4 w-4" />}
            </span>
          ) : null}
        </div>

        <p className="text-sm leading-relaxed text-foreground/90">{team.roast}</p>

        <RoastFacts facts={team.facts} />

        <p className="text-right text-[10px] font-medium tracking-wide text-muted-foreground/70 uppercase">
          Fantavo -- {formatPercent(team.title_probability, 1)} simulated title odds
        </p>
      </div>

      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={handleDownload} disabled={status === "working"}>
          {status === "downloaded" ? (
            <Check className="text-primary" aria-hidden="true" />
          ) : (
            <Download aria-hidden="true" />
          )}
          {status === "downloaded" ? "Saved" : "Download image"}
        </Button>
        {canCopyImage ? (
          <Button variant="ghost" size="sm" onClick={handleCopy} disabled={status === "working"}>
            {status === "copied" ? (
              <Check className="text-primary" aria-hidden="true" />
            ) : (
              <Copy aria-hidden="true" />
            )}
            {status === "copied" ? "Copied" : "Copy image"}
          </Button>
        ) : null}
        {status === "error" ? (
          <span className="text-xs text-destructive">Couldn&apos;t export -- try again</span>
        ) : null}
      </div>
    </div>
  );
}
