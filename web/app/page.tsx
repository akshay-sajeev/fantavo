import { redirect } from "next/navigation";

/**
 * No account/league-selection flow exists yet (out of scope for 5b -- this
 * phase is the dashboard views themselves). Redirects to a default league
 * id from DEFAULT_LEAGUE_ID so the app has somewhere to land; every page
 * under /league/[leagueId] is fully generic on that route param and does
 * not special-case which league id it receives.
 */
export default function RootPage() {
  const defaultLeagueId = process.env.DEFAULT_LEAGUE_ID ?? "-1990001";
  redirect(`/league/${defaultLeagueId}`);
}
