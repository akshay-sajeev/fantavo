"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  TrendingUp,
  ShieldAlert,
  GitCompare,
  ClipboardList,
  Trophy,
  ListChecks,
  UserPlus,
  Swords,
  Bot,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

// Collapsed rail width, not Tailwind's default w-14 (56px). Chosen so a
// centered icon lines up with the header logo's own center: the logo sits
// `px-4` (16px) in and renders at its 4:3 aspect ratio -- see layout.tsx.
// An icon centered in a slot this wide, starting at x=0 (this rail sits
// flush left, same as the header), lands at the same center-x -- verified
// live via getBoundingClientRect, not just computed, since Next/Image
// rounding can shift the logo's true rendered width by a pixel or two.
const COLLAPSED_WIDTH = "w-[86px]";

/**
 * Icon-only rail that expands into a labeled list on hover/focus, instead
 * of a per-icon tooltip -- hovering (or tabbing through, via
 * `focus-within`) reveals every page's name at once. The outer div keeps a
 * fixed collapsed width reserved in the page's flex layout so the main
 * content column never reflows on hover; the actual `<nav>` inside is
 * absolutely positioned within that box and grows past it, floating over
 * the content with a shadow while expanded, then snaps back -- the same
 * flyout-rail pattern VSCode's activity bar / Notion's sidebar use.
 *
 * Each icon lives in its own fixed-width, centered slot (matching
 * `COLLAPSED_WIDTH`) as the first child of its row, rather than being
 * centered via padding on the row itself -- padding-based centering drifts
 * off-true-center the moment the row's own width changes (exactly the bug
 * this replaced: a `px-3.5` row centers its icon only by coincidence, and
 * didn't here). A fixed slot keeps the icon pinned at the same x position
 * whether the rail is collapsed or expanded, with the label simply
 * revealed to its right as room opens up.
 */
export function LeagueNav({ leagueId }: { leagueId: number }) {
  const pathname = usePathname();
  const base = `/league/${leagueId}`;
  const tabs: { href: string; label: string; icon: LucideIcon }[] = [
    { href: base, label: "Overview", icon: LayoutDashboard },
    { href: `${base}/power-rankings`, label: "Power Rankings", icon: TrendingUp },
    { href: `${base}/risk`, label: "Roster Risk", icon: ShieldAlert },
    { href: `${base}/whatif`, label: "What-If", icon: GitCompare },
    { href: `${base}/draft`, label: "Draft Autopsy", icon: ClipboardList },
    { href: `${base}/playoffs`, label: "Playoff Planner", icon: Trophy },
    { href: `${base}/lineup-optimizer`, label: "Lineup Optimizer", icon: ListChecks },
    { href: `${base}/waivers`, label: "Waiver Intelligence", icon: UserPlus },
    { href: `${base}/beat-my-league`, label: "Beat My League", icon: Swords },
    { href: `${base}/analyst`, label: "AI Analyst", icon: Bot },
  ];

  return (
    // z-30 lives here, not just on the inner <nav> -- `position: sticky`
    // always creates its own stacking context (unlike `relative`, which
    // only does with a non-auto z-index), so without an explicit z-index
    // *on this wrapper* it stacks at the implicit z-index:0/DOM-order tier
    // against its sibling (the main content column), which comes later in
    // the DOM and would win the paint order despite the inner nav's z-30 --
    // that z-30 only ever mattered inside this wrapper's own context.
    <div className={cn("sticky top-16 z-30 h-[calc(100vh-4rem)] shrink-0", COLLAPSED_WIDTH)}>
      <nav
        aria-label="League sections"
        className={cn(
          // hover/focus-within width is w-64 (256px), not w-56 -- the
          // wider COLLAPSED_WIDTH icon slot (96px, up from the old 56px
          // rail) eats into the space left for labels; the longest one
          // ("Waiver Intelligence") measures 137px and needs the extra
          // room to not truncate.
          "absolute inset-y-0 left-0 z-30 flex flex-col gap-1 overflow-x-hidden overflow-y-auto border-r border-border/70 bg-card/95 py-3 backdrop-blur-md transition-[width,box-shadow] duration-200 ease-out hover:w-64 hover:shadow-xl focus-within:w-64 focus-within:shadow-xl",
          COLLAPSED_WIDTH
        )}
      >
        {tabs.map((tab) => {
          const isActive = pathname === tab.href;
          const Icon = tab.icon;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "flex h-10 shrink-0 cursor-pointer items-center overflow-hidden rounded-xl transition-all duration-150",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring",
                isActive
                  ? "bg-primary/15 text-primary shadow-[var(--shadow-glow-primary)]"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              )}
            >
              <span className={cn("flex shrink-0 items-center justify-center", COLLAPSED_WIDTH)}>
                <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
              </span>
              <span className="min-w-0 truncate pr-4 text-sm font-medium">{tab.label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
