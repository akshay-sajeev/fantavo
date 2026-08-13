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
  Flame,
  Bot,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

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
    { href: `${base}/roast`, label: "Roast", icon: Flame },
    { href: `${base}/analyst`, label: "AI Analyst", icon: Bot },
  ];

  return (
    <nav
      aria-label="League sections"
      className="sticky top-14 flex h-[calc(100vh-3.5rem)] w-14 shrink-0 flex-col items-center gap-1 overflow-y-auto border-r border-border/70 bg-card/40 py-3 backdrop-blur-md"
    >
      {tabs.map((tab) => {
        const isActive = pathname === tab.href;
        const Icon = tab.icon;
        return (
          <Tooltip key={tab.href}>
            <TooltipTrigger
              render={
                <Link
                  href={tab.href}
                  aria-current={isActive ? "page" : undefined}
                  aria-label={tab.label}
                  className={cn(
                    "flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-xl transition-all duration-150",
                    "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring",
                    isActive
                      ? "bg-primary/15 text-primary shadow-[var(--shadow-glow-primary)]"
                      : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                  )}
                />
              }
            >
              <Icon className="h-5 w-5" aria-hidden="true" />
            </TooltipTrigger>
            <TooltipContent side="right">{tab.label}</TooltipContent>
          </Tooltip>
        );
      })}
    </nav>
  );
}
