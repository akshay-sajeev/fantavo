import { NextResponse } from "next/server";
import { ApiError, postAnalystChat } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";
import { ownsLeague } from "@/lib/leagueConnection";
import type { AnalystMessage } from "@/lib/types";

/**
 * Orchestration only -- no analytics logic (CLAUDE.md). A thin pass-through
 * to POST /league/{id}/analyst/{team_id} (sim.api.analyst_view /
 * sim.api.analyst_tools run the real Gemini tool-calling loop entirely
 * server-side in the Python sim service; GEMINI_API_KEY never reaches this
 * route or the browser). Same "Route Handlers run server-side, so this is
 * the same 'the web layer's only path to the sim API' boundary lib/api.ts
 * already enforces" pattern as
 * app/api/league/[leagueId]/whatif-compare/route.ts and
 * app/api/league/[leagueId]/season-replay/route.ts.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ leagueId: string; teamId: string }> },
) {
  const { leagueId, teamId } = await params;
  const id = Number(leagueId);
  const team = Number(teamId);

  const user = await getCurrentUser();
  if (!user) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }
  if (!(await ownsLeague(id))) {
    return NextResponse.json({ error: "not authorized for this league" }, { status: 403 });
  }

  let body: { season_id?: number; messages?: AnalystMessage[] };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }

  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    return NextResponse.json({ error: "messages must be a non-empty array" }, { status: 400 });
  }

  try {
    const result = await postAnalystChat(id, team, {
      season_id: body.season_id,
      messages: body.messages,
    });
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
