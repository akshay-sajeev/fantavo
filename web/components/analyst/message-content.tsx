import type { ReactNode } from "react";
import { tokenizeMessage } from "@/lib/analyst";
import { StatChip } from "@/components/analyst/stat-chip";
import { useTypewriter } from "@/components/analyst/use-typewriter";
import type { AnalystCitation, AnalystSpan } from "@/lib/types";

/**
 * Renders one message's text, substituting a real <StatChip> wherever the
 * sim API identified a real cited number (`spans`) and rendering `**bold**`
 * runs as real emphasis -- see lib/analyst.ts::tokenizeMessage for the
 * (pure, non-analytics) single-pass tokenizer this composes over.
 *
 * `animate` (only ever true for the model's just-arrived reply, set by the
 * caller in chat.tsx) reveals the tokens at a typing cadence via
 * useTypewriter instead of showing them all at once -- see that hook for
 * why this is a client-side replay rather than real token streaming.
 */
export function MessageContent({
  text,
  citations,
  spans,
  animate = false,
  onAnimationComplete,
}: {
  text: string;
  citations: AnalystCitation[];
  spans: AnalystSpan[];
  animate?: boolean;
  onAnimationComplete?: () => void;
}) {
  const tokens = tokenizeMessage(text, spans, citations);
  const revealed = useTypewriter(tokens, text, animate, onAnimationComplete);
  return (
    <span className="whitespace-pre-wrap">
      {revealed.map((token, i) => {
        if (token.kind === "citation") {
          return <StatChip key={i} citation={token.citation} />;
        }
        let node: ReactNode = token.text;
        if (token.italic) node = <em>{node}</em>;
        if (token.bold) node = <strong>{node}</strong>;
        return <span key={i}>{node}</span>;
      })}
    </span>
  );
}
