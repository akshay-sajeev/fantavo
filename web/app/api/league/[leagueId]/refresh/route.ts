import { NextResponse } from "next/server";
import { ApiError, postLeagueRefresh } from "@/lib/api";
import { getCurrentUser, getSessionToken } from "@/lib/auth";
import { ownsLeague } from "@/lib/leagueConnection";

/** Thin pass-through to POST /league/{id}/refresh -- see
 * sim.api.app.refresh_league for the actual work. Mirrors
 * season-replay/route.ts's shape: web-layer getCurrentUser/ownsLeague
 * checks run first, the sim API's require_league_owner is the backstop. */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ leagueId: string }> },
) {
  const { leagueId } = await params;
  const id = Number(leagueId);

  const user = await getCurrentUser();
  if (!user) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }
  if (!(await ownsLeague(id))) {
    return NextResponse.json({ error: "not authorized for this league" }, { status: 403 });
  }
  const token = (await getSessionToken())!;

  try {
    const result = await postLeagueRefresh(token, id);
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    const retryAfterSeconds = error instanceof ApiError ? error.retryAfterSeconds : undefined;
    return NextResponse.json(
      { error: message, retry_after_seconds: retryAfterSeconds },
      {
        status,
        headers: retryAfterSeconds !== undefined ? { "Retry-After": String(retryAfterSeconds) } : {},
      },
    );
  }
}
