import "server-only";
import type {
  DraftAutopsyResponse,
  RosterResponse,
  ScheduleResponse,
  SeasonReplayResponse,
  SimulationResponse,
  WhatIfRequestBody,
} from "@/lib/types";

/**
 * Thin fetch wrapper against sim/api/app.py -- this is the ONLY place the
 * web layer talks to the sim API. No response is transformed, derived, or
 * re-aggregated here: every function returns exactly the JSON shape the
 * route handler serializes (see lib/types.ts). CLAUDE.md's "no analytics
 * logic in components" rule starts at this boundary.
 *
 * Server-only: every caller is a Server Component or a Route Handler, never
 * a client component, so the sim API's address is never shipped to the
 * browser bundle (`import "server-only"` makes an accidental client import
 * a build error rather than a silent leak).
 */

const API_BASE = process.env.SIM_API_URL ?? "http://127.0.0.1:8123";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(
  path: string,
  params: Record<string, string | number | undefined> = {},
): Promise<T> {
  const url = new URL(path, API_BASE);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch (cause) {
    throw new ApiError(
      0,
      `could not reach the sim API at ${API_BASE} -- is uvicorn running? (${String(cause)})`,
    );
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    // FastAPI's HTTPException body is {"detail": "..."} -- unwrap it so the
    // UI shows the human-readable message rather than raw JSON.
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // not JSON -- fall through and use the raw body text
    }
    throw new ApiError(res.status, detail || res.statusText);
  }
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const url = new URL(path, API_BASE);

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(
      0,
      `could not reach the sim API at ${API_BASE} -- is uvicorn running? (${String(cause)})`,
    );
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
  return (await res.json()) as T;
}

export function getSimulation(
  leagueId: number,
  seasonId?: number,
): Promise<SimulationResponse> {
  return getJson<SimulationResponse>(`/league/${leagueId}/simulation`, {
    season_id: seasonId,
  });
}

export function getRoster(leagueId: number, seasonId?: number): Promise<RosterResponse> {
  return getJson<RosterResponse>(`/league/${leagueId}/roster`, { season_id: seasonId });
}

export function getSchedule(leagueId: number, seasonId?: number): Promise<ScheduleResponse> {
  return getJson<ScheduleResponse>(`/league/${leagueId}/schedule`, { season_id: seasonId });
}

/**
 * POST /league/{id}/whatif -- live, n_sims defaults to the API's own
 * LIVE_WHATIF_N_SIMS (2000) when omitted. Used twice per scenario by
 * app/api/league/[leagueId]/whatif-compare/route.ts (a baseline call with
 * empty roster_overrides and a scenario call), sharing one seed so the two
 * results are directly comparable (common random numbers) -- never called
 * directly from a client component, per this file's server-only contract.
 */
export function postWhatif(leagueId: number, body: WhatIfRequestBody): Promise<SimulationResponse> {
  return postJson<SimulationResponse>(`/league/${leagueId}/whatif`, body);
}

/** POST /league/{id}/whatif/season-replay -- see sim.api.season_replay_view
 * and sim.api.app.SeasonReplayResponse for what this data means (one
 * sampled SYNTHETIC "actual" season, never real results). */
export function postSeasonReplay(
  leagueId: number,
  body: { season_id?: number; seed?: number },
): Promise<SeasonReplayResponse> {
  return postJson<SeasonReplayResponse>(`/league/${leagueId}/whatif/season-replay`, body);
}

/** GET /league/{id}/draft-autopsy -- see sim.api.draft_autopsy_view for the
 * full grading methodology. 409s for a league with no completed draft to
 * grade (a pre-draft league, or the SYNTHETIC validation league). */
export function getDraftAutopsy(
  leagueId: number,
  seasonId?: number,
): Promise<DraftAutopsyResponse> {
  return getJson<DraftAutopsyResponse>(`/league/${leagueId}/draft-autopsy`, {
    season_id: seasonId,
  });
}
