import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import type { MessageToken } from "@/lib/analyst";

const CHARS_PER_TICK = 2;
const TICK_MS = 15;

function tokenWeight(token: MessageToken): number {
  return token.kind === "text" ? token.text.length : token.citation.display.length;
}

/**
 * Reveals an already-tokenized message at a steady typing cadence, purely
 * client-side: the sim API's tool-calling loop (sim.api.analyst_view) only
 * has a final reply to send once the whole multi-turn loop finishes, and
 * citation spans are computed by regex-matching numbers in that complete
 * text -- there's no partial response to stream token-by-token from the
 * server. This is a presentational replay of a reply already fully
 * received, not a second data source.
 *
 * Plain text reveals character-by-character; a citation token (rendered as
 * a <StatChip>) is atomic and only appears once the cursor reaches its full
 * width, never partially drawn. `animate=false` (already-seen messages, or
 * prefers-reduced-motion) returns the full token list immediately with no
 * timer. Keyed off `text` rather than `tokens` itself, since the caller
 * re-tokenizes on every render (a fresh array each time) -- depending on
 * that identity would restart the animation on every unrelated re-render.
 */
export function useTypewriter(
  tokens: MessageToken[],
  text: string,
  animate: boolean,
  onDone?: () => void,
): MessageToken[] {
  const reduceMotion = useReducedMotion();
  const shouldAnimate = animate && !reduceMotion;
  const [revealedChars, setRevealedChars] = useState(0);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;
  const totalChars = tokens.reduce((sum, t) => sum + tokenWeight(t), 0);

  // Drives the reveal timer only -- never calls `onDone` itself. Calling a
  // callback that updates the parent (AnalystChat's `animatingIndex`) from
  // inside a useState functional updater runs it during MessageContent's
  // own render/commit, which React disallows ("Cannot update a component
  // while rendering a different component") -- completion is reported by
  // the separate effect below instead, which runs after commit.
  useEffect(() => {
    if (!shouldAnimate) return;
    setRevealedChars(0);
    if (totalChars === 0) return;
    const interval = window.setInterval(() => {
      setRevealedChars((prev) => {
        const next = prev + CHARS_PER_TICK;
        if (next >= totalChars) {
          window.clearInterval(interval);
          return totalChars;
        }
        return next;
      });
    }, TICK_MS);
    return () => window.clearInterval(interval);
  }, [shouldAnimate, text, totalChars]);

  useEffect(() => {
    if (shouldAnimate && revealedChars >= totalChars) {
      onDoneRef.current?.();
    }
  }, [shouldAnimate, revealedChars, totalChars]);

  if (!shouldAnimate) return tokens;

  const revealed: MessageToken[] = [];
  let consumed = 0;
  for (const token of tokens) {
    const weight = tokenWeight(token);
    if (consumed + weight <= revealedChars) {
      revealed.push(token);
      consumed += weight;
      continue;
    }
    if (token.kind === "text") {
      const remaining = Math.max(0, revealedChars - consumed);
      if (remaining > 0) {
        revealed.push({ ...token, text: token.text.slice(0, remaining) });
      }
    }
    break;
  }
  return revealed;
}
