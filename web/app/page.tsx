import { redirect } from "next/navigation";

/**
 * Redirects to a default league id from DEFAULT_LEAGUE_ID so the app has
 * somewhere to land; every page under /league/[leagueId] is fully generic
 * on that route param and does not special-case which league id it
 * receives. The account/session flow lives downstream of this redirect,
 * not here: web/middleware.ts gates /league/:path* on a session cookie and
 * sends an unauthenticated visitor to /login (which redirects back to
 * /league/{DEFAULT_LEAGUE_ID} on success); every user still sees the same
 * DEFAULT_LEAGUE_ID today (per-user league data is Phase B).
 */
export default function RootPage() {
  const defaultLeagueId = process.env.DEFAULT_LEAGUE_ID ?? "-1990001";
  redirect(`/league/${defaultLeagueId}`);
}
