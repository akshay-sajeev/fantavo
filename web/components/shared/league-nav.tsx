"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

export function LeagueNav({ leagueId }: { leagueId: number }) {
  const pathname = usePathname();
  const base = `/league/${leagueId}`;
  const tabs = [
    { href: base, label: "Overview" },
    { href: `${base}/power-rankings`, label: "Power Rankings" },
    { href: `${base}/risk`, label: "Roster Risk" },
    { href: `${base}/whatif`, label: "What-If" },
    { href: `${base}/draft`, label: "Draft Autopsy" },
    { href: `${base}/playoffs`, label: "Playoff Planner" },
  ];

  return (
    <nav className="flex gap-1 overflow-x-auto" aria-label="League sections">
      {tabs.map((tab) => {
        const isActive = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "shrink-0 rounded-md px-3 py-2 text-sm font-medium transition-colors duration-150",
              "cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring",
              isActive
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
