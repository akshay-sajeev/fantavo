import "server-only";
import type { RosterResponse, ScheduleResponse, SimulationResponse } from "@/lib/types";

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
