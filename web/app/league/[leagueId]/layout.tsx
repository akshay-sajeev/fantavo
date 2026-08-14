import { LeagueNav } from "@/components/shared/league-nav";
import { PageTransition } from "@/components/shared/page-transition";

export default async function LeagueLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;

  return (
    <div className="flex w-full flex-1">
      <LeagueNav leagueId={Number(leagueId)} />
      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 py-4">
        <PageTransition>{children}</PageTransition>
      </div>
    </div>
  );
}
