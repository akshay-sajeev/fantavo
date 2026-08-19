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
