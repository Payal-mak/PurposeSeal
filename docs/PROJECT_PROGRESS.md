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

> **Superseded note**: this section originally had `Grant` referencing a
> resource only by a free-text `resource_id`, and `DataAsset.origin_grant_id`
> as `NOT NULL` on the (incorrect) assumption that an asset only exists
> because a grant created it. That was corrected — see "Original Asset →
> Purpose Grant → Retrieved/Derived Asset" below and the Domain Model
> Correction entry under Implemented Features. What follows is the
> corrected, current schema.

- **DataAsset** (`data_assets` table) — an original or derived piece of
  sensitive data. **Exists independently of any grant** — see the
  lifecycle explanation below.
  - `id`, `name`, `asset_type`, `parent_asset_id` (self-FK, nullable — the
    direct predecessor; `None` for an original/root asset),
    `root_asset_id` (self-FK, nullable — denormalized pointer straight at
    the top-level ancestor so lineage checks don't need to walk the parent
    chain; `None` means *this row is the root*, so the effective root id
    is always `root_asset_id or id`), `origin_grant_id` (FK to
    `grants.id`, **nullable** — `None` for an original/root asset that
    pre-exists independently of any grant; set only on a retrieved/derived
    asset, to the grant that authorized its creation), `state` (`ACTIVE` /
    `QUARANTINED`), `fingerprint` (nullable string), `created_at` (UTC).
- **Grant** (`grants` table) — PurposeSeal's "PurposeGrant": why an actor
  may use one specific, already-existing protected `DataAsset`, for how
  long, and for which operations.
  - `id`, `subject` (actor identity — a stable string, no auth system yet),
    `purpose` (free-text bounded purpose), `asset_id` (FK to
    `data_assets.id`, **not nullable** — the authoritative reference to
    the protected asset this grant authorizes use of; see "resource_id
    removal" below), `allowed_operations` (JSON array of
    `AllowedOperation` values — `VIEW`/`ANALYZE`/`COPY`/`EXPORT` —
    defaults to all four if not given), `status` (`ACTIVE` / `EXPIRED` /
    `REVOKED`), `created_at` (issued time), `expires_at` (both UTC
    datetimes).
- **AuditLog** (`audit_logs` table) — PurposeSeal's "AuditEvent": a
  persistent chronological record of domain activity.
  - `id`, `event_type` (e.g. `GRANT_CREATED`), `entity_type` (e.g.
    `"grant"`, `"data_asset"`), `entity_id`, `details` (JSON-encoded
    string), `created_at`. Deliberately references entities by
    `entity_type` + `entity_id` string rather than a real foreign key,
    since it must be able to log against any current or future entity
    without a schema change.

### Original Asset → Purpose Grant → Retrieved/Derived Asset

This is the corrected domain lifecycle, in order:

1. **Original Asset** — a protected source `DataAsset` (e.g. "Patient Lab
   Result #104") already exists in the system, independent of any grant.
   `parent_asset_id`, `root_asset_id`, and `origin_grant_id` are all
   `None`. Source data isn't *created by* a purpose — it's data that
   already exists and that a purpose later authorizes someone to use.
2. **Purpose Grant** — a `Grant` is issued *against* that existing asset:
   `Grant.asset_id` points at it (e.g. `researcher_01` →
   *Patient Lab Result #104* → `clinical_trial_screening` →
   `VIEW`/`ANALYZE`/`COPY` → 30 minutes). The asset must already exist;
   creating a grant for a nonexistent `asset_id` fails (see Tests below).
3. **Retrieved/Derived Asset** — *not implemented yet* (a future feature).
   Once retrieval exists, exercising a grant will create a new
   `DataAsset` row with `parent_asset_id`/`root_asset_id` pointing at the
   original and `origin_grant_id` set to the grant that authorized it.
   This step only confirms the schema can represent that; no retrieval
   logic exists yet.

### `resource_id` removal — design decision

The original `Grant.resource_id` (a free-text string) was **removed
outright**, not kept as legacy/display metadata and not migrated into a
new column. Once `Grant.asset_id` is the authoritative reference, keeping
`resource_id` alongside it would mean two descriptions of "what this
grant is about" that could drift out of sync (e.g. `resource_id` says one
thing, the linked asset's `name`/`asset_type` says another) — exactly the
"two conflicting sources of truth" this correction was asked to avoid.
Anything resource_id used to convey for display is now available by
joining to the asset it references. This is a breaking change to `POST
/grants` (it now requires `asset_id` instead of `resource_id`); since no
asset-creation endpoint exists yet, tests create the prerequisite
`DataAsset` directly via the ORM rather than through the (not yet built)
API — consistent with this step's schema-only scope.

### Relationships

- `Grant.asset_id` → `DataAsset.id` (a grant always references exactly
  one existing asset; **not nullable**).
- `DataAsset.origin_grant_id` → `Grant.id` (nullable — only set on
  retrieved/derived assets).
- `DataAsset.parent_asset_id` → `DataAsset.id` (self-referential, one
  level of lineage per row).
- `DataAsset.root_asset_id` → `DataAsset.id` (self-referential,
  denormalized shortcut to the root).
- SQLite does not enforce foreign keys by default; `db/session.py` turns
  `PRAGMA foreign_keys=ON` on for every connection (production and test
  engines alike) so an invalid reference actually raises `IntegrityError`
  instead of silently succeeding.

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
- **Note (corrected below)**: this entry made `origin_grant_id` `NOT
  NULL` and kept `Grant.resource_id` as the authoritative link, on the
  incorrect assumption that a `DataAsset` only exists because a grant
  created it. See the Domain Model Correction entry immediately below —
  `origin_grant_id` is now nullable and `Grant.asset_id` is a real FK.

### Domain Model Correction — separate source assets from purpose grants

- **What was wrong**: the previous entry required every `DataAsset` to
  carry a `NOT NULL origin_grant_id`, implying original protected data
  (e.g. a patient's lab result) only exists because a purpose grant was
  created for it. In reality the source asset pre-exists; a grant is
  issued *against* it later. `Grant` also had no real FK to the asset it
  protects — only a free-text `resource_id`.
- **What was implemented**: see "Original Asset → Purpose Grant →
  Retrieved/Derived Asset" and "`resource_id` removal" under Database
  Model above for the full reasoning. In summary:
  - `DataAsset.origin_grant_id` changed from `NOT NULL` to nullable — an
    original/root asset can now exist with `parent_asset_id`,
    `root_asset_id`, and `origin_grant_id` all `None`.
  - `Grant.resource_id` (free-text string) removed entirely; replaced
    with `Grant.asset_id`, a real `NOT NULL` foreign key to
    `data_assets.id`.
  - `grant_service.create_grant` now looks up the referenced `DataAsset`
    first and raises a clean `NotFoundError` (`asset_not_found`, HTTP
    404) if it doesn't exist, rather than surfacing a raw
    `IntegrityError` as a 500 to API callers. The DB-level FK constraint
    still backstops direct ORM usage that bypasses the service.
- **Files modified**: `backend/app/models/data_asset.py`
  (`origin_grant_id` nullable), `backend/app/models/grant.py`
  (`resource_id` → `asset_id`), `backend/app/schemas/grant.py`
  (`GrantCreate`/`GrantOut`: `resource_id` → `asset_id`),
  `backend/app/services/grant_service.py` (asset-existence check before
  creating a grant), `backend/tests/conftest.py` (added an
  `existing_asset` fixture — a pre-existing root `DataAsset` for tests
  that need a valid `asset_id`), `backend/tests/test_grants.py` (every
  grant-creation test now creates an asset first and passes `asset_id`;
  added a nonexistent-asset → 404 test), `backend/tests/test_domain_model.py`
  (rewritten to test the corrected lifecycle — see Tests below).
- **Endpoints changed**: `POST /grants` now requires `asset_id` (an
  existing `DataAsset` id) instead of `resource_id`. Breaking change to
  the request contract; documented and accepted because no other
  consumer of `resource_id` existed yet (frontend has no grants UI, no
  external clients).
- **Tests** (all against isolated per-test SQLite databases, never the
  development database):
  - `backend/tests/test_domain_model.py` (8 tests): an original asset can
    exist without a grant; a grant can reference an existing original
    asset; a grant referencing a nonexistent asset fails safely
    (`IntegrityError`, rolled back cleanly) at the ORM level; a
    retrieved/derived-style asset can carry both lineage
    (`parent_asset_id`/`root_asset_id`) and `origin_grant_id` pointing at
    the grant that authorized it; `allowed_operations` still survives
    persistence; an audit event still persists; invalid
    `parent_asset_id`/`origin_grant_id` references still fail safely.
  - `backend/tests/test_grants.py` (13 tests, all passing an `asset_id`
    from the new `existing_asset` fixture): every previous grant test
    updated to the new contract, plus a new test that `POST /grants`
    with a nonexistent `asset_id` returns a clean `404`
    (`error_code: "asset_not_found"`), not a raw 500.
  - `backend/tests/test_foundation.py` (4 tests) unaffected, re-run for
    regression coverage.
- **Test result**: `25 passed, 2 warnings in 2.26s` — full suite, zero
  regressions. (Warnings are the same pre-existing Starlette/FastAPI
  internal notices as every prior run, unrelated to this change.)
- **Known limitations**: retrieval is still not implemented — nothing yet
  creates a retrieved/derived `DataAsset` under a grant; this step only
  proves the schema can represent that relationship. No endpoint exists
  yet to create an original `DataAsset`, so grants can currently only be
  created against assets seeded directly via the ORM (tests) or a future
  admin/demo-seed endpoint — not yet via any HTTP call.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Liveness check — `{status, app_name}` |
| POST | /grants | Create a purpose-bound access grant for an existing `asset_id` (accepts optional `allowed_operations`) |
| GET | /grants | List all grants |
| GET | /grants/{id} | Get one grant by id |

No endpoint yet creates a `DataAsset` — one must currently be seeded
directly via the ORM (as the tests do) before a grant can reference it.

## Demo Journey

1. Open the frontend shell — product name, layout, and a live "Backend
   connected" indicator confirm the stack is wired end-to-end.
2. (Not yet exposed via API) An original `DataAsset` exists.
3. `POST /grants` with a subject, purpose, `asset_id`, and duration —
   grant is created, `ACTIVE`, and audited.

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
- **`DataAsset.origin_grant_id` is nullable, set only on retrieved/derived
  assets** (corrected from an earlier `NOT NULL` version) — an original
  source asset exists independently of any grant; only a copy/derivative
  created *under* a grant carries a reference back to it. See "Original
  Asset → Purpose Grant → Retrieved/Derived Asset" under Database Model.
- **`Grant.asset_id` is a real, `NOT NULL` foreign key to `DataAsset`**,
  and the earlier free-text `Grant.resource_id` was removed rather than
  kept alongside it — one authoritative reference to "what this grant is
  about" instead of two that could drift apart. See "`resource_id`
  removal" under Database Model.
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
- **Domain Model Correction — separate source assets from purpose
  grants**: recommended commit message
  `fix(domain): separate source assets from purpose grants`.

## Next Step

Retrieve simulated sensitive data under an active grant: create a
retrieved/derived `DataAsset` row (`parent_asset_id`/`root_asset_id`
pointing at the original, `origin_grant_id` pointing at the grant that
authorized it) and associate it with the purpose it was accessed under
(attach/inherit the purpose seal), per the hackathon priority list. This
will also need a way to create the *original* `DataAsset` in the first
place (currently only seedable via the ORM, not via any endpoint).
