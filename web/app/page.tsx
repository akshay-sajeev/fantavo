import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import { getLeagueConnection } from "@/lib/leagueConnection";

/**
 * Auth Phase B: DEFAULT_LEAGUE_ID and the unconditional redirect it drove
 * are gone -- every signed-in user now lands on their own real connected
 * league. web/middleware.ts still gates /league/:path* on the session
 * cookie and sends an unauthenticated visitor to /login; this page adds
 * the next layer once signed in: no connection yet -> /connect-league,
 * connected but no team picked -> /connect-league/pick-team, both set ->
 * their real league.
 */
export default async function RootPage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const connection = await getLeagueConnection();
  if (!connection?.league_id) redirect("/connect-league");
  if (!connection.team_id) redirect("/connect-league/pick-team");
  redirect(`/league/${connection.league_id}`);
}
