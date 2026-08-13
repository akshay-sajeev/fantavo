import type { ReactNode } from "react";
import { tokenizeMessage } from "@/lib/analyst";
import { StatChip } from "@/components/analyst/stat-chip";
import type { AnalystCitation, AnalystSpan } from "@/lib/types";

/**
 * Renders one message's text, substituting a real <StatChip> wherever the
 * sim API identified a real cited number (`spans`) and rendering `**bold**`
 * runs as real emphasis -- see lib/analyst.ts::tokenizeMessage for the
 * (pure, non-analytics) single-pass tokenizer this composes over.
 */
export function MessageContent({
  text,
  citations,
  spans,
}: {
  text: string;
  citations: AnalystCitation[];
  spans: AnalystSpan[];
}) {
  const tokens = tokenizeMessage(text, spans, citations);
  return (
    <span className="whitespace-pre-wrap">
      {tokens.map((token, i) => {
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
