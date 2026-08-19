import "server-only";
import type {
  AnalystChatResponse,
  AnalystMessage,
  AuthResponse,
  AuthUser,
  BeatMyLeagueResponse,
  ConnectLeagueResponse,
  DraftAutopsyResponse,
  LeagueConnection,
  LineupOptimizerResponse,
  PlayoffPlannerResponse,
  PowerRankingRoastResponse,
  RosterResponse,
  ScheduleResponse,
  SeasonReplayResponse,
  SimulationResponse,
  WaiverIntelligenceResponse,
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

async function authedFetch(
  path: string,
  token: string,
  method: "GET" | "POST",
  body?: unknown,
): Promise<Response> {
  const url = new URL(path, API_BASE);

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(
      0,
      `could not reach the sim API at ${API_BASE} -- is uvicorn running? (${String(cause)})`,
    );
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // not JSON -- fall through and use the raw body text
    }
    throw new ApiError(res.status, detail || res.statusText);
  }
  return res;
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

/** GET /league/{id}/playoff-planner -- see sim.api.playoff_planner_view for
 * the full methodology (projected bracket, seeding odds, per-roster-slot
 * playoff-window strength, and the bench-depth-driven weakness signal). */
export function getPlayoffPlanner(
  leagueId: number,
  seasonId?: number,
): Promise<PlayoffPlannerResponse> {
  return getJson<PlayoffPlannerResponse>(`/league/${leagueId}/playoff-planner`, {
    season_id: seasonId,
  });
}

/** GET /league/{id}/lineup-optimizer/{team_id} -- see
 * sim.api.lineup_optimizer_view for the full methodology: why floor comes
 * from real Monte Carlo samples of the team total (never a sum of
 * individual player floors), why "highest upside" means season title
 * probability (never mean points), and exactly what search space
 * ("every single-slot swap") makes re-simulating that tractable. */
export function getLineupOptimizer(
  leagueId: number,
  teamId: number,
  seasonId?: number,
): Promise<LineupOptimizerResponse> {
  return getJson<LineupOptimizerResponse>(`/league/${leagueId}/lineup-optimizer/${teamId}`, {
    season_id: seasonId,
  });
}

/** GET /league/{id}/waiver-intelligence/{team_id} -- see
 * sim.api.waiver_intelligence_view for the full methodology: the four
 * scoring signals (opportunity, availability, league fit, competition), why
 * the response is grouped by position rather than one flat cross-position
 * list, and why this route calls no simulation at all. */
export function getWaiverIntelligence(
  leagueId: number,
  teamId: number,
  seasonId?: number,
): Promise<WaiverIntelligenceResponse> {
  return getJson<WaiverIntelligenceResponse>(
    `/league/${leagueId}/waiver-intelligence/${teamId}`,
    { season_id: seasonId },
  );
}

/** GET /league/{id}/beat-my-league/{team_id} -- see
 * sim.api.beat_my_league_view for the full methodology: every team's title
 * odds / structural strengths & weaknesses / playoff schedule difficulty
 * (all reused from Playoff Planner's own already-computed output, never
 * recomputed), plus one selected team's biggest threat, real advantage, and
 * which positions not to trade away. */
export function getBeatMyLeague(
  leagueId: number,
  teamId: number,
  seasonId?: number,
): Promise<BeatMyLeagueResponse> {
  return getJson<BeatMyLeagueResponse>(`/league/${leagueId}/beat-my-league/${teamId}`, {
    season_id: seasonId,
  });
}

/** GET /league/{id}/power-ranking-roast -- see sim.api.roast_view for the
 * full methodology: every roast sentence is grounded in an already-real,
 * already-computed fact (simulated title-odds rank, a real draft reach or
 * steal, a real bench-depth weakness, a real rival threat), never an
 * invented joke. `has_draft_data` is false for a league with no completed
 * draft to grade -- every roast still renders, just without a draft-derived
 * sentence. */
export function getPowerRankingRoast(
  leagueId: number,
  seasonId?: number,
): Promise<PowerRankingRoastResponse> {
  return getJson<PowerRankingRoastResponse>(`/league/${leagueId}/power-ranking-roast`, {
    season_id: seasonId,
  });
}

/** POST /league/{id}/analyst/{team_id} -- the AI league analyst's real
 * Gemini tool-calling loop (sim.api.analyst_view / sim.api.analyst_tools).
 * `messages` is the full persisted transcript so far (oldest first, ending
 * in the newest user turn) -- the sim API keeps no session state. Every
 * number in the response's `reply` traces back to a real `tool_calls[i]`
 * result; `citations`/`spans` let the frontend render cited numbers as
 * real components without computing anything itself. */
export function postAnalystChat(
  leagueId: number,
  teamId: number,
  body: { season_id?: number; messages: AnalystMessage[] },
): Promise<AnalystChatResponse> {
  return postJson<AnalystChatResponse>(`/league/${leagueId}/analyst/${teamId}`, body);
}

export function postAuthSignup(email: string, password: string): Promise<AuthResponse> {
  return postJson<AuthResponse>("/auth/signup", { email, password });
}

export function postAuthLogin(email: string, password: string): Promise<AuthResponse> {
  return postJson<AuthResponse>("/auth/login", { email, password });
}

export async function postAuthLogout(token: string): Promise<void> {
  await authedFetch("/auth/logout", token, "POST");
}

export async function getAuthMe(token: string): Promise<AuthUser> {
  const res = await authedFetch("/auth/me", token, "GET");
  return (await res.json()) as AuthUser;
}

export async function postLeaguesConnect(
  token: string,
  body: { league_id: number; espn_s2?: string; swid?: string },
): Promise<ConnectLeagueResponse> {
  const res = await authedFetch("/leagues/connect", token, "POST", body);
  return (await res.json()) as ConnectLeagueResponse;
}

export async function postLeaguesTeam(token: string, teamId: number): Promise<void> {
  await authedFetch("/leagues/team", token, "POST", { team_id: teamId });
}

export async function getLeaguesMe(token: string): Promise<LeagueConnection> {
  const res = await authedFetch("/leagues/me", token, "GET");
  return (await res.json()) as LeagueConnection;
}
