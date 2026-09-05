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

### Purpose Seal propagation — design decision

A retrieved/derived `DataAsset` must be able to answer, later: where did
this data originate, which grant authorized its retrieval, what purpose
justified it, and when does that purpose expire. **No new columns were
added to store any of this.** The existing FK fields already fully
answer the first two questions (`parent_asset_id` / `root_asset_id` for
lineage, `origin_grant_id` for authorization), and the latter two
(purpose, expiry) are answered by joining through `origin_grant_id` to
the `Grant` row at *read time* — `grant_service`'s pattern of computing
derived facts on demand (as with live-evaluated grant status) extends
naturally to assets. `services/asset_service.py:to_data_asset_out`
performs this join and returns `origin_purpose` and
`origin_grant_expires_at` as response-only, computed fields.

The alternative — copying `purpose` and `expires_at` onto every
retrieved `DataAsset` at creation time — was rejected for the same
reason `resource_id` was removed from `Grant`: it would create a second,
driftable copy of information the grant already owns (e.g. if a grant
were ever revoked or corrected, every asset retrieved under it would
silently keep stale values). Joining through the FK means there is
exactly one place purpose/expiry live, and every retrieved asset always
reflects the grant's current values.

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

### Purpose Grant Lifecycle — status evaluation and stricter validation

- **What was implemented**:
  - `GET /grants/{id}/status` — evaluates a grant's *current* status
    (`ACTIVE`/`EXPIRED`/`REVOKED`) live, with a `reason_code` and
    `human_readable_reason` explaining the decision
    (`WITHIN_VALIDITY_WINDOW`, `EXPIRY_TIME_PASSED`, or
    `EXPLICITLY_REVOKED`). Implemented as `grant_service.evaluate_grant_status`,
    a pure function: it reads `clock.now()` and the grant's stored
    `status`/`expires_at`, and **never writes back to the database** — the
    stored `status` column only ever changes via an explicit action
    (creation → `ACTIVE`; a future revoke action → `REVOKED`).
    `EXPIRED` is always derived at read time from `expires_at`, never
    persisted, satisfying "do not silently mutate unrelated state."
  - `GET /grants` and `GET /grants/{id}` now also serialize the
    **live-evaluated** status (via a new `grant_service.to_grant_out`
    helper) instead of the raw stored column, so a grant past its expiry
    shows `EXPIRED` immediately everywhere, not just at the dedicated
    `/status` endpoint.
  - `allowed_operations` on `POST /grants` changed from optional
    (defaulting to all four operations) to **required, with at least one
    entry** — matching this step's explicit validation rules. Pydantic's
    `min_length=1` plus the existing `AllowedOperation` enum together
    reject a missing field, an empty list, and any unsupported operation
    value, all with 422.
  - Confirmed "expiry must be after issue time" is enforced structurally
    rather than by an extra check: the API only ever accepts a relative
    `duration_minutes` (never a raw `expires_at`), and `duration_minutes`
    must be `> 0`, so a computed `expires_at` can never be at or before
    `created_at`.
- **Bug found and fixed along the way**: comparing `clock.now()`
  (timezone-aware UTC) against a `Grant.expires_at` read back from SQLite
  raised `TypeError: can't compare offset-naive and offset-aware
  datetimes`. SQLite has no native timezone-aware datetime type, so
  `DateTime(timezone=True)` silently returns naive values on read.
  Fixed at the root with a new `UTCDateTime` `TypeDecorator`
  (`app/db/types.py`) that normalizes every datetime to timezone-aware
  UTC on both write and read, applied to all three models' timestamp
  columns (`Grant`, `DataAsset`, `AuditLog`) — not just the one
  comparison site, since every future feature that compares timestamps
  (expiry checks are the core of this project) would otherwise hit the
  same bug.
- **Important decisions**:
  - Evaluated status is computed, never persisted — the alternative (a
    background job or request-time write flipping `status` to `EXPIRED`)
    would violate "do not silently mutate unrelated state" and add
    complexity a pure read doesn't need.
  - `allowed_operations` required-with-`min_length=1` was a deliberate
    breaking change from the prior "defaults to all four" behavior,
    because this step's validation rules explicitly listed "at least one
    allowed operation is required" alongside actor/purpose as required
    fields. The now-unused ORM-level default on `Grant.allowed_operations`
    was left in place as a harmless defensive fallback for direct ORM
    construction (e.g. in tests), not removed.
- **Files added**: `backend/app/db/types.py` (`UTCDateTime`).
- **Files modified**: `backend/app/models/{grant,data_asset,audit_log}.py`
  (switched to `UTCDateTime`), `backend/app/schemas/grant.py`
  (`allowed_operations` required; added `GrantStatusOut`),
  `backend/app/schemas/__init__.py` (export `GrantStatusOut`),
  `backend/app/services/grant_service.py` (added
  `evaluate_grant_status`, `is_grant_active`, `to_grant_out`; removed the
  allowed-operations default-fill), `backend/app/api/grants.py` (added
  the `/status` route; responses now go through `to_grant_out`),
  `backend/tests/test_grants.py` (every grant-creation call now passes
  `allowed_operations`; added tests for missing/empty allowed operations
  and both status-evaluation scenarios).
- **Endpoints changed**: `POST /grants` — `allowed_operations` is now
  required (breaking change; previously optional with a default). New:
  `GET /grants/{id}/status`.
- **Tests added/updated** (all against isolated per-test SQLite
  databases):
  - `test_create_grant_missing_allowed_operations_returns_422`,
    `test_create_grant_with_empty_allowed_operations_returns_422` (new).
  - `test_active_grant_status_evaluation` — creates a grant, checks
    `/status` returns `ACTIVE` / `WITHIN_VALIDITY_WINDOW`.
  - `test_expired_grant_status_evaluation_using_controlled_time` — creates
    a grant, advances the abstracted clock past its expiry
    (`clock.advance(minutes=31)`), confirms `/status` and `GET
    /grants/{id}` both show `EXPIRED` / `EXPIRY_TIME_PASSED`, **and**
    directly queries the database to confirm the stored `status` column
    is still `ACTIVE` — proving evaluation never mutates persisted state.
  - `test_grant_status_for_nonexistent_grant_returns_404`.
  - Every pre-existing grant-creation test updated to pass
    `allowed_operations` explicitly (previously relied on the removed
    default).
  - Removed `test_create_grant_defaults_allowed_operations_to_all_four`
    — the default behavior it tested no longer exists, per the changed
    domain rule.
- **Test result**: `29 passed, 2 warnings in 4.78s` — full suite (8 domain
  model + 4 foundation + 17 grants), zero regressions. Also manually
  verified end-to-end against a live `uvicorn` process: created a grant,
  confirmed `ACTIVE`/`WITHIN_VALIDITY_WINDOW`, advanced the clock 31
  simulated minutes, confirmed both `/status` and `GET /grants/{id}` show
  `EXPIRED`/`EXPIRY_TIME_PASSED` with real ISO-8601 UTC (`Z`-suffixed)
  timestamps.
- **Known limitations**: no revoke endpoint yet, so `REVOKED` is
  reachable in code (`evaluate_grant_status` handles it) but not yet
  through any API call. No endpoint exposes `clock.advance()` — time
  simulation is currently only exercised from tests/scripts, not
  demo-able through the frontend or a REST call. Copied-data enforcement
  (the actual purpose-violation check against retrieved/derived assets)
  is explicitly out of scope for this step.

### Data Retrieval and Purpose Seal Propagation

- **What was implemented**: `POST /retrievals` — the first place actual
  policy enforcement happens end-to-end. Given `{grant_id, actor,
  asset_id, operation}`, it:
  1. loads the grant (404 if it doesn't exist);
  2. checks `grant.subject == actor` (else `403 actor_mismatch`);
  3. checks `grant.asset_id == asset_id` (else `403 asset_mismatch`);
  4. evaluates the grant via the existing `grant_service.evaluate_grant_status`
     (else `403 grant_not_active`, reusing its `reason_code`/
     `human_readable_reason` so expiry/revocation explanations are
     defined in exactly one place);
  5. checks `operation` is in `grant.allowed_operations` (else
     `403 operation_not_permitted`);
  6. only then creates a new `DataAsset` — the retrieved copy — with
     `parent_asset_id`/`root_asset_id` pointing at the source asset and
     `origin_grant_id` pointing at the grant (the "purpose seal"; see the
     design decision under Database Model above);
  7. writes a `DATA_RETRIEVED` audit event referencing the new asset.
  Any of steps 2–5 failing writes a `DATA_RETRIEVAL_DENIED` audit event
  against the *grant* instead (distinct event type, so it can never be
  mistaken for a successful retrieval) and raises before any `DataAsset`
  or `DATA_RETRIEVED` event is created — failure is atomic with respect
  to what gets persisted.
  - A SHA-256 `fingerprint` is computed for every retrieved copy, hashed
    from the **root asset's** stable identity (id, name, asset_type) —
    see the explicit limitation below.
  - `services/asset_service.py:to_data_asset_out` serializes any
    `DataAsset` with its purpose seal resolved (`origin_purpose`,
    `origin_grant_expires_at`) plus a convenience
    `effective_root_asset_id` (`root_asset_id or id`, so callers never
    need to know the NULL-means-root convention themselves).
- **Fingerprint limitation (explicit, not a bug)**: the fingerprint is a
  SHA-256 hash of a small string standing in for "this asset's content"
  (there is no real file content in this simulated MVP). Two copies
  retrieved from the same root asset get the *same* fingerprint, which is
  correct — but this can only ever prove byte-for-byte identity. It
  **cannot** detect that a summarized, reworded, partially copied, or
  otherwise transformed version of the data was derived from this asset;
  a real adversary editing the content even slightly defeats an exact
  hash. Detecting transformed derivatives would need similarity/content
  analysis, explicitly out of scope here.
- **Important decisions**: see "Purpose Seal propagation — design
  decision" under Database Model above for why no new columns were added
  to `DataAsset` to carry purpose/expiry. Also fixed a real bug found
  while wiring this up: `schemas/data_asset.py:DataAssetOut.origin_grant_id`
  was declared as a required `int`, even though the underlying model
  column has been nullable since the Domain Model Correction step —
  serializing any original/root asset (where it's legitimately `None`)
  would have raised a response-validation error. Corrected to
  `Optional[int]`; caught only now because this was the first time
  `DataAssetOut` was actually used by an endpoint.
- **Files added**: `backend/app/api/retrievals.py`,
  `backend/app/schemas/retrieval.py`,
  `backend/app/services/{retrieval_service,asset_service,fingerprint}.py`,
  `backend/tests/test_retrieval.py`.
- **Files modified**: `backend/app/core/errors.py` (added
  `ForbiddenError`, HTTP 403), `backend/app/schemas/data_asset.py`
  (`origin_grant_id` → `Optional[int]`; added `effective_root_asset_id`,
  `origin_purpose`, `origin_grant_expires_at`), `backend/app/schemas/__init__.py`,
  `backend/app/api/__init__.py` (registered the retrievals router).
- **Endpoints added**: `POST /retrievals`.
- **Tests added** (`backend/tests/test_retrieval.py`, 9 tests, all
  against isolated per-test SQLite databases): successful retrieval;
  correct root linkage (`parent_asset_id`/`root_asset_id`/
  `effective_root_asset_id` all point at the source asset); correct
  grant linkage (`origin_grant_id`, `origin_purpose`,
  `origin_grant_expires_at` match the grant); a `DATA_RETRIEVED` audit
  event is created; an expired grant is denied (`clock.advance()`
  controlled time, `403 grant_not_active`, zero new `DataAsset` rows,
  zero `DATA_RETRIEVED` events, one `DATA_RETRIEVAL_DENIED` event); wrong
  actor denied (`403 actor_mismatch`); wrong asset denied
  (`403 asset_mismatch`, using a second seeded asset); `VIEW` not
  permitted when the grant only allows `ANALYZE`
  (`403 operation_not_permitted`); retrieval against a nonexistent grant
  returns `404`. Every denial test explicitly asserts the `DataAsset`
  count is unchanged, directly proving failed retrieval never produces a
  successful retrieval record.
- **Test result**: `38 passed, 2 warnings in 4.30s` — full suite (8 domain
  model + 4 foundation + 17 grants + 9 retrieval), zero regressions.
  Manually verified end-to-end against a live `uvicorn` process: seeded
  `patient_lab_104`, created a grant for `researcher_01` /
  `clinical_trial_screening` / `VIEW,ANALYZE,COPY`, retrieved it
  successfully (seal fields all correct, 64-character fingerprint),
  then confirmed a wrong-actor attempt and an `EXPORT` (not in the
  grant's allowed operations) attempt both cleanly denied with
  `403` and a clear `error_code`.
- **Known limitations**: only one level of retrieval is exercised here
  (original → retrieved copy); making a *copy of a copy* (the next
  hackathon step) should work with the same schema (`parent_asset_id`
  would point at the retrieved copy, `root_asset_id` still at the true
  root) but isn't implemented or tested yet. No endpoint lists retrieved
  assets — only the direct `POST /retrievals` response and DB inspection
  currently expose them.

### Reliability Correction — atomic domain writes and audit events

- **Partial-state risk discovered**: `grant_service.create_grant` and
  `retrieval_service.retrieve_data` each called `db.commit()` **twice** —
  once right after creating the business record (`Grant` /
  retrieved `DataAsset`), and again after writing its success audit
  event (`GRANT_CREATED` / `DATA_RETRIEVED`). Between those two commits,
  the business record was already durably persisted. If the second
  commit — or `write_audit_log` itself — had failed for any reason (a
  disk error, a constraint violation, the process being killed), the
  database would be left holding a successfully created grant or
  retrieved asset with **no audit record showing it happened**. For a
  system whose core value proposition is an explainable, trustworthy
  audit trail, a business record that exists without its audit event is
  exactly the kind of quiet corruption that undermines the whole premise
  — worse than the operation simply failing outright.
- **Correction**: both functions now do a single `db.add()` →
  `db.flush()` (to assign the new row's id, needed for the audit entry's
  `entity_id`, without committing) → `write_audit_log(...)` →
  `db.commit()`, wrapped in `try/except Exception: db.rollback(); raise`.
  The business record and its success audit event are now one atomic
  transaction: either both are durably persisted, or neither is.
  `audit_service.write_audit_log` was already correct — it only
  `db.add()`s and `db.flush()`es, never commits — so transaction
  ownership already belonged to the caller; no change was needed there,
  confirming the bug was purely in the two call sites' double-commit
  pattern, not in the shared helper.
- **Denied-retrieval attempts were deliberately left unchanged**: those
  are single, audit-only writes (no paired business record), so there is
  no atomicity gap to close, per the task's explicit instruction not to
  touch that path unless correctness required it.
- **Files modified**: `backend/app/services/grant_service.py`,
  `backend/app/services/retrieval_service.py` (both: replaced
  commit-then-commit with flush-then-single-commit, wrapped in
  try/rollback).
- **Files added**: `backend/tests/test_atomicity.py`.
- **Tests added** (4 tests, all against isolated per-test SQLite
  databases):
  - `test_successful_grant_creation_is_atomic` — a normal grant creation
    still produces exactly one `Grant` row and exactly one
    `GRANT_CREATED` audit event.
  - `test_grant_creation_rolls_back_if_audit_write_fails` — monkeypatches
    `grant_service.write_audit_log` to raise, confirms the exception
    surfaces (Starlette's `ServerErrorMiddleware` re-raises after
    sending its 500 response, which `TestClient` propagates into the
    test — itself proof the failure wasn't silently swallowed), and
    confirms **zero** `Grant` rows and **zero** `GRANT_CREATED` events
    exist afterward — the flushed-but-uncommitted grant was rolled back.
  - `test_successful_retrieval_is_atomic` — mirrors the grant test for
    retrieval: exactly one retrieved `DataAsset` and one `DATA_RETRIEVED`
    event.
  - `test_retrieval_rolls_back_if_audit_write_fails` — mirrors the grant
    failure test: monkeypatches `retrieval_service.write_audit_log` to
    raise, confirms only the original asset remains (no retrieved copy
    committed) and zero `DATA_RETRIEVED` events exist.
  No existing test was weakened or removed to make this pass.
- **Test result**: `42 passed, 2 warnings in 9.24s` — full suite (4
  atomicity + 8 domain model + 4 foundation + 17 grants + 9 retrieval),
  zero regressions.
- **Known limitations**: this addresses application-level atomicity
  (both writes inside one SQLAlchemy transaction/commit). It does not
  add distributed-transaction or two-phase-commit machinery — unnecessary
  for a single SQLite database where both tables live in the same
  transaction anyway; the fix is entirely about not committing between
  the two related writes, not about coordinating across separate
  databases.

### Copy and Derived-Data Lineage

- **What was implemented**:
  - `POST /copies` — create a copy or derived asset from any *existing*
    asset that was itself legitimately retrieved (has an
    `origin_grant_id`). Given `{parent_asset_id, name, asset_type,
    derivation_type, actor, operation}`, it re-runs exactly the same
    checks as retrieval — actor matches the origin grant's subject,
    the grant is currently active (`grant_service.evaluate_grant_status`),
    and `operation` is in the grant's `allowed_operations` — before
    creating the child. `derivation_type` (`COPY` or `DERIVED`) decides
    the audit event type and the fingerprinting strategy, but is **not**
    persisted as a column (see design decision below).
  - `GET /assets/{id}` — fetch a single asset by id (with its purpose
    seal resolved, same as retrieval's response shape). Added because,
    once assets can chain several levels deep, there needed to be a way
    to look one up directly instead of only ever seeing it in a parent
    response.
  - `GET /assets/{id}/lineage` — the "useful query": given *any* node in
    a lineage tree (root or a deep descendant), returns the **whole
    tree** as `{root_asset_id, nodes, edges}` — a frontend-friendly
    nodes/edges shape that drops directly into a graph visualization
    library (e.g. the project's planned `@xyflow/react` lineage graph)
    with no reshaping needed. One query suffices regardless of depth,
    because `root_asset_id` is denormalized onto every descendant (a
    design decision from the Core Domain Model step) — `WHERE id =
    root_id OR root_asset_id = root_id` retrieves the entire tree in a
    single pass, no recursive walk required.
  - Example lineage actually produced end-to-end
    (`patient_lab_104` → `Retrieved copy of patient_lab_104` →
    `analysis_dataset_1` → `derived_report_1`), verified via a live
    `uvicorn` process:
    ```
    GET /assets/1/lineage
    {
      "root_asset_id": 1,
      "nodes": [
        {"id": 1, "name": "patient_lab_104", "parent_asset_id": null, "root_asset_id": null, "origin_grant_id": null, ...},
        {"id": 2, "name": "Retrieved copy of patient_lab_104", "parent_asset_id": 1, "root_asset_id": 1, "origin_grant_id": 1, ...},
        {"id": 3, "name": "analysis_dataset_1", "parent_asset_id": 2, "root_asset_id": 1, "origin_grant_id": 1, ...},
        {"id": 4, "name": "derived_report_1", "parent_asset_id": 3, "root_asset_id": 1, "origin_grant_id": 1, ...}
      ],
      "edges": [{"parent_id": 1, "child_id": 2}, {"parent_id": 2, "child_id": 3}, {"parent_id": 3, "child_id": 4}]
    }
    ```
    Every node's `origin_purpose`/`origin_grant_expires_at` (omitted
    above for brevity) all resolve to the same original grant — the
    purpose did not disappear because the data was copied twice over.
- **Important decisions**:
  - **Route shape departs from the suggested example.** Rather than
    `POST /assets/{asset_id}/copies`, this uses a flat `POST /copies`
    (`parent_asset_id` in the body) — matching this project's existing
    pattern of modeling an *action* as its own top-level resource
    (`POST /retrievals`, not `POST /grants/{id}/retrievals`). Creating a
    copy is the same kind of thing as retrieving: an event that produces
    a new sealed asset, so it gets the same shape as retrieval, not a
    nested-under-asset shape.
  - **`derivation_type` is not a new `DataAsset` column.** It only
    decides which audit event to write (`COPY_CREATED` vs
    `DERIVED_ASSET_CREATED`) and which fingerprint function runs;
    everything else it might imply is already fully captured by the
    existing `parent_asset_id`/`root_asset_id`/`origin_grant_id`
    columns. Adding a column that exists purely to label an audit
    choice would be exactly the kind of unnecessary duplication earlier
    steps deliberately avoided (`resource_id`, purpose/expiry on
    `DataAsset`).
  - **Provenance is never accepted from the caller.** `CopyCreate` has
    no `root_asset_id` or `origin_grant_id` field at all, and uses
    `extra="forbid"` so supplying one is a hard `422`, not a silently
    ignored field. `root_asset_id` and `origin_grant_id` on the new
    child are *always* computed server-side from `parent`. This is what
    directly satisfies "cross-purpose misuse cannot silently change
    inherited provenance" — there is no code path where a caller's input
    can override the inherited grant/root.
  - **Copying from a never-retrieved asset is rejected**
    (`403 no_origin_grant`). An asset with `origin_grant_id = None` is
    an original/root that hasn't been legitimately retrieved under any
    purpose yet; letting someone "copy" it directly would produce a
    derivative with no purpose seal at all, which is exactly what this
    whole feature exists to prevent.
  - **Cycles are prevented structurally, not by a runtime check.** This
    endpoint only ever creates a *new* child row under an *existing*
    parent — there is no reparenting/update operation on `DataAsset` at
    all. A cycle would require an already-existing asset to become its
    own ancestor, which is impossible when children can only be created
    after, and pointing at, an already-persisted parent. No additional
    cycle-detection code was needed or added.
  - **Fingerprint strategy differs by `derivation_type`**: a `COPY`
    shares its root's fingerprint (same content, same hash — extending
    retrieval's existing approach); a `DERIVED` asset gets its own hash
    computed from its own name/type plus its parent's id
    (`compute_transformed_fingerprint`), since transformed content is,
    honestly, no longer the same content. This makes the project's
    already-documented fingerprint limitation ("can't detect
    transformation") concrete rather than papered over: a derived
    asset's hash *should* differ from its ancestor's.
  - **Same atomicity pattern as the Reliability Correction**: `db.add()`
    → `db.flush()` → `write_audit_log(...)` → single `db.commit()`,
    wrapped in `try/except: rollback(); raise`. A copy/derivation and
    its audit event are one transaction, same as grant creation and
    retrieval.
- **Files added**: `backend/app/api/{copies,assets}.py`,
  `backend/app/schemas/{copy,lineage}.py`,
  `backend/app/services/copy_service.py`, `backend/tests/test_lineage.py`.
- **Files modified**: `backend/app/models/enums.py` (added
  `DerivationType`), `backend/app/services/fingerprint.py` (added
  `compute_transformed_fingerprint`), `backend/app/services/asset_service.py`
  (added `get_asset`, `get_lineage`), `backend/app/schemas/__init__.py`,
  `backend/app/api/__init__.py` (registered both new routers).
- **Endpoints added**: `POST /copies`, `GET /assets/{id}`,
  `GET /assets/{id}/lineage`.
- **Tests added** (`backend/tests/test_lineage.py`, 15 tests, all
  against isolated per-test SQLite databases): direct copy creation;
  correct parent linkage through a 3-level chain; correct root linkage
  through the same chain; purpose provenance (`origin_grant_id`,
  `origin_purpose`, `origin_grant_expires_at`) preserved at every level;
  multiple independent descendants from the same parent; lineage query
  from the root; lineage query from a deep descendant returns the
  identical tree; invalid parent → `404`; an attempt to supply
  `origin_grant_id` directly is rejected with `422` (`extra="forbid"`);
  copying a never-retrieved asset → `403 no_origin_grant`; wrong actor
  → `403 actor_mismatch`; disallowed operation → `403
  operation_not_permitted`; `COPY_CREATED` audit event for an exact
  copy; `DERIVED_ASSET_CREATED` audit event for a derived asset; lineage
  survives a fresh session bound to the same database (simulating an
  application restart), read entirely independently of the session that
  created it.
- **Test result**: `57 passed, 2 warnings in 8.55s` — full suite (4
  atomicity + 8 domain model + 4 foundation + 17 grants + 15 lineage + 9
  retrieval), zero regressions. Also manually verified end-to-end
  against a live `uvicorn` process, producing the example lineage shown
  above.
- **Known limitations**: no revocation of an individual copy/derivative
  independent of its origin grant. No quarantine action yet (that's the
  violation-detection feature). `GET /assets/{id}/lineage` returns the
  *entire* tree regardless of size — fine at hackathon scale, would need
  pagination for a very large lineage tree in a non-demo setting.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Liveness check — `{status, app_name}` |
| POST | /grants | Create a purpose-bound access grant for an existing `asset_id` (requires `allowed_operations`, at least one) |
| GET | /grants | List all grants (with live-evaluated `status`) |
| GET | /grants/{id} | Get one grant by id (with live-evaluated `status`) |
| GET | /grants/{id}/status | Evaluate a grant's current status with an explanation (`reason_code`, `human_readable_reason`) |
| POST | /retrievals | Retrieve data under a grant; creates a purpose-sealed `DataAsset` copy or denies with a clear `error_code` |
| POST | /copies | Create a copy or derived asset from an existing, already-retrieved asset; re-checks actor/status/operation against its origin grant |
| GET | /assets/{id} | Get one asset by id, with its purpose seal resolved |
| GET | /assets/{id}/lineage | Get the full lineage tree (`{root_asset_id, nodes, edges}`) containing this asset |

No endpoint yet creates an *original* `DataAsset` — one must currently be
seeded directly via the ORM (as the tests do) before a grant can
reference it or a retrieval can happen against it.

## Demo Journey

1. Open the frontend shell — product name, layout, and a live "Backend
   connected" indicator confirm the stack is wired end-to-end.
2. (Not yet exposed via API) An original `DataAsset` exists, e.g.
   `patient_lab_104`.
3. `POST /grants` with an actor, purpose, `asset_id`, duration, and
   allowed operations — grant is created, `ACTIVE`, and audited.
4. `GET /grants/{id}/status` — shows `ACTIVE` with a
   `WITHIN_VALIDITY_WINDOW` explanation.
5. `POST /retrievals` with the grant id, the same actor, the same asset,
   and a permitted operation — a new, purpose-sealed `DataAsset` is
   created (linked to the original and the grant) and `DATA_RETRIEVED` is
   audited.
6. `POST /copies` with the retrieved asset's id as `parent_asset_id`, the
   same actor, and a permitted operation — a linked `analysis_dataset_1`
   is created (`DERIVED_ASSET_CREATED` audited), still carrying the same
   `origin_grant_id`/`origin_purpose` as the retrieved copy. Repeat
   against `analysis_dataset_1` to produce `derived_report_1` — the
   purpose survives two levels of derivation.
7. `GET /assets/{root_id}/lineage` — returns the whole tree (root →
   retrieved copy → analysis dataset → derived report) as nodes/edges,
   ready for a graph visualization.
8. Advance the simulated clock past the grant's expiry
   (`clock.advance(minutes=...)`, exercised in tests; not yet exposed via
   an endpoint) — `GET /grants/{id}/status` now shows `EXPIRED`, and a
   further `POST /retrievals` or `POST /copies` against the same grant is
   now denied (`403 grant_not_active`) with a denial audit event instead
   of creating anything.

(Later steps — purpose-violation detection on already-retrieved copies,
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
- **All DateTime columns use a custom `UTCDateTime` type, not SQLAlchemy's
  `DateTime(timezone=True)` directly** — SQLite silently drops timezone
  info on that type, which broke the very first `clock.now()` vs.
  `expires_at` comparison. Normalizing at the column-type level fixes it
  for every current and future timestamp comparison, not just one call
  site.
- **Grant status is computed at read time, never persisted as EXPIRED**
  — the stored `status` column is an explicit lifecycle flag (set only by
  creation or a future revoke action); "is this grant currently active"
  is always derived from `expires_at` vs. `clock.now()` on demand, so a
  GET request can never have the side effect of mutating a row.
- **`allowed_operations` is required, not defaulted** (changed from the
  previous step) — this step's explicit validation rules asked for it
  alongside actor/purpose as a required field, not an optional one.
- **Purpose/expiry answered by joining through `origin_grant_id`, never
  duplicated onto `DataAsset`** — see "Purpose Seal propagation" under
  Database Model. Consistent with the earlier `resource_id` removal: one
  authoritative source per fact, computed on read rather than copied at
  write time.
- **Fingerprint hashes the root asset's stable identity, not the
  retrieval event** — every copy retrieved from the same original gets
  the same SHA-256 fingerprint, the way a real content hash would;
  documented explicitly as unable to detect transformed/derived content,
  only byte-identical copies (see the retrieval feature's known
  limitations).
- **Failed retrieval writes a distinctly-typed `DATA_RETRIEVAL_DENIED`
  audit event, never `DATA_RETRIEVED`** — keeps the audit trail
  unambiguous about what actually succeeded, per the project's
  auditability rule against faking successful entries.
- **A business record and its success audit event share one commit, not
  two** — `grant_service.create_grant` and `retrieval_service.retrieve_data`
  now `flush()` (for the id), write the audit entry, and `commit()` once,
  wrapped in `try/except: rollback(); raise`. Prevents the exact
  partial-state risk of a persisted grant/asset with no audit record
  proving it happened — see the Reliability Correction entry.
- **`GET /copies`/`POST /copies` re-validate the origin grant on every
  call rather than trusting the parent asset's existing seal** — a copy
  is itself a use of the grant (structurally identical to retrieval:
  actor/status/operation), so it gets the same live checks, not a
  cheaper "the parent was already legitimate once" shortcut.
- **Cycle prevention is structural, not a runtime check** — `DataAsset`
  rows are never reparented or updated after creation, only ever
  created fresh under an already-existing parent, so a cycle is
  impossible by construction. See the lineage feature's design decision.

## Known Issues / Deferred Work

- Purpose-violation detection (evaluating *continued* use of
  already-retrieved data after its grant expires) is not implemented —
  copy/derivation creation currently re-checks the grant is active, but
  nothing yet detects or reacts to use of data that was retrieved
  *before* the grant expired and is used again *after*. Nothing
  quarantines an asset yet.
- No revoke-grant endpoint yet (the evaluation logic supports `REVOKED`,
  but nothing can set it).
- No endpoint exposes simulated time advancement (`clock.advance()`) —
  only reachable from tests/scripts today.
- No `UsageDecision` table yet — deferred until the policy-evaluation
  feature defines its real shape (see design decision above).
- No revocation of an individual copy/derivative independent of its
  origin grant. `GET /assets/{id}/lineage` returns the entire tree with
  no pagination — fine at hackathon scale.
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
- **Purpose Grant Lifecycle — status evaluation and stricter validation**:
  recommended commit message `feat(grants): add purpose-bound access lifecycle`.
- **Data Retrieval and Purpose Seal Propagation**: recommended commit
  message `feat(retrieval): propagate purpose seal to retrieved data`.
- **Reliability Correction — atomic domain writes and audit events**:
  recommended commit message `fix(audit): make domain writes and audit events atomic`.
- **Copy and Derived-Data Lineage**: recommended commit message
  `feat(lineage): track copied and derived data provenance`.

## Next Step

Begin purpose-violation detection: evaluate continued use (retrieval,
copy, or derivation attempts) against already-retrieved data once its
originating grant has expired, and take a corrective action (blocking
use, quarantining the asset via `AssetState.QUARANTINED`), per the
hackathon priority list.
