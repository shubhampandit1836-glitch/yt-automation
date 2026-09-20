# Orbit · YT Automation

A safe-by-default control plane for the autonomous Hinglish gaming channel described in the master plan. This repository is the first executable slice of that architecture:

- **FastAPI** is the only backend surface: REST, SSE, durable queue, controls and health.
- **LangGraph** is the orchestration core. The same Planner → Research → Creation → Render → QA → Publish graph has a deterministic fallback for minimal installs, so the dashboard works before external accounts are configured.
- **React + Vite in JavaScript** is an observability dashboard. It is not on the production critical path.
- **SQLite** is the zero-cost local repository. The explicit repository boundary is ready to be replaced by Postgres/pgvector for production.
- **Dry-run is the default.** No YouTube upload, LLM request or generated claim is implied by a successful local request.

## What works now

- durable jobs, runs, node events, videos, metrics, series, experiments, provider health and audit tables;
- worker-side queue claiming with retries and backoff;
- an APScheduler entry point that only enqueues work;
- a resource governor with provider roles, reservations and persisted mock quota;
- a safe pipeline graph that blocks unverified local fixture claims at QA;
- SSE event stream for live dashboard updates;
- owner control endpoints for pause, resume, kill switch and force-run;
- responsive dark monitoring UI with Overview, Videos, Performance, Series, Experiments, Comments, Memory, Ops and Controls pages.

Provider, YouTube, FFmpeg, OAuth, pgvector and real embedding adapters are intentionally seams rather than fake implementations. Wire each adapter and pass its own QA contract before changing `DRY_RUN=false`.

## Quick start

### Backend

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

The API is at `http://localhost:8000`, OpenAPI docs at `/docs`, and health at `/api/v1/health`.

In a second shell, run one worker and enqueue one safe local job:

```bash
. .venv/bin/activate
python -m backend.app.scheduler --once
python -m backend.app.workers.runtime --once
```

The job will finish with a visible QA hold because local mode has no verified research source. This is intentional: the scaffold must fail closed, not publish a made-up fact.

### Dashboard

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

Open `http://localhost:5173`. Vite proxies `/api` to the backend, so browser code never calls `localhost` directly in a deployed build. Use `VITE_API_BASE_URL` only when the API is hosted at a different public origin.

### Tests and build

```bash
pytest
cd frontend && npm run build
```

### Docker Compose

`docker-compose.yml` contains separate `api`, `worker` and `scheduler` services and a persistent local data volume. It is a starting point for the planned VM deployment; use Postgres/Supabase/Neon and a real secret manager before production.

```bash
cp .env.example .env
docker compose up --build
```

## API shape

| Route | Purpose |
| --- | --- |
| `GET /api/v1/overview` | slate, queue, views, health and quota summary |
| `GET /api/v1/videos` | video ledger with fact sheet, QA and license fields |
| `GET /api/v1/runs` | graph status and current node |
| `GET /api/v1/events/stream` | live SSE event stream |
| `GET /api/v1/ops` | provider, queue and audit state |
| `POST /api/v1/controls/force-run` | enqueue work without running it in the API process |
| `POST /api/v1/controls/pause` | pause publishing, audit logged |
| `POST /api/v1/controls/kill-switch` | stop new production work, audit logged |

In production, put Google sign-in or Cloudflare Access in front of the API and supply the owner identity as part of the authenticated gateway integration. The local development guard intentionally does not create a pretend auth system.

## Safety decisions

1. **No verified source, no publish.** The research node retains source URLs and a verification flag. QA blocks any unverified claim.
2. **No provider key, no provider call.** Empty credentials use explicit mock mode; mock responses are labelled and never claim to be external results.
3. **API never does heavy work.** It only writes jobs. Workers claim jobs, persist graph events and retry failures.
4. **Controls are not learning inputs.** Compliance gates, thresholds, budgets and kill-switch conditions are outside the editable playbook.
5. **Dry run is conservative.** `DRY_RUN=true` is the default and `publishable` remains false even if the rest of a graph completes.

## Project map

```text
backend/app/
  api.py                 REST and SSE contract
  db.py                  SQLite repository and durable schema
  events.py              durable event + live fan-out
  graphs/pipeline.py     LangGraph-compatible workflow
  services/governor.py   quota and concurrency budget
  services/providers.py  normalized provider seam + mock mode
  workers/runtime.py     claim / run / retry loop
  scheduler.py           enqueue-only APScheduler process
  main.py                FastAPI application factory
frontend/src/
  App.jsx                dashboard views and live state
  api.js                relative API client and SSE client
  styles.css            responsive dark UI
```

## Next implementation seams

- Google OAuth and resumable YouTube upload idempotency;
- grounded research adapters with retained evidence records;
- FFmpeg/Pillow renderer and Whisper alignment;
- Postgres migrations, advisory lock and pgvector retriever;
- real TTS, image, stock and Telegram adapters with quota telemetry;
- Analytics snapshots, comment intelligence and the signal-ladder learning loop;
- external heartbeat pings and policy-notice email parsing.

Those integrations should each land behind the existing interfaces and add an acceptance test before being enabled in production.
