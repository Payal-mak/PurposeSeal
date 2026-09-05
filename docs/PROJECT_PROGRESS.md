# PurposeSeal Development Progress

## Current MVP Goal

Demonstrate that purpose-bound access to sensitive data is enforced beyond
the moment of retrieval: a grant is created for a specific purpose, data is
retrieved under it, copies/derived data are tracked, the purpose expires,
and any later use of the already-retrieved data is evaluated against the
original purpose — violations are blocked and audited, not just prevented
at the access-grant level.

## Architecture

- **Backend**: Python 3.11, FastAPI, SQLAlchemy ORM, Pydantic v2 schemas,
  SQLite database (file-based, survives restarts). Layered package layout
  under `backend/app/`:
  - `api/` — thin FastAPI routers only (request/response wiring), no
    business logic or direct DB queries.
  - `services/` — business logic (e.g. `grant_service.create_grant`,
    `audit_service.write_audit_log`), takes a DB session, returns models.
  - `models/` — SQLAlchemy ORM models (`Grant`, `AuditLog`).
  - `schemas/` — Pydantic request/response schemas.
  - `db/` — `base.py` (declarative `Base`) and `session.py` (engine,
    `SessionLocal`, `get_db`, `init_db`).
  - `core/` — cross-cutting concerns: `config.py` (centralized
    `Settings`), `clock.py` (time abstraction), `errors.py` (`AppError`
    hierarchy + exception handlers).
- **Frontend**: React 19 + Vite 8 + Tailwind CSS v4 (via `@tailwindcss/vite`,
  no separate `tailwind.config.js`/PostCSS needed under v4). Minimal shell
  only: `Layout` (header with product name + placeholder nav) and
  `HealthIndicator` (polls backend `/health` on mount, shows
  checking/connected/unavailable). No feature screens yet.
- **Time handling**: a single `Clock` singleton (`app/core/clock.py`) is
  the only source of "now" for business logic. It wraps real UTC time plus
  an in-memory offset that can be advanced (`clock.advance(minutes=...)`)
  so a 30-minute expiry can be demoed in seconds. No business code calls
  `datetime.now()` directly.
- **Audit trail**: a dedicated `AuditLog` table, written to via
  `app/services/audit_service.py:write_audit_log`, decoupled from any
  single feature's router so every future feature can log into the same
  trail.
- **Error handling**: `app/core/errors.py` defines an `AppError` base
  (with `NotFoundError` so far) and registers two FastAPI exception
  handlers — one turning any `AppError` into a clean
  `{error_code, message}` JSON body with the right status code, one
  catching any unhandled exception and returning a generic 500 instead of
  leaking a traceback.
- **Configuration**: `app/core/config.py` exposes a cached `Settings`
  (pydantic-settings) for `app_name`, `database_url`
  (`PURPOSESEAL_DATABASE_URL` env var), and `cors_origins` — nothing reads
  `os.environ` directly outside this module.

## Database Model

- **Grant** (`grants` table)
  - `id`, `subject` (who holds the grant), `purpose` (free-text bounded
    purpose), `resource_id` (identifier of the sensitive resource/dataset),
    `status` (`ACTIVE` / `EXPIRED` / `REVOKED`), `created_at`, `expires_at`
    (both UTC datetimes).
- **AuditLog** (`audit_logs` table)
  - `id`, `event_type` (e.g. `GRANT_CREATED`), `entity_type` (e.g.
    `"grant"`), `entity_id`, `details` (JSON-encoded string), `created_at`.

No relationships between them yet at the ORM level (audit entries reference
entities by `entity_type` + `entity_id` string, not a foreign key) — kept
loose deliberately since future entities (retrieved data, copies) will also
need to write into the same audit log.

## Implemented Features

### Feature 1 — Create purpose-bound access grant

- **What was implemented**: `POST /grants` creates a `Grant` with a
  caller-supplied `subject`, `purpose`, `resource_id`, and
  `duration_minutes`. `expires_at` is computed as `clock.now() +
  duration_minutes`, status starts as `ACTIVE`. `GET /grants` lists all
  grants; `GET /grants/{id}` fetches one (404 if missing). Every successful
  creation writes a `GRANT_CREATED` audit log entry containing the grant's
  subject/purpose/resource/expiry.
- **Important decisions**:
  - Grant duration is specified as `duration_minutes` (not a raw
    `expires_at` timestamp) so the API/demo speaks in relative terms
    ("30 minutes") and always derives `expires_at` from the abstracted
    clock rather than trusting client-supplied timestamps.
  - `Clock` and `AuditLog` were built now, ahead of being asked for as a
    separate "feature," because every subsequent feature (retrieval,
    copies, expiry, violation detection) depends on both — building them
    once as shared infrastructure avoids retrofitting.
  - CORS is enabled for `http://localhost:5173` (default Vite dev port) in
    `app/main.py` so the not-yet-built frontend can call the API without
    later config churn.
- **Files added**: `backend/requirements.txt`, `backend/app/__init__.py`,
  `backend/app/database.py`, `backend/app/clock.py`, `backend/app/models.py`,
  `backend/app/schemas.py`, `backend/app/audit.py`,
  `backend/app/routers/__init__.py`, `backend/app/routers/grants.py`,
  `backend/app/main.py`, `backend/tests/__init__.py`,
  `backend/tests/conftest.py`, `backend/tests/test_grants.py`, `.gitignore`.
- **Endpoints added**: `POST /grants`, `GET /grants`, `GET /grants/{id}`,
  `GET /health`.
- **Tests added** (`backend/tests/test_grants.py`, 9 tests): happy-path
  creation, retrieval by id, listing multiple grants, 404 on missing grant,
  422 on missing `subject`, 422 on empty `purpose`, 422 on zero
  `duration_minutes`, 422 on negative `duration_minutes`, and a check that
  creation writes exactly one `GRANT_CREATED` audit log entry.
- **Test result**: `9 passed, 2 warnings in 0.65s` (warnings are
  Starlette/FastAPI internal deprecation notices about their own test
  client, unrelated to this code).
- **Known limitations**: no revocation endpoint yet; no automatic
  transition from `ACTIVE` to `EXPIRED` yet (that lands with the "detect
  purpose expiry" feature); no data-retrieval or copy tracking yet.
- **Note (superseded by Foundation below)**: the files listed above were
  later moved into the layered `api/services/models/schemas/db/core`
  structure with no behavior change — see the Foundation entry.

### Foundation — Layered architecture, centralized config, error handling, frontend shell

- **What was implemented**:
  - Migrated the flat `app/{database,models,schemas,audit,clock}.py` +
    `app/routers/grants.py` from Feature 1 into the layered structure
    described under Architecture, with grant business logic moved out of
    the router and into `services/grant_service.py`.
  - Added `core/config.py` (centralized `Settings`) and `core/errors.py`
    (`AppError`/`NotFoundError` + two exception handlers registered in
    `main.py`); the grants "not found" case now raises `NotFoundError`
    instead of a raw `HTTPException`.
  - `/health` now returns `{"status": "ok", "app_name": "PurposeSeal"}`
    (previously just `{"status": "ok"}`).
  - Scaffolded `frontend/` with Vite's React template, added Tailwind CSS
    v4 and Vitest + React Testing Library + jsdom, and built a minimal
    shell: `Layout` (title, tagline, disabled placeholder nav for
    Grants/Retrieved Data/Audit Trail) and `HealthIndicator` (fetches
    `VITE_API_BASE_URL` or `http://localhost:8000` + `/health` on mount).
  - Verified the backend actually boots under real `uvicorn` (not just
    `TestClient`) and that CORS headers are correctly returned for the
    `http://localhost:5173` origin.
- **Important decisions**:
  - Migrated Feature 1's code into the new structure now (while it's a
    single small feature) rather than leaving two coexisting styles,
    per explicit direction — cheaper now than after more features exist.
  - Business logic pulled out of the router into `services/` so `api/`
    stays a thin translation layer; matches the project's "keep DB access
    separated from business logic" rule and makes services independently
    testable later without a live HTTP layer.
  - Tailwind v4's Vite plugin was used instead of the v3
    config-file/PostCSS setup — v4 is the current stable release and needs
    no `tailwind.config.js` for this MVP's needs.
  - No feature screens were built in the frontend (per explicit scope
    restriction) — only product name, layout shell, and a live backend
    health indicator.
- **Files added**: `backend/app/core/{__init__,config,errors}.py`,
  `backend/app/core/clock.py` (moved), `backend/app/db/{__init__,base,session}.py`,
  `backend/app/models/{__init__,grant,audit_log}.py`,
  `backend/app/schemas/{__init__,grant}.py`,
  `backend/app/services/{__init__,audit_service,grant_service}.py`,
  `backend/app/api/{__init__,health,grants}.py`,
  `backend/tests/test_foundation.py`; entire `frontend/` app
  (`src/App.jsx`, `src/components/{Layout,HealthIndicator}.jsx` +
  `.test.jsx`, `src/lib/api.js`, `src/setupTests.js`, `src/index.css`,
  config files).
- **Files removed**: `backend/app/{database,models,schemas,audit}.py`,
  `backend/app/routers/` (contents moved into `api/`/`services/`).
- **Files modified**: `backend/app/main.py` (assembles settings, error
  handlers, `api_router`), `backend/requirements.txt` (added
  `pydantic-settings`), `backend/tests/conftest.py` (updated import paths),
  `.gitignore` (added `.env*` patterns).
- **Endpoints changed**: `/health` response shape gained `app_name`; all
  grants endpoints unchanged in behavior, just re-homed under `api/grants.py`.
- **Tests added**: `backend/tests/test_foundation.py` (4 tests: app boots
  and serves its OpenAPI schema, `/health` returns 200 with expected shape,
  a DB session can be created and closed, an unknown route returns 404).
  `frontend/src/App.test.jsx` (2 tests: renders "PurposeSeal" heading,
  renders nav items) and
  `frontend/src/components/HealthIndicator.test.jsx` (4 tests: checking
  state before resolution, connected on success, unavailable on fetch
  rejection, unavailable on non-OK response) — fetch is mocked via
  `vi.stubGlobal`, no real network calls in tests.
- **Test results**:
  - Backend: `13 passed, 2 warnings in 0.67s` (9 from Feature 1 + 4 new;
    the 2 warnings are pre-existing Starlette/FastAPI internal notices).
  - Frontend: `Test Files 2 passed (2), Tests 6 passed (6)`.
  - Manually booted the app under real `uvicorn` and confirmed `GET
    /health` returns 200 with the expected body, and that the response
    carries `access-control-allow-origin: http://localhost:5173`.
  - Frontend production build (`npm run build`) succeeds.
- **Known limitations**: no `tailwind.config.js` content-scanning
  customization needed yet (v4 auto-detects); no frontend routing library
  added (single-page shell only, matches "no feature screens yet"); no
  `.env.example` committed yet since no secrets/config are required to run
  locally.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Liveness check — `{status, app_name}` |
| POST | /grants | Create a purpose-bound access grant |
| GET | /grants | List all grants |
| GET | /grants/{id} | Get one grant by id |

## Demo Journey

1. Open the frontend shell — product name, layout, and a live "Backend
   connected" indicator confirm the stack is wired end-to-end.
2. `POST /grants` with a subject, purpose, resource, and duration — grant
   is created, `ACTIVE`, and audited.

(Later steps — retrieval, copy tracking, expiry, violation detection,
blocking/quarantine, and their corresponding frontend screens — will be
appended here as each feature lands.)

## Technical Decisions

- **SQLite via SQLAlchemy**, default file `backend/purposeseal.db`
  (gitignored), overridable via `PURPOSESEAL_DATABASE_URL` env var — keeps
  the MVP deterministic and restart-safe without extra infrastructure.
- **Clock abstraction over `datetime.now()`** — required by the project's
  simulation rule so a 30-minute expiry can be demoed in seconds via
  `clock.advance()`, without branching business logic on "are we in demo
  mode."
- **No LLM/AI in the decision path** — policy decisions (once implemented)
  will be plain deterministic Python/SQL logic, per project rules.
- **Layered backend package structure adopted immediately** (rather than
  after more features existed) so `api/services/models/schemas/db/core`
  boundaries are established once, cheaply, instead of retrofitted later.
- **Tailwind v4 via `@tailwindcss/vite`** instead of v3's PostCSS config —
  fewer config files, same utility classes, current stable release.

## Known Issues / Deferred Work

- No grant expiry transition, data retrieval, copy/lineage tracking,
  purpose-violation policy engine, or quarantine action yet — these are
  the next features per the hackathon priority list.
- No revoke-grant endpoint yet.
- Frontend has no routing and no feature screens yet (by design for this
  step) — grants UI, retrieval UI, audit trail view, and lineage graph all
  come with their respective backend features.
- No `.env.example` committed yet (nothing currently requires one to run
  locally); add one if/when a required env var appears.

## Git History

- **Feature 1 — Create purpose-bound access grant**: recommended commit
  message `feat(grants): add purpose-bound access grant creation`
  (+ `test(grants): cover grant creation happy/invalid/edge cases`, or
  combined into one commit — see Git Commands below). Committed and
  pushed to `origin/main` as `feat(grants): add purpose-bound access
  grant creation`.
- **Foundation — layered architecture, config, error handling, frontend
  shell**: recommended commit message
  `chore(core): scaffold PurposeSeal application`.

## Next Step

Retrieve simulated sensitive data under an active grant, and associate the
retrieved data with the purpose it was accessed under (attach/inherit the
purpose seal), per the hackathon priority list.
