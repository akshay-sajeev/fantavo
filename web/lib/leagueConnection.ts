import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { getLeaguesMe } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";
import type { LeagueConnection } from "@/lib/types";

/**
 * Mirrors web/lib/auth.ts's getCurrentUser(): reads the session cookie and
 * asks the sim API for this user's league-connection state. Returns null
 * if there's no session, or if the sim API call fails for any reason --
 * fails closed to "not connected," the safe side for every caller here
 * (the fallback is always "show the connect flow," never "show someone
 * else's league").
 */
export const getLeagueConnection = cache(async (): Promise<LeagueConnection | null> => {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!token) return null;
  try {
    return await getLeaguesMe(token);
  } catch {
    return null;
  }
});

/**
 * Auth Phase B's per-league authorization check (added in the final-review
 * fix wave). Verifies the signed-in user's own connected league actually
 * matches the leagueId in the URL. Every /league/{id}/* page (via the
 * shared layout) and every /api/league/{id}/* Route Handler (client-side
 * fetches, not covered by middleware.ts's matcher -- see
 * web/middleware.ts's own docstring) must call this before touching
 * leagueId. Deliberately does NOT itself check whether the caller is
 * signed in at all -- call getCurrentUser() first, as every existing call
 * site already does.
 */
export async function ownsLeague(leagueId: number): Promise<boolean> {
  const connection = await getLeagueConnection();
  return connection?.league_id === leagueId;
}
