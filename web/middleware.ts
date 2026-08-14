import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Duplicated from web/lib/auth.ts's SESSION_COOKIE_NAME rather than
// imported: that module is `import "server-only"`, and Next.js runs
// middleware in the Edge runtime, a separate bundle from the Node.js
// runtime Server Components use -- importing a server-only module here
// would be a build error, not just unnecessary.
const SESSION_COOKIE_NAME = "fantavo_session";

/**
 * A cheap first gate only: checks that the session cookie exists, nothing
 * more. It cannot validate the token against the sim API (adding a network
 * round trip to every single navigation in Edge middleware is not worth
 * it), so an expired or tampered cookie still passes this check -- the
 * authoritative check is app/league/[leagueId]/layout.tsx calling
 * getCurrentUser(), which really does call GET /auth/me.
 */
export function middleware(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE_NAME);
  if (!hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/league/:path*"],
};
