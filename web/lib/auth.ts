import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { getAuthMe } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

export const SESSION_COOKIE_NAME = "fantavo_session";

/**
 * The authoritative session check: reads the cookie and asks the sim API
 * to validate it via GET /auth/me. Wrapped in React's cache() so the
 * several call sites that need this in one request (the root layout, plus
 * app/league/[leagueId]/layout.tsx's own check) share a single round trip
 * instead of firing it once per call site.
 *
 * Fails closed: any error at all -- an expired/invalid token (401), or the
 * sim API being unreachable (ApiError status 0) -- is treated as "not
 * signed in," never as "signed in." There's nowhere safer to fall back to.
 */
export const getCurrentUser = cache(async (): Promise<AuthUser | null> => {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!token) return null;
  try {
    return await getAuthMe(token);
  } catch {
    return null;
  }
});
