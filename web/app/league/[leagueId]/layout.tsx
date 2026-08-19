import { redirect } from "next/navigation";
import { LeagueNav } from "@/components/shared/league-nav";
import { PageTransition } from "@/components/shared/page-transition";
import { getCurrentUser } from "@/lib/auth";
import { ownsLeague } from "@/lib/leagueConnection";

export default async function LeagueLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ leagueId: string }>;
}) {
  // The authoritative check -- middleware.ts only verified the cookie
  // exists; this actually validates it against the sim API.
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const { leagueId } = await params;

  // Per-league authorization: the signed-in user may only view their own
  // connected league. Redirecting to "/" reuses app/page.tsx's existing
  // "figure out where this signed-in user belongs" routing rather than
  // duplicating it here.
  if (!(await ownsLeague(Number(leagueId)))) redirect("/");

  return (
    <div className="flex w-full flex-1">
      <LeagueNav leagueId={Number(leagueId)} />
      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 py-4">
        <PageTransition>{children}</PageTransition>
      </div>
    </div>
  );
}
