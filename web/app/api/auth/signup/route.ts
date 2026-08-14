import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ApiError, postAuthSignup } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

// 30 days, matching sim.api.auth_view.SESSION_LIFETIME_DAYS.
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

/**
 * Orchestration only (CLAUDE.md) -- creates the account via sim, then sets
 * the session cookie here. The raw token is never sent to the browser as
 * JSON, only via the httpOnly Set-Cookie header, so client-side JS can
 * never read it.
 */
export async function POST(request: Request) {
  let body: { email?: string; password?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!body.email || !body.password) {
    return NextResponse.json({ error: "email and password are required" }, { status: 400 });
  }

  try {
    const result = await postAuthSignup(body.email, body.password);
    const cookieStore = await cookies();
    cookieStore.set(SESSION_COOKIE_NAME, result.token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: SESSION_MAX_AGE_SECONDS,
    });
    return NextResponse.json({ email: result.email });
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
