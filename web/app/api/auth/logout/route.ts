import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { postAuthLogout } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

export async function POST() {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (token) {
    // Best-effort: even if the sim API call fails (e.g. the session had
    // already expired), still clear the cookie below so the browser is
    // signed out locally either way.
    await postAuthLogout(token).catch(() => {});
  }
  cookieStore.delete(SESSION_COOKIE_NAME);
  return NextResponse.json({ ok: true });
}
