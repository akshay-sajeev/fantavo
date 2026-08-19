import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ApiError, postLeaguesTeam } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

export async function POST(request: Request) {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!token) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }

  let body: { team_id?: number };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (body.team_id === undefined) {
    return NextResponse.json({ error: "team_id is required" }, { status: 400 });
  }

  try {
    await postLeaguesTeam(token, body.team_id);
    return NextResponse.json({ ok: true });
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
