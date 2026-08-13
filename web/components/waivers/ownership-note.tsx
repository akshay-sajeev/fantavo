import { Info } from "lucide-react";

/**
 * Honest data-provenance framing, visible in the UI itself and not just in
 * docs/decisions.md -- same "surface the caveat in the product, not just
 * the docs" discipline `components/lineup/framing-note.tsx` already
 * established for Lineup Optimizer's season-long-override framing. The
 * sentence itself comes straight from the API
 * (`sim.api.waiver_intelligence_view._OWNERSHIP_DATA_NOTE`), never
 * reworded here.
 */
export function OwnershipNote({ note }: { note: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
      <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <p className="leading-relaxed">{note}</p>
    </div>
  );
}
