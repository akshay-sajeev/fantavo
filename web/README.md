# Fantavo web

Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui. UI only — no
analytics logic. Renders whatever `sim/api/app.py` returns for a given
`leagueId`; see `/CLAUDE.md` and `/docs/decisions.md` (Phase 5b) for the
full rationale.

## Run locally

Requires the sim API running (see repo root):

```bash
uvicorn sim.api.app:app --host 127.0.0.1 --port 8123
```

Then, from this directory:

```bash
cp .env.example .env.local   # adjust SIM_API_URL / DEFAULT_LEAGUE_ID if needed
npm install
npm run dev
```

## Checks

```bash
npx tsc --noEmit
npx eslint .
npm run build
```
