import type { AnalystCitation, AnalystSpan } from "@/lib/types";

/**
 * Pure text tokenization, not analytics: `spans` are character offsets the
 * sim API already computed (sim.api.analyst_view._match_citations_in_text,
 * a deterministic server-side pass over real tool results -- see that
 * module's docstring) identifying exactly where a real cited number
 * appears in `text`. `**bold**` is a plain LLM markdown habit the model's
 * prose commonly includes, rendered here as real emphasis instead of
 * literal asterisks. Neither concern computes or decides which content is
 * "real" -- both are pure display formatting over text/offsets this
 * function was already handed (CLAUDE.md's "no analytics logic in
 * components" rule, applied to the one bit of message-rendering logic this
 * feature needs).
 *
 * A single left-to-right pass walks the raw text once, tracking whether a
 * `**...**` bold run is currently open and whether the current position
 * falls inside a citation span -- so a citation that happens to sit inside
 * a bold run (the model's own habit is to bold the whole clause including
 * the number, e.g. "**18.2%**") renders correctly as a <StatChip> without
 * leaving stray "**" markers behind, which a naive two-pass
 * (split-by-citation, then bold-format each leftover fragment
 * independently) gets wrong -- exactly the bug this two-pass version had
 * during this phase's own live browser verification (see
 * docs/decisions.md Phase 13).
 *
 * The same pass also recognizes plain LLM markdown habits at the START of a
 * line only (never mid-sentence, so this can't misfire on real prose): a
 * `#`/`##`/.../`######` heading marker is dropped and the rest of that line
 * renders bold instead (a chat bubble is too small for real multi-level
 * heading typography); a `* `/`- ` list-item marker is replaced with a
 * plain bullet glyph. A lone `*...*` (single asterisk, anywhere -- distinct
 * from the `**...**` bold pair and from a line-start `* ` bullet, which
 * always has a trailing space) toggles italic instead. All of these are
 * pure substitutions over characters this function was already walking
 * past -- they don't shift any citation span's meaning, since spans are
 * always positioned at a real cited number, never at a markdown marker.
 */
export type MessageToken =
  | { kind: "text"; text: string; bold: boolean; italic: boolean }
  | { kind: "citation"; citation: AnalystCitation };

export function tokenizeMessage(
  text: string,
  spans: AnalystSpan[],
  citations: AnalystCitation[],
): MessageToken[] {
  const citationByIndex = new Map(citations.map((c) => [c.index, c]));
  const sortedSpans = [...spans]
    .filter((s) => s.start >= 0 && s.end <= text.length && s.start < s.end)
    .sort((a, b) => a.start - b.start);

  const tokens: MessageToken[] = [];
  let i = 0;
  let spanIdx = 0;
  let bold = false;
  let italic = false;
  let buffer = "";
  let atLineStart = true;

  const flush = () => {
    if (buffer) {
      tokens.push({ kind: "text", text: buffer, bold, italic });
      buffer = "";
    }
  };

  while (i < text.length) {
    // Defensively skip any span that overlaps one already consumed
    // (out-of-order/overlapping spans should never happen server-side,
    // but rendering must not crash or duplicate text if it ever does).
    while (sortedSpans[spanIdx] && sortedSpans[spanIdx].start < i) spanIdx++;

    const span = sortedSpans[spanIdx];
    if (span && span.start === i) {
      flush();
      const citation = citationByIndex.get(span.citation_index);
      if (citation) {
        tokens.push({ kind: "citation", citation });
      } else {
        // No matching citation (shouldn't happen) -- fall back to plain text.
        buffer += text.slice(span.start, span.end);
      }
      i = span.end;
      spanIdx++;
      atLineStart = false;
      continue;
    }

    if (atLineStart) {
      const heading = /^#{1,6} +/.exec(text.slice(i));
      if (heading) {
        flush();
        bold = true;
        i += heading[0].length;
        atLineStart = false;
        continue;
      }
      const bullet = /^[*-] +/.exec(text.slice(i));
      if (bullet) {
        buffer += "• "; // a real bullet glyph, not a literal "*"/"-"
        i += bullet[0].length;
        atLineStart = false;
        continue;
      }
    }

    if (text.startsWith("**", i)) {
      flush();
      bold = !bold;
      i += 2;
      atLineStart = false;
      continue;
    }

    if (text[i] === "*") {
      flush();
      italic = !italic;
      i += 1;
      atLineStart = false;
      continue;
    }

    if (text[i] === "\n") {
      flush();
      bold = false; // a heading/bold/italic run never spans past its own line
      italic = false;
      buffer += "\n";
      flush();
      atLineStart = true;
      i++;
      continue;
    }

    buffer += text[i];
    atLineStart = false;
    i++;
  }
  flush();
  return tokens;
}
