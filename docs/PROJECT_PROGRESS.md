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
  - `models/` — SQLAlchemy ORM models (`Grant`, `DataAsset`, `AuditLog`,
    plus shared enums `AllowedOperation`, `AssetState`, `GrantStatus`).
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

- **Grant** (`grants` table) — PurposeSeal's "PurposeGrant": why an actor
  may use a resource, for how long, and for which operations.
  - `id`, `subject` (actor identity — a stable string, no auth system yet),
    `purpose` (free-text bounded purpose), `resource_id` (string identifier
    of the resource/dataset category this grant authorizes — **not** a
    foreign key; see "Grant ↔ DataAsset relationship" below),
    `allowed_operations` (JSON array of `AllowedOperation` values —
    `VIEW`/`ANALYZE`/`COPY`/`EXPORT` — defaults to all four if not given),
    `status` (`ACTIVE` / `EXPIRED` / `REVOKED`), `created_at` (issued time),
    `expires_at` (both UTC datetimes).
- **DataAsset** (`data_assets` table) — an original or derived piece of
  sensitive data.
  - `id`, `name`, `asset_type`, `parent_asset_id` (self-FK, nullable — the
    direct predecessor; `None` for an original/root asset),
    `root_asset_id` (self-FK, nullable — denormalized pointer straight at
    the top-level ancestor so lineage checks don't need to walk the parent
    chain; `None` means *this row is the root*, so the effective root id
    is always `root_asset_id or id`), `origin_grant_id` (FK to
    `grants.id`, **not nullable** — denormalized onto every asset, root
    and copies alike, so "which purpose justified this data" is a single-
    column lookup), `state` (`ACTIVE` / `QUARANTINED`), `fingerprint`
    (nullable string), `created_at` (UTC).
- **AuditLog** (`audit_logs` table) — PurposeSeal's "AuditEvent": a
  persistent chronological record of domain activity.
  - `id`, `event_type` (e.g. `GRANT_CREATED`), `entity_type` (e.g.
    `"grant"`, `"data_asset"`), `entity_id`, `details` (JSON-encoded
    string), `created_at`. Deliberately references entities by
    `entity_type` + `entity_id` string rather than a real foreign key,
    since it must be able to log against any current or future entity
    without a schema change.

### Relationships

- `DataAsset.origin_grant_id` → `Grant.id` (many assets per grant).
- `DataAsset.parent_asset_id` → `DataAsset.id` (self-referential, one
  level of lineage per row).
- `DataAsset.root_asset_id` → `DataAsset.id` (self-referential,
  denormalized shortcut to the root).
- SQLite does not enforce foreign keys by default; `db/session.py` turns
  `PRAGMA foreign_keys=ON` on for every connection (production and test
  engines alike) so an invalid reference actually raises `IntegrityError`
  instead of silently succeeding.

### Grant ↔ DataAsset relationship — design decision

PurposeGrant's "protected/root asset" concept is **not** a foreign key on
`Grant`. Reasoning: per the hackathon demo flow, a grant is created
*before* any data has been retrieved (step 1), and a `DataAsset` row only
comes into existence when that data is actually retrieved (step 2, a
future feature) — at grant-creation time there is nothing yet for
`Grant` to point to. So the relationship runs the other way: `Grant`
keeps its free-text `resource_id` (which resource/category it
authorizes), and each `DataAsset` created under it stores
`origin_grant_id` pointing back to the grant. This also means **no
change was needed to the existing `POST /grants` endpoint's contract**
for `resource_id` — only an additive, optional `allowed_operations`
field was introduced.

### Usage/policy decision entity — design decision

No dedicated `UsageDecision` table was added this step. Reasoning: no
evaluation logic exists yet (that is the "detect purpose expiry" /
"evaluate reuse" feature, still ahead per the priority list), and
building the table before the real decision shape is known from that
logic risks getting it wrong and rewriting it. `AuditLog` is already
generic enough (`event_type` + `entity_type` + `entity_id` + JSON
`details`) to record `USE_ALLOWED` / `PURPOSE_VIOLATION` events for now.
When the policy engine lands, its required output shape (`decision`,
`reason_code`, `human_readable_reason`, `grant_id`, `asset_id`,
`evaluated_at` — per the project's policy engine rules) will very likely
justify a dedicated, queryable `UsageDecision` table so judges can filter
decisions by grant/asset; that table is deferred to that feature, not
built speculatively now.

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

### Core Domain Model — DataAsset, enums, and FK-safe persistence

- **What was implemented**:
  - `DataAsset` model (`data_assets` table) with self-referential
    `parent_asset_id`/`root_asset_id` and a denormalized `origin_grant_id`
    FK to `Grant` — see Database Model above for the full field list and
    the reasoning for how it relates to `Grant`.
  - `AllowedOperation` (`VIEW`/`ANALYZE`/`COPY`/`EXPORT`) and `AssetState`
    (`ACTIVE`/`QUARANTINED`) enums added under `app/models/`; `Grant`
    gained an `allowed_operations` JSON column (defaults to all four
    operations when not supplied).
  - `DataAssetOut` Pydantic schema added for future serialization; no new
    HTTP endpoints were added for `DataAsset` (per explicit scope — only
    enough was built to test persistence).
  - SQLite foreign-key enforcement turned on (`PRAGMA foreign_keys=ON`)
    for both the production and test engines via a shared
    `enable_sqlite_foreign_keys()` helper in `db/session.py` — without
    this, SQLite silently ignores invalid FK references by default.
  - Reset the local `backend/purposeseal.db` dev database file (it's
    gitignored, disposable demo data — `Base.metadata.create_all` only
    creates missing tables, it does not add columns to an existing table,
    so the old `grants` table needed to be recreated to pick up
    `allowed_operations`).
- **Important decisions**: see "Grant ↔ DataAsset relationship" and
  "Usage/policy decision entity" under Database Model above.
- **Files added**: `backend/app/models/{enums,data_asset}.py`,
  `backend/app/schemas/data_asset.py`, `backend/tests/test_domain_model.py`.
- **Files modified**: `backend/app/models/{grant,__init__}.py` (added
  `allowed_operations`), `backend/app/schemas/{grant,__init__}.py` (added
  `allowed_operations` to `GrantCreate`/`GrantOut`),
  `backend/app/services/grant_service.py` (defaults `allowed_operations`
  to all four operations, includes it in the `GRANT_CREATED` audit
  details), `backend/app/db/session.py` (FK pragma helper),
  `backend/tests/conftest.py` (enables FK pragma on the test engine, adds
  a `db_session` fixture), `backend/tests/test_grants.py` (3 new tests for
  the `allowed_operations` field).
- **Endpoints changed**: `POST /grants` gained an optional
  `allowed_operations` field (list of `AllowedOperation`); omitting it
  defaults to all four. No breaking change — existing callers are
  unaffected.
- **Tests added**:
  - `backend/tests/test_domain_model.py` (8 tests, covering the required
    checklist): create + retrieve an original asset; create a child asset
    relationship; root/parent relationship is valid (root has `None`
    `root_asset_id`, child points at root's id); persist a `Grant`;
    `allowed_operations` survives persistence; an audit event persists;
    an invalid `parent_asset_id` reference raises `IntegrityError`
    (rolled back cleanly); an invalid `origin_grant_id` reference raises
    `IntegrityError` (rolled back cleanly). All run against a fresh
    per-test SQLite file (via the existing `db_engine`/`db_session`
    fixtures), never the development database.
  - `backend/tests/test_grants.py`: `allowed_operations` defaults to all
    four when omitted; is stored/returned as given when supplied
    explicitly; an invalid operation value (`"DELETE"`) is rejected with
    422 by Pydantic before it reaches the database.
- **Test result**: `24 passed, 2 warnings in 1.59s` (16 from before + 8
  domain-model tests; the `test_grants.py` count includes 3 new
  `allowed_operations` tests). No regressions.
- **Known limitations**: no lifecycle logic implemented — nothing
  transitions `Grant.status` to `EXPIRED`, nothing creates a `DataAsset`
  on retrieval, nothing quarantines one on violation; this step is schema
  only, exactly as scoped. No `UsageDecision` table (see design decision
  above). No ORM `relationship()` declarations on `DataAsset`'s two
  self-FKs — tests query by id directly rather than via lineage
  traversal helpers, since no such helpers were needed to prove
  persistence.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Liveness check — `{status, app_name}` |
| POST | /grants | Create a purpose-bound access grant (now accepts optional `allowed_operations`) |
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
- **`allowed_operations` stored as a JSON column, not a normalized
  many-to-many table** — a small, fixed enum per grant doesn't justify a
  join table at this scale; the project's own guidance says to normalize
  "where sensible without overengineering."
- **`DataAsset.origin_grant_id` denormalized onto every asset (root and
  copies)** rather than only on the root — the whole point of the system
  is "which purpose justified this data," so that lookup is made a single
  column read instead of a lineage-chain walk, at the cost of one integer
  column repeated down the chain. See "Grant ↔ DataAsset relationship"
  under Database Model.
- **SQLite FK enforcement explicitly turned on** — SQLite ignores foreign
  keys by default per connection; without the `PRAGMA foreign_keys=ON`
  pragma, invalid lineage/grant references would silently persist instead
  of failing, which the project's reliability priority can't afford.

## Known Issues / Deferred Work

- No lifecycle/business logic yet: nothing transitions `Grant.status` to
  `EXPIRED`, nothing creates a `DataAsset` on retrieval, nothing detects a
  purpose violation or quarantines an asset. Schema only, so far.
- No revoke-grant endpoint yet.
- No `UsageDecision` table yet — deferred until the policy-evaluation
  feature defines its real shape (see design decision above).
- Frontend has no routing and no feature screens yet (by design for this
  step) — grants UI, retrieval UI, audit trail view, and lineage graph all
  come with their respective backend features.
- No `.env.example` committed yet (nothing currently requires one to run
  locally); add one if/when a required env var appears.
- No schema migration tool (Alembic etc.) — tables are created via
  `Base.metadata.create_all`, which only adds missing tables, never alters
  existing ones. Fine for a hackathon MVP (the local dev db can just be
  deleted and recreated), but worth naming as a real limitation.

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
- **Core Domain Model — DataAsset, enums, FK-safe persistence**:
  recommended commit message `feat(domain): add PurposeSeal core data model`.

## Next Step

Retrieve simulated sensitive data under an active grant: create a
`DataAsset` row (root, `origin_grant_id` pointing at the grant) and
associate it with the purpose it was accessed under (attach/inherit the
purpose seal), per the hackathon priority list.
