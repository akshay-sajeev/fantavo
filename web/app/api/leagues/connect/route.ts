import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ApiError, postLeaguesConnect } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

export async function POST(request: Request) {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!token) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }

  let body: { league_id?: number; espn_s2?: string; swid?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!body.league_id) {
    return NextResponse.json({ error: "league_id is required" }, { status: 400 });
  }

  try {
    const result = await postLeaguesConnect(token, {
      league_id: body.league_id,
      espn_s2: body.espn_s2 || undefined,
      swid: body.swid || undefined,
    });
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
