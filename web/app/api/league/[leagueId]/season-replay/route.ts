import { NextResponse } from "next/server";
import { ApiError, postSeasonReplay } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";
import { ownsLeague } from "@/lib/leagueConnection";

/** Thin pass-through to POST /league/{id}/whatif/season-replay -- see
 * sim.api.season_replay_view for the actual computation. No analytics logic
 * here, just forwarding a client-triggered request through the server-only
 * API client (Route Handlers run server-side, so this is the same "the web
 * layer's only path to the sim API" boundary lib/api.ts already enforces). */
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

  let body: { season_id?: number; seed?: number } = {};
  try {
    body = await request.json();
  } catch {
    // Empty body is fine -- season_id/seed are both optional.
  }

  try {
    const result = await postSeasonReplay(id, body);
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
