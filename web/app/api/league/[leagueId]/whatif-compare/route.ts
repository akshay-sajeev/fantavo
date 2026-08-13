import { NextResponse } from "next/server";
import { ApiError, postWhatif } from "@/lib/api";
import type { WhatIfCompareResponse } from "@/lib/types";

/**
 * Orchestration only -- no analytics logic (CLAUDE.md). Calls the real
 * sim API's POST /league/{id}/whatif twice: once with empty
 * `roster_overrides` (the baseline, i.e. every team's real ingested
 * roster) and once with the caller's scenario overrides, sharing ONE seed
 * between the two calls (common random numbers) so "before" and "after"
 * differ only because of the roster change, not sampling noise. Both calls
 * go through sim.engine.simulate_seasons() via the existing whatif route
 * (sim/api/app.py) -- this handler adds no new simulation path, it is
 * plumbing over the one that already exists, matching lib/api.ts's
 * existing "the web layer's only path to the sim API" pattern (Route
 * Handlers run server-side, so importing that server-only module here is
 * safe and intended).
 */

// Matches sim.api.seeds._SEED_MODULUS exactly, so a seed drawn here is
// always in the range np.random.default_rng() accepts on the Python side.
const SEED_MODULUS = 2 ** 32 - 1;

function randomSeed(): number {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return buf[0] % SEED_MODULUS;
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ leagueId: string }> },
) {
  const { leagueId } = await params;
  const id = Number(leagueId);

  let body: { season_id?: number; overrides: Record<string, number[]>; n_sims?: number };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }

  if (!body.overrides || Object.keys(body.overrides).length === 0) {
    return NextResponse.json({ error: "overrides must include at least one team" }, { status: 400 });
  }

  const overrides: Record<number, number[]> = {};
  for (const [teamId, playerIds] of Object.entries(body.overrides)) {
    overrides[Number(teamId)] = playerIds;
  }

  const seed = randomSeed();

  try {
    const [before, after] = await Promise.all([
      postWhatif(id, {
        season_id: body.season_id,
        roster_overrides: {},
        n_sims: body.n_sims,
        seed,
      }),
      postWhatif(id, {
        season_id: body.season_id,
        roster_overrides: overrides,
        n_sims: body.n_sims,
        seed,
      }),
    ]);
    const result: WhatIfCompareResponse = { seed, before, after };
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
