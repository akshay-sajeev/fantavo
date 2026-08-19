# Manual League Refresh

Date: 2026-08-19
Status: approved, not yet implemented

## Context

`/sim`'s data (league standings, rosters, cached title odds) currently only
updates on a schedule: Vercel Cron Jobs call `POST /internal/reingest` and
`POST /internal/precompute` once daily (`docs/decisions.md`'s Vercel
Serverless Migration entry), which loop over every connected user / every
ingested league respectively. A user who just made a trade, or wants to
check standings right after their games finish, has no way to see fresh
data without waiting for that daily cycle.

This spec adds a per-user, per-league manual refresh: a button on the
league dashboard that re-ingests that one user's connected league from
ESPN and recomputes its simulation odds, on demand, right now.

### Constraints inherited from the existing codebase

- `reingest_user` (`sim/api/reingest.py`) and `precompute_league`
  (`sim/api/precompute.py`) already do exactly the per-league work this
  feature needs -- both exist today, used by the batch jobs. Neither
  changes.
- Every `/league/{league_id}/*` route uses `require_league_owner`
  (`sim/api/app.py`), which 403s for any league the caller doesn't own.
  The new route follows the same convention.
- `web/lib/api.ts` is the only place the web layer talks to `/sim`
  (`import "server-only"`); Route Handlers under `web/app/api/league/
  [leagueId]/*` are the established thin-proxy pattern forwarding the
  session cookie as a bearer token, with their own `getCurrentUser`/
  `ownsLeague` checks ahead of the sim API's own backstop -- see
  `web/app/api/league/[leagueId]/season-replay/route.ts` for the pattern
  this reuses.
- ESPN's own (undocumented) rate limits, not Vercel's, are the real
  constraint on how often this can run -- see Decisions below.

## Decisions

1. **A manual refresh re-ingests AND recomputes odds**, not just a raw
   ESPN pull. Measured locally: `precompute_league` (10,000 sims) on the
   real fixture league takes 0.36s -- cheap enough that skipping it would
   only leave odds stale for no real benefit.
2. **One new route: `POST /league/{league_id}/refresh`**, gated by the
   existing `require_league_owner`. Calls `reingest_user` then
   `precompute_league` for that one user's connected league/season.
3. **5-minute cooldown, enforced server-side, keyed on the league (not the
   user).** Multiple users can be connected to the same real ESPN league
   (the existing shared-league-view product model); if one just refreshed,
   another's refresh would be redundant work anyway, so blocking both via
   the same `league.ingested_at` check is correct, not unfair.
4. **Feedback is inline button state, no new toast/notification system.**
   This codebase has no toast component yet; building one is out of scope
   for this feature.
5. **Button lives on the dashboard/Overview page only** (`web/app/league/
   [leagueId]/page.tsx`'s header), not in the shared per-league layout --
   every other sub-page still benefits from the fresher cache next time it
   loads, without needing the button itself on every page.
6. **Not built:** reconstructing an already-ticking cooldown on page load
   (e.g. if someone else refreshed 2 minutes before you loaded the page).
   The button starts enabled on every fresh page load; a 429's
   `Retry-After` corrects the client's countdown if the server disagrees.
   Avoids plumbing `ingested_at` through an extra fetch just for this.
7. **Not built:** coordinating against the daily cron job to prevent a
   manual refresh racing a same-league cron reingest/precompute under READ
   COMMITTED (the same class of issue the cron's own offset solved for
   itself). Accepted as a low-stakes, low-probability edge case for a
   first version -- an ad hoc user click landing in the exact 3:00-3:15 AM
   UTC cron window is unlikely, and the failure mode (odds computed from a
   half-updated league) is the same one-cycle staleness the system already
   tolerates elsewhere, not data corruption.

## Scope

### In scope

- `sim/api/app.py`: `POST /league/{league_id}/refresh` route, a
  `RefreshLeagueResponse` model, and a `REFRESH_COOLDOWN` constant.
- `web/lib/api.ts`: `postLeagueRefresh`.
- `web/app/api/league/[leagueId]/refresh/route.ts`: new Route Handler.
- `web/components/dashboard/refresh-button.tsx` (new Client Component).
- `web/app/league/[leagueId]/page.tsx`: render the button in the header.

### Out of scope

- A toast/notification system (decision 4).
- Cooldown state on initial page load (decision 6).
- Manual-refresh-vs-cron coordination (decision 7).
- Any change to `reingest_user`, `precompute_league`, the batch jobs, or
  Vercel Cron config -- all unchanged.
- A refresh affordance anywhere other than the dashboard page (decision 5).

## Design

### `sim/api/app.py`

```python
REFRESH_COOLDOWN = timedelta(minutes=5)


class RefreshLeagueResponse(BaseModel):
    status: str
    ingested_at: datetime | None
    odds_updated: bool


@app.post("/league/{league_id}/refresh", response_model=RefreshLeagueResponse)
def refresh_league(
    league_id: int,
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008
) -> RefreshLeagueResponse:
    """Manual, on-demand counterpart to the daily Cron-triggered
    /internal/reingest + /internal/precompute pair (see docs/decisions.md's
    Vercel Serverless Migration entry) -- re-ingests and recomputes odds
    for exactly the caller's one connected league, not the full batch.
    Cooldown is keyed on the league (not the user): this app's
    shared-league-view model means a second connected user's refresh
    request within the cooldown window would just repeat the same work,
    not serve a genuinely different need.
    """
    now = datetime.now(UTC)
    season_id = league_connection_view.resolve_current_season_id(now)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (league_id, season_id),
        )
        row = cur.fetchone()
    if row is not None:
        elapsed = now - row[0]
        if elapsed < REFRESH_COOLDOWN:
            retry_after = REFRESH_COOLDOWN - elapsed
            raise HTTPException(
                status_code=429,
                detail="refreshed too recently, try again shortly",
                headers={"Retry-After": str(max(1, int(retry_after.total_seconds())))},
            )

    try:
        reingest_user(conn, _owner.user_id, now)
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except EspnFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except IngestError:
        # e.g. RosterNotAvailableError -- a new NFL season that hasn't
        # drafted yet is a legitimate state, not a failure. Nothing
        # changed, so nothing to precompute either.
        return RefreshLeagueResponse(status="ok", ingested_at=None, odds_updated=False)

    try:
        precompute_league(conn, league_id, season_id, now)
    except IngestError:
        return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=False)

    return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=True)
```

New imports needed: `reingest_user` (extend the existing `from
sim.api.reingest import ...` line), `precompute_league` (extend the
existing `from sim.api.precompute import ...` line), `EspnFetchError`
(new: `from ingest.espn_client import EspnFetchError`), `timedelta` (extend
the existing `from datetime import ...` line). `IngestError` and
`CredentialEncryptionError` are already imported.

`429`'s `Retry-After` is a real HTTP header (RFC 9110), not an invented
field -- the standard way to tell a client when to retry, and readable
directly off `Response.headers` on the frontend without inventing a JSON
convention for it.

### `web/lib/api.ts`

`postLeagueRefresh` does **not** reuse the shared `authedFetch`/`postJson`
helpers' throw-on-non-2xx behavior, because a 429 here is an expected,
structured outcome the caller needs to render (the cooldown countdown),
not a failure to propagate as an `ApiError`. Genuine failures (502, 500,
network unreachable) still throw `ApiError`, exactly like every other
function in this file.

```typescript
export type RefreshLeagueResult =
  | { status: "ok"; ingestedAt: string | null; oddsUpdated: boolean }
  | { status: "cooldown"; retryAfterSeconds: number };

export async function postLeagueRefresh(
  token: string,
  leagueId: number,
): Promise<RefreshLeagueResult> {
  const url = new URL(`/league/${leagueId}/refresh`, API_BASE);
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(
      0,
      `could not reach the sim API at ${API_BASE} -- is uvicorn running? (${String(cause)})`,
    );
  }

  if (res.status === 429) {
    const retryAfter = Number(res.headers.get("Retry-After") ?? "300");
    return { status: "cooldown", retryAfterSeconds: Number.isFinite(retryAfter) ? retryAfter : 300 };
  }

  if (!res.ok) {
    const responseBody = await res.text().catch(() => "");
    let detail = responseBody;
    try {
      const parsed = JSON.parse(responseBody) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // not JSON -- fall through and use the raw body text
    }
    throw new ApiError(res.status, detail || res.statusText);
  }

  const body = (await res.json()) as { status: string; ingested_at: string | null; odds_updated: boolean };
  return { status: "ok", ingestedAt: body.ingested_at, oddsUpdated: body.odds_updated };
}
```

### `web/app/api/league/[leagueId]/refresh/route.ts`

Same shape as `season-replay/route.ts`: `getCurrentUser` (401 if none),
`ownsLeague` (403 if not owned), forward via `postLeagueRefresh`. The
cooldown result is **not** an exception here either -- it's serialized
straight through with its real status code:

```typescript
export async function POST(
  request: Request,
  { params }: { params: Promise<{ leagueId: string }> },
) {
  const { leagueId } = await params;
  const id = Number(leagueId);

  const user = await getCurrentUser();
  if (!user) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  if (!(await ownsLeague(id))) {
    return NextResponse.json({ error: "not authorized for this league" }, { status: 403 });
  }
  const token = (await getSessionToken())!;

  try {
    const result = await postLeagueRefresh(token, id);
    if (result.status === "cooldown") {
      return NextResponse.json(result, {
        status: 429,
        headers: { "Retry-After": String(result.retryAfterSeconds) },
      });
    }
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
```

### `web/components/dashboard/refresh-button.tsx`

Client Component, `leagueId: number` prop. Internal state machine:
`idle -> refreshing -> (cooldown | error)`, with `cooldown` also being
`idle`'s entry state after a successful click (client-managed 5-minute
countdown, corrected by the server's `retryAfterSeconds` if a click during
what the client thought was "idle" comes back `cooldown` instead --
someone else refreshed, or the daily cron ran).

- **idle**: `Refresh` button, enabled.
- **refreshing**: `Refreshing…`, disabled, small spinner.
- **cooldown**: `Refresh (4:59)` ticking down every second via
  `setInterval`, disabled; reverts to idle at 0.
- **error**: small inline text under the button (`Couldn't reach ESPN,
  try again shortly` for a 502, `Refresh failed` otherwise), button
  immediately re-enabled -- an error isn't a cooldown, no reason to block
  retrying.

On a successful response (`status: "ok"`), call Next's `router.refresh()`
(from `next/navigation`) so the Server Component page re-fetches
`getSimulation`/`getRoster`/`getSchedule` -- the whole point of the
button is that the dashboard's own numbers update, not just the button.

Whether to enter the `cooldown` state afterward depends on `ingestedAt`:
if it's non-null, something actually changed and the server's own
`league.ingested_at` check would block a retry for 5 minutes, so the
client starts the same countdown, seeded from now. If it's `null` (the
`IngestError`/pre-draft case -- nothing changed), the server's cooldown
check wouldn't block an immediate retry either (it compares against
whatever `ingested_at` already was), so the button returns straight to
`idle` instead of showing a countdown the server wouldn't actually
enforce.

### `web/app/league/[leagueId]/page.tsx`

Render `<RefreshButton leagueId={id} />` in the existing header `<div>`,
next to the "League {id} · {season} season · based on N simulated
seasons" subtitle -- a flex row, button aligned right.

## Testing

- `sim/tests/test_api_refresh.py` (new): cooldown blocks a second call
  within 5 minutes (real DB-observable: assert 429 + `Retry-After`
  header present and numeric); a call past the cooldown succeeds (real
  DB-observable: `league.ingested_at` advances, a fresh `simulation_cache`
  row exists via `read_cached_simulation`); a non-owner gets 403 from
  `require_league_owner` before ever reaching the cooldown check
  (monkeypatch `fetch_live_league`/`reingest_user` unused in that case,
  proving the auth backstop runs first); an `EspnFetchError` from a
  monkeypatched `reingest_user` maps to 502; a `RosterNotAvailableError`
  (real, via a pre-draft-shaped payload, same fixture-mutation technique
  `sim/tests/test_api_precompute.py` already uses) returns 200 with
  `odds_updated: false`, not an error.
- `web`: no existing test infrastructure covers Route Handlers or Client
  Components in this codebase today (`web/app/api/*` and `web/components/*`
  have no test files) -- this feature follows that existing precedent
  rather than introducing a new testing pattern unilaterally; verified
  instead via `npx tsc --noEmit`, `npx eslint .`, and a live browser
  walkthrough (see Verification).

## Verification

- `pytest sim/tests ingest/tests -q`, `mypy --strict sim ingest`,
  `ruff check sim ingest db scripts` -- same bar as every prior phase.
- `npx tsc --noEmit`, `npx eslint .`, `npm run build` from `/web`.
- Live walkthrough against a real connected league: click refresh, confirm
  the button cycles through its states correctly, confirm the dashboard's
  numbers actually change after a real ESPN-side update (e.g. after
  setting a lineup or after a trade), click again immediately and confirm
  the 429/cooldown countdown renders correctly, wait out the cooldown and
  confirm it re-enables.

## Known gaps (accepted, documented deliberately)

- No coordination against the daily cron job (Decision 7).
- No reconstructed cooldown state on page load (Decision 6).
- No toast system; inline button state only (Decision 4).
