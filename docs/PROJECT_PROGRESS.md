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
    `Remediation`, `User`, plus shared enums `AllowedOperation`,
    `AssetState`, `GrantStatus`, `RemediationStatus`, `UserRole`).
  - `schemas/` — Pydantic request/response schemas.
  - `db/` — `base.py` (declarative `Base`) and `session.py` (engine,
    `SessionLocal`, `get_db`, `init_db`).
  - `core/` — cross-cutting concerns: `config.py` (centralized
    `Settings`), `clock.py` (time abstraction), `errors.py` (`AppError`
    hierarchy + exception handlers), `security.py` (password hashing +
    access tokens — see "Actor Roles and Minimal Authentication" under
    Implemented Features).
  - `api/deps.py` — authentication/role-authorization dependencies
    (`get_current_user`, `get_current_user_optional`, `require_role`),
    deliberately separate from `services/policy_service.py`'s purpose
    authorization — see the same feature entry for why.
- **Frontend**: React 19 + Vite 8 + Tailwind CSS v4 (via `@tailwindcss/vite`,
  no separate `tailwind.config.js`/PostCSS needed under v4). `Layout`
  (header with product name + placeholder nav) and `HealthIndicator`
  (polls backend `/health` on mount) wrap a single `Dashboard` screen —
  see "Minimal Judge-Ready Dashboard" and "Interactive Data Lineage
  Visualization" under Implemented Features. Data lineage renders via
  `@xyflow/react` (React 19-compatible; no graph database, no separate
  backend). All API calls go through `src/lib/api.js` (a thin `fetch`
  wrapper reading `VITE_API_BASE_URL`, default `http://localhost:8000`),
  never a raw `fetch()` call inside a component.
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
- **Remediation** (`remediations` table, added by the Stronger
  Remediation Workflow feature) — a persisted, explicit record of a
  purpose-lifecycle violation's required follow-up, created the moment
  the policy engine quarantines an asset.
  - `id`, `asset_id` (FK to `data_assets.id`, not nullable — the
    affected copy), `grant_id` (FK to `grants.id`, nullable), `reason_code`
    (e.g. `PURPOSE_EXPIRED`), `reason` (the human-readable explanation at
    the moment of violation), `corrective_action` (plain-language next
    step), `status` (`RemediationStatus` — currently only
    `COMPLIANCE_REVIEW_REQUIRED`), `created_at`. Unlike `AuditLog`, this
    table has a genuinely mutable field (`status`) a future review
    workflow would update in place — see the design decision under
    "Usage/policy decision entity" above for why this didn't get folded
    into that already-deferred idea, and the feature entry under
    Implemented Features for full reasoning. Rows here are never
    deleted.
- **User** (`users` table, added by the Actor Roles and Minimal
  Authentication feature) — a login identity: "who is this caller, and
  what role do they hold." Deliberately **not** referenced by
  `Grant`/`DataAsset`/`AuditLog`/`Remediation` via foreign key — those
  keep their existing free-text `subject`/`actor` strings unchanged.
  See "Actor Roles and Minimal Authentication" under Implemented
  Features for why authentication intentionally doesn't reach into the
  domain model beyond setting what string gets used at the API
  boundary.
  - `id`, `username` (unique), `password_hash`, `password_salt`
    (PBKDF2-SHA256, see `core/security.py`), `role` (`UserRole` —
    `RESEARCHER` / `CLINICIAN` / `ANALYST` / `COMPLIANCE_OFFICER` /
    `ADMIN`), `created_at`.

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

### Usage/policy decision entity — design decision (resolved)

**Update (Stronger Remediation Workflow feature): a *different*
dedicated table — `Remediation`, not `UsageDecision` — was added, and
the reasoning below for why `UsageDecision` stays deferred still
holds.** `Remediation` isn't "all decisions, filterable/paginated" (the
thing this section says isn't needed yet); it exists because a
violation's remediation has a genuinely *mutable* field —
`status` — that a future review workflow would update in place, unlike
an `AuditLog` row or a `PolicyDecision` response, both of which are a
permanent record of a single moment and are never rewritten. See
"Stronger Remediation Workflow" under Implemented Features.

**Update (Expiry, Policy Evaluation, and Continued-Use Violation
feature): still no dedicated `UsageDecision` table — the policy engine
has now landed, and `AuditLog` proved sufficient.** Every use evaluation
writes `DATA_USE_ATTEMPTED`, then one of `USE_ALLOWED` /
`USE_BLOCKED` / `PURPOSE_VIOLATION` (see `services/policy_service.py`),
each carrying the full decision context in `details`. The live
`PolicyDecision` returned by `POST /uses` already gives callers the
required structured shape (`decision`, `reason_code`, `reason`,
`asset_id`, `grant_id`, `evaluated_at`) at the moment of evaluation —
querying *past* decisions means querying `AuditLog` by `entity_id`,
which every test in `test_policy.py` already does successfully. A
dedicated table would only pay for itself if a query like "all
decisions across all assets, filterable/paginated" were actually needed;
nothing in this project requires that yet, so it stays deferred rather
than built speculatively.

<details><summary>Original deferral reasoning (superseded, kept for history)</summary>

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

</details>

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

### Expiry, Policy Evaluation, and Continued-Use Violation — MAJOR CHECKPOINT MILESTONE

This is the feature the whole project exists to demonstrate: detecting
and reacting to continued use of data that was legitimately retrieved,
after the purpose that justified it has expired or changed — not merely
revoking future access to the source.

- **What was implemented**:
  - `POST /uses` — evaluates an attempted use
    (`{asset_id, actor, purpose, operation}`) of an **already-retrieved
    or derived** asset against the purpose that originally justified it.
    Always returns HTTP `200` with a structured decision — `DENY` is a
    valid, successful evaluation outcome, not an API error (unlike
    retrieval/copy, which reject with 403/404 because those are
    mutations that either happen or don't; a use evaluation's entire job
    *is* to produce ALLOW-or-DENY, so treating DENY as an HTTP error
    would be modeling it wrong).
  - `services/policy_service.py:evaluate_use` — the policy engine, a
    pure function of: `asset.state`, `asset.origin_grant_id`, the
    inherited `Grant` (`subject`, `purpose`, `allowed_operations`),
    `grant_service.evaluate_grant_status` (reused, not reimplemented),
    the request's claimed `actor`/`purpose`/`operation`, and
    `clock.now()`. Checks run in this order, first match wins:
    1. `asset.state == QUARANTINED` → `DENY ASSET_QUARANTINED`
    2. `asset.origin_grant_id is None` → `DENY NO_ORIGIN_GRANT`
    3. `grant.subject != actor` → `DENY ACTOR_MISMATCH`
    4. `purpose != grant.purpose` → `DENY PURPOSE_MISMATCH`
    5. `operation not in grant.allowed_operations` → `DENY OPERATION_NOT_PERMITTED`
    6. grant evaluates to `EXPIRED` → `DENY PURPOSE_EXPIRED`
    7. grant evaluates to `REVOKED` → `DENY GRANT_REVOKED`
    8. otherwise → `ALLOW WITHIN_PURPOSE_AND_VALIDITY`
  - Every call writes `DATA_USE_ATTEMPTED` unconditionally first (its own
    immediate commit — an audit-only fact, same pattern as retrieval's
    denied-attempt logging), then exactly one outcome event: `USE_ALLOWED`
    on ALLOW; `PURPOSE_VIOLATION` + `ASSET_QUARANTINED` (one atomic
    commit, quarantining the asset) for reasons 6–7 and 4; `USE_BLOCKED`
    (no quarantine) for reasons 1–3 and 5. See the quarantine-scope
    design decision below for exactly which reasons quarantine.
  - **Simulation clock exposed over HTTP, clearly marked as
    dev-only**: `GET /dev/clock` and `POST /dev/clock/advance`
    (`{minutes, seconds, hours}`) — thin wrappers around the existing
    `Clock` singleton, registered under the project's clock abstraction.
    Namespaced under `/dev` (distinct from every business router) and
    only registered at all when `settings.enable_dev_endpoints` is true
    (new config flag, defaults to `True` for this hackathon build,
    documented as needing to be `False` — `PURPOSESEAL_ENABLE_DEV_ENDPOINTS=false`
    — for anything resembling a real deployment). Tests already
    manipulated `clock.advance()` directly in Python; this closes the
    gap so the *demo* can advance time via the same HTTP surface
    everything else uses, without a real 30-minute wait.
  - Verified all three scenarios from the spec end-to-end against a live
    `uvicorn` process — see the worked example below.
- **Important decisions**:
  - **Quarantine scope: only purpose-lifecycle reasons quarantine.**
    `PURPOSE_EXPIRED`, `PURPOSE_MISMATCH`, and `GRANT_REVOKED` — the
    reasons that mean "the purpose itself no longer covers this data,"
    which is literally this feature's subject — quarantine the asset.
    `ACTOR_MISMATCH`, `OPERATION_NOT_PERMITTED`, and
    `ASSET_QUARANTINED`-already do **not** additionally quarantine
    anything; they're ordinary access-control denials (an unauthorized
    party touching legitimately-purposed data, or a permitted actor
    asking for something the grant never covered) — the data itself
    isn't at fault, so it isn't punished. This mirrors the project's own
    "avoid punishing the source record merely because one derived copy
    was misused" instruction, applied one level down: avoid punishing a
    *legitimately purposed* asset merely because a request against it
    was invalid for reasons unrelated to purpose lifecycle. This was a
    genuine judgment call (the task's "Required violation response"
    section doesn't explicitly scope which denials quarantine) —
    documented here precisely so it's easy to revisit.
  - **`POST /uses` always returns 200; DENY is not an HTTP error.**
    Contrast with retrieval/copy, which use 403/404 because those
    represent "this write did or didn't happen." A use evaluation
    doesn't write anything conditional on its own outcome (the asset
    already exists) — its entire purpose is to produce a decision, so
    the decision *is* the response body, not an exception.
  - **`evaluate_grant_status` reused verbatim**, not reimplemented — the
    policy engine has exactly one place that decides "is this grant
    currently valid," used identically by `GET /grants/{id}/status`,
    retrieval, copy creation, and now use evaluation.
  - **No `UsageDecision` table** — see the resolved design decision
    under Database Model above.
  - **Dev clock is a real, if minimal, security boundary, not just a
    naming convention** — gated behind a settings flag that a real
    deployment must flip off, not merely parked under a `/dev` prefix
    that anyone could still call.
  - **Same atomicity pattern throughout**: the one place a business
    state change happens (quarantine) shares a single commit with its
    two audit events, wrapped in `try/except: rollback(); raise`,
    consistent with the Reliability Correction.
- **Worked example (from a live server)**:
  ```
  Setup: grant(researcher_01, clinical_trial_screening, VIEW/ANALYZE/COPY, 30 min)
         → retrieve patient_lab_104 → retrieved copy (asset id 2)

  Scenario A — valid continued use:
    POST /uses {asset_id: 2, actor: researcher_01, purpose: clinical_trial_screening, operation: ANALYZE}
    → 200 {"decision": "ALLOW", "reason_code": "WITHIN_PURPOSE_AND_VALIDITY", ...}

  Scenario C — wrong purpose (on a sibling copy, asset id 3):
    POST /uses {asset_id: 3, actor: researcher_01, purpose: marketing_analytics, operation: ANALYZE}
    → 200 {"decision": "DENY", "reason_code": "PURPOSE_MISMATCH",
            "reason": "This data was retrieved for 'clinical_trial_screening', not 'marketing_analytics'."}
    → asset 3 state: QUARANTINED

  Scenario B — expired purpose (advance clock 31 minutes past the 30-minute grant):
    POST /dev/clock/advance {"minutes": 31}
    POST /uses {asset_id: 2, actor: researcher_01, purpose: clinical_trial_screening, operation: ANALYZE}
    → 200 {"decision": "DENY", "reason_code": "PURPOSE_EXPIRED",
            "reason": "The purpose grant authorizing this data expired at ... and was evaluated at ..."}
    → asset 2 state: QUARANTINED
    → root asset (patient_lab_104) state: ACTIVE (untouched)
  ```
- **Files added**: `backend/app/api/{uses,dev}.py`,
  `backend/app/schemas/{use,policy,dev}.py`,
  `backend/app/services/policy_service.py`, `backend/tests/{test_policy,test_dev_clock}.py`.
- **Files modified**: `backend/app/core/config.py` (added
  `enable_dev_endpoints`), `backend/app/schemas/__init__.py`,
  `backend/app/api/__init__.py` (registered `/uses` unconditionally,
  `/dev` conditionally on the new setting).
- **Endpoints added**: `POST /uses`, `GET /dev/clock`,
  `POST /dev/clock/advance`.
- **Tests added**:
  - `backend/tests/test_policy.py` (17 tests): use while active → ALLOW;
    use exactly one minute before expiry → ALLOW (boundary check); use
    after expiry → DENY `PURPOSE_EXPIRED` + quarantine; purpose mismatch
    → DENY `PURPOSE_MISMATCH` + quarantine; unsupported operation → DENY
    `OPERATION_NOT_PERMITTED`, **not** quarantined; wrong actor → DENY
    `ACTOR_MISMATCH`, not quarantined; expired use creates a
    `PURPOSE_VIOLATION` audit event with the right `reason_code` in its
    details; violating copy becomes `QUARANTINED`; root asset stays
    `ACTIVE` when a derived copy is quarantined; a quarantined asset's
    second use attempt is denied again without a duplicate
    `ASSET_QUARANTINED` event; a legitimate sibling asset (same parent,
    different branch) remains fully usable after its sibling is
    quarantined; every response (ALLOW and DENY) includes a non-empty
    `reason`; use of a never-retrieved asset → DENY `NO_ORIGIN_GRANT`;
    use of a nonexistent asset → `404`; response includes `evaluated_at`.
  - `backend/tests/test_dev_clock.py` (3 tests): `GET /dev/clock`
    returns a valid timestamp; `POST /dev/clock/advance` actually moves
    simulated time forward by the requested amount; omitting all fields
    is a no-op (only real time elapses).
- **Test result**: `75 passed, 2 warnings in 8.85s` — full suite (4
  atomicity + 3 dev clock + 8 domain model + 4 foundation + 17 grants +
  15 lineage + 17 policy + 9 retrieval), zero regressions. Also manually
  verified all three required scenarios (A/B/C) end-to-end against a
  live `uvicorn` process — see the worked example above.
- **Known limitations**: no way to un-quarantine an asset (no
  "restore"/appeal flow — matches the project's scope, which asks for
  detection and corrective action, not remediation). ~~No revoke-grant
  endpoint yet, so `GRANT_REVOKED` is reachable in code but not yet
  triggerable through any API call.~~ **Resolved** by the "Operational
  APIs" feature below. `POST /uses` doesn't itself create any new
  `DataAsset` — it only evaluates and, on a purpose-lifecycle violation,
  mutates the existing asset's state; this is intentional (the scenario
  is "use of already-retrieved data," not another retrieval).

### Operational APIs — Source Asset Management and Grant Revocation

**Goal**: remove the last requirement to manipulate SQLite directly.
Before this feature, an original `DataAsset` could only be created by
seeding it through the ORM (as `conftest.py`'s `existing_asset` fixture
does); there was also no way to trigger `GRANT_REVOKED`, a reason code
the policy engine already understood but that no API path produced.
After this feature, the entire lifecycle — original asset → grant →
retrieval → copy/derived asset → use evaluation → revocation — is
reachable purely over HTTP.

- **`POST /assets`** — creates an original/root `DataAsset` from just
  `name` and `asset_type`. `DataAssetCreate` uses `extra="forbid"`
  (`backend/app/schemas/data_asset.py`), the same pattern as
  `CopyCreate`, so a caller cannot supply `parent_asset_id`,
  `root_asset_id`, `origin_grant_id`, or `state` — those are hard-coded
  server-side to `None`/`None`/`None`/`ACTIVE`. This is deliberately the
  *only* way to create a root asset; a "retrieved" or "derived" asset
  still can only come from `/retrievals` or `/copies`, which is what
  keeps the purpose-seal model trustworthy (a client can never forge
  provenance by pretending a fabricated asset was legitimately
  retrieved).
  - Audited as `SOURCE_ASSET_CREATED` — a new, distinctly-named event
    (not `DATA_RETRIEVED`/`COPY_CREATED`), since creating a root asset
    is a different fact than retrieving or deriving one.
  - Follows the established flush-then-audit-then-commit atomic pattern
    (`asset_service.create_asset`).
- **`GET /assets`** — lists assets with optional `state`, `asset_type`,
  and `root_only` (`parent_asset_id IS NULL`) query filters, each
  applied only when provided; no pagination, per the "don't overengineer
  query syntax" instruction. Reuses the existing `DataAssetOut`
  serialization (`asset_service.to_data_asset_out`), so list rows carry
  the same purpose-seal fields (`effective_root_asset_id`,
  `origin_grant_id`, `origin_purpose`, …) as the single-asset endpoint.
  `GET /assets/{id}` (already existed) was reused unchanged.
- **`POST /grants/{id}/revoke`** — marks a grant's stored `status` as
  `REVOKED` and audits `GRANT_REVOKED`. The grant row is never deleted;
  its provenance and audit trail must survive, per the task's explicit
  instruction. Idempotent: revoking an already-revoked grant returns the
  grant unchanged, `200`, without writing a second `GRANT_REVOKED`
  event — see the "idempotency decision" below.
  - Deliberately contains **no** policy logic of its own.
    `policy_service.evaluate_use` already checked
    `evaluate_grant_status(grant).status == GrantStatus.REVOKED` and
    already quarantined on that reason (`GRANT_REVOKED` was already in
    `PURPOSE_LIFECYCLE_REASONS`) before this feature existed — the
    revoke endpoint only had to make that stored state reachable via
    HTTP. Nothing about the policy engine changed.
- **Idempotency decision** (explicitly requested by the task): repeat
  revocation is a **safe no-op**, not a conflict response. Rationale —
  a grant's revocation is a single fact ("this was explicitly revoked"),
  not a repeatable action; a second identical call carries no new
  information, and writing a second `GRANT_REVOKED` event would
  misleadingly suggest two separate lifecycle transitions happened. A
  `409 Conflict` was the rejected alternative: it would force every
  caller (including an idempotent retry after a dropped response) to
  special-case "already revoked" as an error, when nothing has actually
  gone wrong.

**Worked example (from a live server)**:
```
POST /assets {"name": "Patient Lab Result #104", "asset_type": "lab_result"}
-> 201 {"id": 1, "parent_asset_id": null, "root_asset_id": null,
        "origin_grant_id": null, "state": "ACTIVE", ...}

POST /grants {...asset_id: 1...}          -> 201 {"id": 1, "status": "ACTIVE", ...}
POST /retrievals {...grant_id: 1...}      -> 201 {"id": 2, ...}
POST /copies {...parent_asset_id: 2...}   -> 201 {"id": 3, "state": "ACTIVE", ...}

POST /grants/1/revoke   -> 200 {"id": 1, "status": "REVOKED", ...}
POST /grants/1/revoke   -> 200 {"id": 1, "status": "REVOKED", ...}  (no 2nd audit event)

POST /uses {"asset_id": 3, "actor": "researcher_01",
            "purpose": "clinical_trial_screening", "operation": "ANALYZE"}
-> 200 {"decision": "DENY", "reason_code": "GRANT_REVOKED",
        "reason": "This grant was explicitly revoked.", ...}

GET /assets/3  -> "state": "QUARANTINED"
GET /assets/1  -> "state": "ACTIVE"   (root asset untouched)
```

- **Files added**: `backend/tests/test_source_assets.py`,
  `backend/tests/test_grant_revocation.py`,
  `backend/tests/test_full_lifecycle_via_http.py`.
- **Files modified**: `backend/app/schemas/data_asset.py` (added
  `DataAssetCreate`), `backend/app/schemas/__init__.py`,
  `backend/app/services/asset_service.py` (added `create_asset`,
  `list_assets`), `backend/app/api/assets.py` (added `POST`/`GET ""`),
  `backend/app/services/grant_service.py` (added `revoke_grant`),
  `backend/app/api/grants.py` (added `POST /{grant_id}/revoke`).
- **Endpoints added**: `POST /assets`, `GET /assets`,
  `POST /grants/{id}/revoke`.
- **Tests added** (27 new):
  - `test_source_assets.py` (18 tests): valid creation; no parent; no
    root pointer; no origin grant; starts `ACTIVE`;
    `SOURCE_ASSET_CREATED` audit event exists; creation+audit atomic
    (simulated audit failure leaves nothing committed); missing/empty
    name rejected; missing asset type rejected; caller cannot inject
    `parent_asset_id`/`root_asset_id`/`origin_grant_id`/`state`
    (each `422`, since `DataAssetCreate` uses `extra="forbid"`); list
    works; list filters by `state`/`asset_type`/`root_only`; retrieve
    by id; nonexistent asset → clean `404`.
  - `test_grant_revocation.py` (7 tests): valid grant revoked; persisted
    status becomes `REVOKED`; `GRANT_REVOKED` audit event exists;
    revocation+audit atomic; nonexistent grant → `404`; repeated
    revocation is deterministic and idempotent (single audit event,
    `200` both times).
  - `test_full_lifecycle_via_http.py` (1 integration test): create
    asset → grant → retrieve → derive a copy → revoke the grant →
    attempt to use the copy → confirm `DENY`/`GRANT_REVOKED` → confirm
    the copy is quarantined and the root asset is untouched — all
    through HTTP, proving no ORM seeding is required anywhere in the
    loop.
- **Test result**: `102 passed, 2 warnings in 15.32s` — full suite (75
  prior + 27 new), zero regressions. Also manually verified the full
  HTTP-only lifecycle end-to-end against a live `uvicorn` process,
  including idempotent double-revocation — see the worked example
  above.
- **Known limitations**: no un-revoke/reinstate endpoint (matches the
  project's "no remediation" scope, same as quarantine). No way to
  edit or delete an asset once created. `root_only=true` uses
  `parent_asset_id IS NULL`, which also happens to match "has no
  origin grant" for every asset in the current model — the two concepts
  aren't distinguished because nothing yet needs an asset with a parent
  but no origin grant.

### Deterministic Demo Scenario Engine

**Goal**: let a judge (or the frontend) trigger the complete PurposeSeal
journey — legitimate use, purpose expiry, purpose mismatch — with a
single API call each, with zero manual grant/retrieval setup and zero
randomness, so the demo is 100% reproducible.

- **`backend/app/services/demo_service.py`** — three scenario functions
  (`run_legitimate_scenario`, `run_expired_scenario`,
  `run_purpose_mismatch_scenario`), each calling the *existing*
  `asset_service` / `grant_service` / `retrieval_service` /
  `copy_service` / `policy_service` functions in sequence — there is no
  separate "demo" business logic path, only canned actors/purposes/
  durations (`researcher_01`, `clinical_trial_screening`,
  `marketing_analytics`) standing in for what a human would otherwise
  type in by hand. Time advancement (the expired scenario) uses
  `clock.advance()` directly, the same simulated-clock abstraction
  `/dev/clock/advance` exposes — no `time.sleep`, no real waiting.
- **Full timeline reconstruction**: `_collect_timeline()` queries
  `AuditLog` for every event touching the scenario's asset(s)/grant,
  ordered by primary key (insertion order) rather than `created_at` —
  several writes in the same scenario can share the exact same
  simulated instant (e.g. everything before a `clock.advance()` call),
  so timestamp ordering alone isn't reliable, but commit order always
  is.
- **No collisions across runs, by construction, not by cleanup**: every
  scenario calls `asset_service.create_asset`/`grant_service.create_grant`
  fresh each time, so every run gets brand-new autoincrement ids. There
  is no shared "the demo asset" row to collide on, reset, or leak state
  between runs — each response is entirely self-contained (its own
  asset/grant ids and its own timeline).
- **Remediation field**: `expired` and `purpose-mismatch` responses
  include a `remediation` string (plain-language next step, e.g. "issue
  a new purpose grant for X if legitimate"); `legitimate` returns
  `remediation: null` since nothing needs correcting.
- **New schemas**: `backend/app/schemas/demo.py` —
  `TimelineEventOut` (`event_type`, `entity_type`, `entity_id`,
  `details`, `created_at`) and `DemoScenarioOut` (`scenario`, `decision`,
  `reason_code`, `reason`, `asset_id`, `grant_id`, `remediation`,
  `timeline`).
- **New router**: `backend/app/api/demo.py`, prefix `/demo/scenarios`,
  registered only when `settings.enable_dev_endpoints` is true — the
  same gate as `/dev/clock*`, since this is demo scaffolding, not
  product functionality a real deployment should expose.

**Worked example (from a live server, three separate scenario calls)**:
```
POST /demo/scenarios/legitimate
-> {"decision": "ALLOW", "reason_code": "WITHIN_PURPOSE_AND_VALIDITY",
    "remediation": null,
    "timeline": [SOURCE_ASSET_CREATED, GRANT_CREATED, DATA_RETRIEVED,
                 COPY_CREATED, DATA_USE_ATTEMPTED, USE_ALLOWED]}

POST /demo/scenarios/expired
-> {"decision": "DENY", "reason_code": "PURPOSE_EXPIRED",
    "remediation": "Issue a new purpose grant against the original asset...",
    "timeline": [SOURCE_ASSET_CREATED, GRANT_CREATED, DATA_RETRIEVED,
                 DERIVED_ASSET_CREATED, DATA_USE_ATTEMPTED,
                 PURPOSE_VIOLATION, ASSET_QUARANTINED]}

POST /demo/scenarios/purpose-mismatch
-> {"decision": "DENY", "reason_code": "PURPOSE_MISMATCH",
    "remediation": "If 'marketing_analytics' is a legitimate use case...",
    "timeline": [SOURCE_ASSET_CREATED, GRANT_CREATED, DATA_RETRIEVED,
                 DATA_USE_ATTEMPTED, PURPOSE_VIOLATION, ASSET_QUARANTINED]}

Rerunning /demo/scenarios/legitimate immediately afterward returned a
brand-new asset_id (11 vs. 3 the first time) with the same ALLOW
decision -- confirming no collision and full determinism.
```

- **Files added**: `backend/app/services/demo_service.py`,
  `backend/app/schemas/demo.py`, `backend/app/api/demo.py`,
  `backend/tests/test_demo_scenarios.py`.
- **Files modified**: `backend/app/api/__init__.py` (registers
  `demo_router` alongside `dev_router`), `backend/app/schemas/__init__.py`.
- **Endpoints added**: `POST /demo/scenarios/legitimate`,
  `POST /demo/scenarios/expired`, `POST /demo/scenarios/purpose-mismatch`.
- **Tests added** (10 tests in `test_demo_scenarios.py`): legitimate
  scenario returns `ALLOW`; expired scenario returns `DENY`; mismatch
  scenario returns `DENY`; exact expected audit-event sequence for each
  of the three scenarios (list equality, not just "contains"); expected
  asset state after each scenario (`ACTIVE` for legitimate,
  `QUARANTINED` for the other two); scenarios can be rerun safely with
  no id collision; all three scenarios produce identical decisions and
  reason codes across repeated runs (determinism); `time.sleep` is
  proven never called (via `monkeypatch`, not just an elapsed-time
  guess) across all three scenarios; every timeline event carries a
  timestamp and a valid `entity_type`.
- **Test result**: `112 passed, 2 warnings in 18.56s` — full suite (102
  prior + 10 new), zero regressions. Also manually verified all three
  scenarios end-to-end against a live `uvicorn` process, including a
  rerun collision check — see the worked example above.
- **Known limitations**: the three scenarios are fixed (not
  parameterizable — actor/purpose/durations are hard-coded), matching
  the "do not overengineer" instruction; a frontend wanting a different
  actor or purpose for its own demo would need a new scenario function,
  not a request parameter. Demo data accumulates in the database across
  repeated runs (each run's rows are never deleted) — acceptable for a
  hackathon demo session, but a long-running demo server would
  eventually want a way to reset/clear it.

### Minimal Judge-Ready Dashboard

**Goal**: the first usable frontend — a single dashboard that
communicates the whole PurposeSeal concept in under 30 seconds, so a
judge never needs Postman/curl during a demo. No backend changes; this
feature is purely a frontend consumer of the APIs that already exist
(`GET /grants`, `GET /assets`, `GET /assets/{id}`,
`POST /demo/scenarios/*`).

- **`src/lib/api.js`** gained a small `request()` wrapper (shared by
  every API function) that turns a network failure into "Could not
  reach the PurposeSeal backend. Is it running?" and a non-2xx response
  into its backend-provided `message` when present, or a generic
  "Request failed with status N" otherwise — the UI never shows a raw
  stack trace or an `Error: ...` string. New functions: `listGrants`,
  `listAssets(params)`, `getAsset(id)`, `runScenario(key)`.
- **`Dashboard.jsx`** is the single screen, composed of four
  presentational sections:
  - **`SummaryMetrics`** — Active Grants, Tracked Copies, Violations
    Detected, Quarantined Assets. **Active Grants** (`GET /grants`,
    live-evaluated `status === 'ACTIVE'`), **Tracked Copies**
    (`GET /assets`, count of `parent_asset_id !== null`), and
    **Quarantined Assets** (`GET /assets?state=QUARANTINED`) are always
    freshly fetched from the backend, refreshed after every scenario
    run. **Violations Detected** is the one exception — see the design
    decision below.
  - **`ScenarioControls`** — the three buttons (`Run Valid Scenario`,
    `Run Expired-Purpose Scenario`, `Run Purpose-Mismatch Scenario`),
    calling `POST /demo/scenarios/{legitimate,expired,purpose-mismatch}`
    directly. All three buttons disable together while any one is
    running (not just the clicked one) and the active button reads
    "Running…", with a `role="status"` line underneath — this is the
    dashboard's whole loading-state story, deliberately not a spinner
    ("no complex animation").
  - **`ScenarioResult`** — after a run: **ALLOW**/**BLOCKED** (mapped
    from the API's `ALLOW`/`DENY`), **Why?** (`reason`), **Original
    Purpose** and **Expiry** (from the evaluated asset's
    `origin_purpose`/`origin_grant_expires_at`, fetched via
    `GET /assets/{id}` right after the scenario call — the scenario
    response itself only carries `asset_id`, not the asset's
    provenance), **Requested Purpose** (the `purpose` field on the
    timeline's `DATA_USE_ATTEMPTED` event — the actual value the policy
    engine evaluated, not a guessed/hardcoded one), **Asset** (the
    fetched asset's `name`), and **Corrective Action** (the scenario
    response's `remediation`, or a fixed "no action needed" message
    when `remediation` is `null`, i.e. on `ALLOW`). Before any run, and
    on an error, this section never renders blank — see the design
    decisions below.
  - **`JourneyTimeline`** — renders the scenario response's `timeline`
    array as a chronological list. Each event's raw `event_type` is
    mapped to a human label (`GRANT_CREATED` → "Grant Created",
    `ASSET_QUARANTINED` → "Asset Quarantined", etc.), with red/green/
    grey status dots (red for `PURPOSE_VIOLATION`/`USE_BLOCKED`/
    `ASSET_QUARANTINED`, green for `USE_ALLOWED`).
- **"Purpose Expired" has no dedicated backend audit event** — as
  documented under the policy feature, an expiry-caused denial is
  logged as `PURPOSE_VIOLATION` with `reason_code: "PURPOSE_EXPIRED"`
  in its `details`, not as its own event type. Rather than adding a new
  backend event (out of scope — "do not redesign backend
  architecture"), `JourneyTimeline` reads that `reason_code` and labels
  the step "Purpose Expired — Violation Detected" (or "Purpose Mismatch
  — Violation Detected") purely at render time. This is a frontend
  presentation decision layered on existing data, not a new backend
  concept.
- **"Violations Detected" is a session-local counter, not a backend
  query** — no `GET`-all-audit-events endpoint exists yet (deferred;
  see Known Issues), and since every purpose-lifecycle violation
  quarantines its asset exactly once, a true backend count would always
  exactly equal the Quarantined Assets count anyway, making the two
  numbers redundant. Instead, the dashboard counts `PURPOSE_VIOLATION`
  events actually returned by scenario runs performed in the current
  browser session (resets on page reload). This is deliberately framed
  as "this session" in the UI (a caption under the metric) rather than
  implied to be a global, persistent count.

**Worked example (from a real backend + a real Vite dev server, exact
request/response contract verified with `curl`, not just component
tests with mocked `fetch`)**:
```
GET  /grants                              -> []
GET  /assets                              -> []
GET  /assets?state=QUARANTINED            -> []
POST /demo/scenarios/legitimate           -> {"decision": "ALLOW", "asset_id": 3, ...}
GET  /assets/3                            -> {"name": "analysis_dataset_1 (Legitimate Use Demo)",
                                               "origin_purpose": "clinical_trial_screening",
                                               "state": "ACTIVE", ...}
GET  /grants (metrics refresh)            -> [{"status": "ACTIVE", ...}]      => Active Grants: 1
GET  /assets (metrics refresh)            -> 3 assets, 2 with parent_asset_id => Tracked Copies: 2

POST /demo/scenarios/expired              -> {"decision": "DENY", "reason_code": "PURPOSE_EXPIRED", ...}
POST /demo/scenarios/purpose-mismatch     -> {"decision": "DENY", "reason_code": "PURPOSE_MISMATCH", ...}
GET  /assets?state=QUARANTINED            -> 2 assets                        => Quarantined Assets: 2
```
A CORS preflight (`OPTIONS /demo/scenarios/legitimate` with
`Origin: http://localhost:5173`) against the real backend confirmed
`access-control-allow-origin: http://localhost:5173` — the default
`cors_origins` setting already covers the frontend's default dev port,
no configuration change needed.

- **Files added**: `frontend/src/components/{Dashboard,SummaryMetrics,
  ScenarioControls,ScenarioResult,JourneyTimeline}.jsx`,
  `frontend/src/components/Dashboard.test.jsx`.
- **Files modified**: `frontend/src/lib/api.js` (added `request()`
  wrapper + new API functions), `frontend/src/App.jsx` (renders
  `Dashboard` instead of the placeholder paragraph).
- **Tests added** (13 total in `Dashboard.test.jsx`, covering all 6
  required cases plus extras): dashboard renders all four sections;
  all three scenario buttons render; loading state shown and all
  buttons disabled while a scenario runs; successful `ALLOW` result
  displayed with its reason and "no action needed" corrective action;
  `BLOCKED` violation result displayed with corrective action and the
  "Violation Detected"/"Asset Quarantined" timeline steps; a non-2xx
  scenario response shows a readable error (no `Error:`-prefixed raw
  text) and the button is rerunnable afterward; a total network failure
  shows the "could not reach the backend" message.
- **Test result**: frontend `13 passed` (`npm test`, Vitest); backend
  regression suite `112 passed, 2 warnings in ~22s` (`pytest`) — zero
  regressions in either suite.
- **Manual end-to-end verification**: `npm run build` (production
  build succeeds, no compile errors) — then a **real** backend
  (`uvicorn`, isolated SQLite file) and a **real** Vite dev server were
  started together, with the dev server's served module inspected
  directly (`curl .../src/lib/api.js`) to confirm it resolves
  `VITE_API_BASE_URL` to the intended backend at runtime. Every request
  the dashboard's code issues — `GET /grants`, `GET /assets`,
  `GET /assets?state=QUARANTINED`, all three
  `POST /demo/scenarios/*`, `GET /assets/{id}` — was then replayed by
  hand against that live backend in the same order the component
  issues them, confirming every field the components read
  (`status`, `parent_asset_id`, `state`, `name`, `origin_purpose`,
  `origin_grant_expires_at`, `decision`, `reason`, `remediation`,
  `timeline[].{event_type,entity_type,entity_id,details,created_at}`)
  is actually present with the expected shape. This is not the same as
  clicking through the app in a real browser (no browser-automation
  tool is available in this environment), but it verifies the full
  request/response contract the browser-executed code depends on, end
  to end, against a real (not mocked) backend.
- **Known limitations**: no literal browser/click-through verification
  was performed (see above) — only the underlying API contract was
  confirmed end-to-end. `getAsset()` is an extra round-trip per
  scenario run purely to obtain a friendly asset name/purpose/expiry;
  acceptable at this scale. "Violations Detected" resets on page
  reload (session-local, by design — see above). No routing — the
  dashboard is the only screen, matching the "one main dashboard"
  instruction.

### Interactive Data Lineage Visualization

**Goal**: show a judge, visually, "where did this data come from and
which copies are affected?" — a graph rendering of the existing
`GET /assets/{id}/lineage` endpoint (no backend changes; purely a new
frontend consumer). No graph database was introduced — the backend
already answers lineage with one SQL query via the denormalized
`root_asset_id` column (see Database Model); this feature only draws
that response.

- **`LineageGraph.jsx`**, built on `@xyflow/react` (peer deps
  `react >=17`, compatible with this project's React 19 — confirmed via
  `npm view` before installing). Renders one custom node per asset
  (name + status badge) connected by edges built directly from the
  lineage response's `edges` array (`{parent_id, child_id}` →
  `{source, target}`).
- **Layout is a small hand-rolled layered algorithm** (`computeLayout`:
  BFS depth from the root via the edge list → row; sibling order →
  column), not a graph-layout library (dagre/elkjs) — this project's
  lineage trees are shallow and mostly linear (the exact
  `Patient Lab Record → Retrieved Copy → Analysis Dataset → Derived
  Report` chain the task named), so a dependency for arbitrary-graph
  auto-layout would be solving a harder problem than the one this app
  actually has.
- **Per-node status is computed from two authoritative backend
  signals, never a client-side clock comparison**: `state ===
  'QUARANTINED'` on the asset itself is definitive; otherwise, the
  node's shared `origin_grant_id` is looked up via `GET /grants/{id}`
  and that grant's live `status` (already computed server-side by
  `evaluate_grant_status` against the *simulated* clock) maps
  `EXPIRED` → "PURPOSE EXPIRED" and `REVOKED` → "GRANT REVOKED";
  anything else (including a root asset with no origin grant) is
  "ACTIVE". A wall-clock comparison in the browser was deliberately
  rejected: this project's expiry is evaluated against `clock.now()`
  (real time + a simulated offset advanced by demo scenarios), which a
  browser's `Date.now()` cannot see, and a naive comparison would show
  a demo-expired grant as still active for real minutes after the
  dashboard already reported it expired.
- **One grant lookup per lineage tree, not per node** — `copy_service`
  always inherits `origin_grant_id` from the parent asset's existing
  grant (never issues a new one), so an entire retrieved/derived
  subtree shares exactly one grant id; the dashboard collects the
  *unique* `origin_grant_id` values across all lineage nodes (typically
  zero or one) before fetching, not one request per node.
- **Clicking a node** shows asset name/id, parent, root, associated
  grant, original purpose, expiry, and current status in a side panel —
  exactly the fields the task specified, sourced entirely from the
  already-fetched `DataAssetOut` node plus the same status computation
  used for the node's badge.
- **Not editable**: `nodesDraggable={false}`, `nodesConnectable={false}`,
  no add/remove/reconnect handlers wired — a pure, read-only view, per
  the task's explicit instruction.
- **Integrated into the existing dashboard, not a separate screen** —
  after any scenario run, the dashboard resolves the evaluated asset's
  root (already has the asset detail from the Result panel's own
  fetch) and loads that root's lineage into a new "Data Lineage"
  section below the Journey Timeline. A lineage-fetch failure sets its
  own independent error state and never blanks out the
  already-successful Result/Timeline sections above it.

**Testing @xyflow/react in jsdom required three environment stubs**
(`src/setupTests.js`), discovered by tracing actual library source
rather than guessing:
1. `ResizeObserver` — jsdom has none; xyflow uses one to detect when to
   measure a node. The stub must fire **asynchronously** (`setTimeout`,
   not synchronously inside `observe()`) — firing synchronously races
   ahead of the root wrapper's own mount effect (which registers the
   DOM node xyflow later measures against), so the update is silently
   dropped.
2. `Element.prototype.getBoundingClientRect` / `offsetWidth` /
   `offsetHeight` — jsdom reports 0 for all of these (no real layout
   engine); xyflow refuses to draw edges between unmeasured (0-size)
   nodes, so both are stubbed to a fixed plausible size.
3. `DOMMatrixReadOnly` — jsdom doesn't implement it at all; xyflow
   parses the viewport's CSS transform through it to read the current
   zoom level on every measurement pass. A minimal `{m22: 1}`
   (identity-scale) stub is enough since tests never pan/zoom.

Without all three, nodes render but stay permanently
`visibility: hidden` and zero edges ever appear — a state that looks
like "the component doesn't work" but is actually "jsdom cannot answer
the layout questions this library asks," a real environment gap rather
than a defect in `LineageGraph.jsx` itself.

- **Files added**: `frontend/src/components/{LineageGraph,
  LineageGraph.test.jsx}`.
- **Files modified**: `frontend/src/components/Dashboard.jsx` (added
  the lineage-loading call after each scenario run and the new "Data
  Lineage" section), `frontend/src/lib/api.js` (added `getGrant`,
  `getLineage`), `frontend/src/setupTests.js` (the three stubs above),
  `frontend/package.json` (added `@xyflow/react`).
- **Tests added**: 7 in `LineageGraph.test.jsx` (renders every node
  from the API; renders an edge for every parent/child relationship;
  makes `QUARANTINED` visible on the affected node; shows
  `PURPOSE EXPIRED` when the shared origin grant has expired; handles
  empty/missing lineage without crashing; handles an API error
  gracefully; clicking a node reveals its asset/parent/root/grant/
  purpose/expiry/status details) plus 1 new integration test in
  `Dashboard.test.jsx` confirming the lineage graph actually renders
  real nodes after a scenario run through the full component tree
  (not just the isolated `LineageGraph` component).
- **Test result**: frontend `21 passed` (13 prior Dashboard-suite tests
  + 7 new LineageGraph tests + 1 new Dashboard integration test);
  backend regression `112 passed, 2 warnings in ~22s` — zero
  regressions, no backend changes at all for this feature.
- **Manual verification**: `npm run build` succeeded (`@xyflow/react`
  bundles cleanly, 382.81 kB / 121.52 kB gzipped). Against a live
  backend, ran the expired-purpose scenario, then replayed the exact
  calls the dashboard makes (`GET /assets/{id}` to find the root,
  `GET /assets/{root}/lineage`, `GET /grants/{id}` for the shared
  origin grant) and confirmed the live response matches what
  `computeNodeStatus` needs and produces the intended labels: root
  asset → `ACTIVE` (no origin grant), retrieved copy → `PURPOSE
  EXPIRED` (shared grant's live `status: "EXPIRED"`), derived copy →
  `QUARANTINED` (its own `state`).
- **Known limitations**: no literal browser click-through (same gap as
  the dashboard feature — no browser-automation tool available here).
  The hand-rolled layout doesn't attempt to avoid edge/node overlap for
  a wide branching tree (only depth/sibling-order based) — acceptable
  for this project's shallow, mostly-linear lineage trees; a genuinely
  bushy tree would need a real layout library. Grant-status lookups are
  not cached across multiple lineage loads in the same session (a
  fresh `GET /grants/{id}` call every time a scenario re-triggers a
  lineage load) — negligible cost at this scale.

### Stronger Remediation Workflow

**Goal**: make remediation of a detected violation explicit and
persistent, not just implicit in the asset's `state` and scattered
across `AuditLog` details — without changing any existing ALLOW/DENY
decision logic. Every check `evaluate_use` already performs (actor,
purpose, operation, grant status) is untouched; this feature only adds
what happens *after* a purpose-lifecycle violation is detected.

- **New `Remediation` entity** (`app/models/remediation.py`) — see
  Database Model above for its columns and the design decision for why
  it's a real table and not folded into `AuditLog`. Created via a new,
  minimal `app/services/remediation_service.py`
  (`create_remediation`, `get_latest_remediation_for_asset`) — no new
  API endpoint; nothing external asked for one, and everything the task
  requires "shown" is already surfaced through `POST /uses`'s existing
  response (see below).
- **`policy_service._deny` now does one more thing** for the three
  purpose-lifecycle reasons (`PURPOSE_EXPIRED`, `PURPOSE_MISMATCH`,
  `GRANT_REVOKED`) — after the existing `PURPOSE_VIOLATION` +
  quarantine + `ASSET_QUARANTINED` sequence, it creates a `Remediation`
  row and writes a new `COMPLIANCE_REVIEW_REQUIRED` audit event, all in
  the same atomic transaction (one `flush`-then-audit-then-`commit`,
  same pattern as everywhere else). Ordinary access-control denials
  (`ACTOR_MISMATCH`, `OPERATION_NOT_PERMITTED`, `NO_ORIGIN_GRANT`) are
  completely unaffected — no remediation, no new event, matching the
  existing quarantine-scope decision and the task's explicit "without
  changing the basic policy semantics."
- **`PolicyDecision`/`PolicyDecisionOut` gained two fields**:
  `remediation` (the corrective-action text) and `remediation_status`
  (currently only ever `"COMPLIANCE_REVIEW_REQUIRED"` or `null`). Both
  are `null` for `ALLOW` and for ordinary access-control denials. This
  directly satisfies "the system should show: violation → reason →
  affected copy → corrective action → current remediation status" at
  the one place a violation is actually reported: the `/uses` response
  itself (`reason` + `asset_id` were already there).
- **Corrective-action text moved into the policy engine, not the demo
  scenarios** — `CORRECTIVE_ACTIONS` (a reason-code → message dict) now
  lives in `policy_service.py`, so *every* real `/uses` call gets a
  remediation message, not just the three canned demo scenarios. This
  also deleted a duplication: `demo_service.py` used to hardcode its
  own per-scenario remediation strings; it now just forwards
  `decision.remediation`/`decision.remediation_status`, so there is one
  source of truth for what a judge sees, matching the project's
  repeated "one authoritative source per fact" pattern (`resource_id`
  removal, purpose-seal propagation, etc.).
- **Repeated identical requests behave safely, for free** — a second
  `/uses` attempt against an already-quarantined asset was already
  routed to a distinct `ASSET_QUARANTINED` reason code that isn't one
  of the three lifecycle reasons (existing behavior, unchanged), so it
  never re-creates a `Remediation` row or re-fires
  `COMPLIANCE_REVIEW_REQUIRED`. That repeat response still needs to
  *show* the existing remediation, though, so `evaluate_use` now looks
  up `get_latest_remediation_for_asset` before calling `_deny` and
  `_deny` echoes that row's `corrective_action`/`status` back — no new
  violation, no new remediation, but the caller still sees the open
  compliance-review state.
- **No delete path was added anywhere** — `Remediation` rows and every
  `AuditLog` row remain exactly as permanent as before; the task's "do
  not automatically delete evidence required for audit" was already
  the project's default (no delete endpoint exists for any entity) and
  needed no new enforcement.
- **Dashboard**: `ScenarioResult.jsx` gained a "Remediation Status"
  field (between Asset and Corrective Action), sourced from the same
  `response.remediation`/`response.remediation_status` fields the
  dashboard already read — no new frontend API calls.

**Worked example (from a live server)**:
```
POST /demo/scenarios/expired
-> {"decision": "DENY", "reason_code": "PURPOSE_EXPIRED",
    "remediation": "Issue a new purpose grant against the original asset
                     if continued access is legitimate; this asset
                     remains quarantined under its expired grant.",
    "remediation_status": "COMPLIANCE_REVIEW_REQUIRED",
    "timeline": [SOURCE_ASSET_CREATED, GRANT_CREATED, DATA_RETRIEVED,
                 DERIVED_ASSET_CREATED, DATA_USE_ATTEMPTED,
                 PURPOSE_VIOLATION, ASSET_QUARANTINED,
                 COMPLIANCE_REVIEW_REQUIRED]}
```

- **Files added**: `backend/app/models/remediation.py`,
  `backend/app/services/remediation_service.py`,
  `backend/tests/test_remediation.py`.
- **Files modified**: `backend/app/models/__init__.py`,
  `backend/app/services/policy_service.py` (remediation creation +
  lookup, `CORRECTIVE_ACTIONS`), `backend/app/schemas/policy.py`
  (+`remediation`, `+remediation_status`), `backend/app/api/uses.py`,
  `backend/app/schemas/demo.py` (+`remediation_status`),
  `backend/app/services/demo_service.py` (now forwards the policy
  engine's remediation fields instead of hardcoding its own),
  `backend/app/api/demo.py`, `backend/tests/test_demo_scenarios.py`
  (two timeline-sequence assertions extended with the new
  `COMPLIANCE_REVIEW_REQUIRED` event), `frontend/src/components/
  {ScenarioResult,Dashboard}.jsx`, `frontend/src/components/
  Dashboard.test.jsx`.
- **Tests added** (8 in `test_remediation.py`): violation creates a
  `Remediation` row with the right fields; violating asset is
  quarantined; a repeated identical request is safe (exactly one
  `Remediation` row and one `COMPLIANCE_REVIEW_REQUIRED` event ever,
  the repeat response still surfaces the existing remediation);
  remediation survives a simulated restart (fresh session, same
  engine); a legitimate (`ALLOW`) use creates no remediation and leaves
  the asset `ACTIVE`; an ordinary access-control denial
  (`ACTOR_MISMATCH`) creates no remediation either (basic policy
  semantics unchanged); the `COMPLIANCE_REVIEW_REQUIRED` audit event
  carries the remediation's id/reason_code/status; evidence (the
  `Remediation` row and the original `PURPOSE_VIOLATION` event) is
  unchanged by a subsequent blocked retry.
- **Test result**: backend `120 passed` (112 prior + 8 new; two
  existing demo-scenario tests were updated, not weakened, to expect
  the new audit event), zero regressions. Frontend `21 passed`. Also
  manually verified live via `uvicorn`: `POST /demo/scenarios/expired`
  returns populated `remediation`/`remediation_status` fields and the
  extended timeline shown above.
- **Known limitations**: `RemediationStatus` has exactly one value
  today (`COMPLIANCE_REVIEW_REQUIRED`) — no resolve/dismiss workflow
  exists, so a remediation, once created, stays in that state forever.
  This matches the task's scope (make remediation *visible and
  persistent*, not build a full review lifecycle) and the project's
  existing "no un-quarantine" limitation; adding a resolution status
  and endpoint is natural future work if a reviewer-facing flow is ever
  requested. No `GET` endpoint lists remediations directly — they're
  only reachable via the `/uses` response that created them or by
  querying `AuditLog`/the `Remediation` table directly (fine for this
  project's current query needs).

### Actor Roles and Minimal Authentication

**Goal**: minimal login/roles appropriate for a hackathon MVP, without
building SSO/OAuth and — critically — without letting "this login is
valid" be confused with "this data use is authorized." Before writing
any code, the repository was searched for existing auth (`grep -r
auth`) and found none, and both test suites (120 backend, 21 frontend)
were confirmed green first, per the task's explicit "only implement if
the core demo is stable."

**The authentication/purpose-authorization boundary, explained**: this
project has always had two independent questions in play, and this
feature makes the second one explicit for the first time:

1. *Authentication*: "is this really who they claim to be, and are
   they the kind of actor (role) allowed to attempt this operation at
   all?" — new in this feature (`core/security.py`, `api/deps.py`,
   `POST /auth/login`).
2. *Purpose authorization*: "is this specific attempted use of this
   specific asset within the purpose, expiry, and operation a grant
   actually authorized?" — the policy engine, unchanged, and unaware
   this feature exists.

A user can authenticate perfectly (correct password, valid token,
permitted role) and still have every `/uses` call `DENY`, because
`evaluate_use` never asks "was this caller properly logged in" — it
only ever asks "does a grant say this purpose/operation/actor/time is
valid." Conversely, a role check passing (e.g., a `COMPLIANCE_OFFICER`
token accepted for `POST /grants/{id}/revoke`) says nothing about
whether any particular grant *should* be revoked — that's still a
business decision the caller makes, not something the auth layer
evaluates. `api/deps.py`'s module docstring and `models/user.py`'s
`UserRole` docstring both restate this; `policy_service.py` was not
touched at all by this feature (verified: `git diff` touches no policy
file), which is the clearest proof the two systems stayed independent.

- **Roles**: `RESEARCHER`, `CLINICIAN`, `ANALYST`, `COMPLIANCE_OFFICER`,
  `ADMIN` (`UserRole` enum) — all five named in the task, none omitted,
  none given special-cased behavior beyond the one role-gated operation
  below (no other requirement implied more).
- **No new dependencies**: this project had zero crypto libraries
  before this feature (`requirements.txt` still only lists
  fastapi/uvicorn/sqlalchemy/pydantic/pydantic-settings/httpx/pytest).
  Rather than adding `passlib`/`pyjwt`/`bcrypt`, `core/security.py`
  hand-rolls both pieces from the standard library: password hashing
  via `hashlib.pbkdf2_hmac` (a NIST-recommended KDF) with a random
  per-user salt, and a genuine HS256 JWT (header.payload.signature,
  base64url, HMAC-SHA256, `hmac.compare_digest` for constant-time
  verification) — not a JWT-*like* format, an actual correct minimal
  implementation of the open standard. This was a judgment call, not a
  requirement: "use a simple secure approach appropriate to the
  existing architecture" was read as license to keep the zero-new-
  dependency pattern this project has followed throughout, given a
  correct implementation was straightforward with the standard library
  alone. PBKDF2 iterations are set to 100,000 (a legitimate, commonly-
  cited baseline, below OWASP's current 600,000+ recommendation) so a
  test suite registering/logging in many users stays fast — documented
  in `core/security.py` as a tradeoff a real deployment should revisit.
- **Token timestamps use the project's simulated `clock`**, not real
  wall-clock time — the same single "now" source as grants, retrievals,
  and everything else, so a demo scenario advancing simulated time
  can't produce surprising token-expiry behavior, and token expiry is
  just as deterministic/testable as the rest of the system.
- **`POST /auth/register`, `POST /auth/login`, `GET /auth/me`** — the
  whole auth surface. Registration is open to anyone for any role,
  including `ADMIN`/`COMPLIANCE_OFFICER` — a real deployment would gate
  who can self-assign the higher-privilege roles; left open here for
  hackathon-scope simplicity (documented as a known limitation below).
- **Exactly one role-restricted operation**: `POST /grants/{id}/revoke`
  now requires authentication outright (`get_current_user`, 401
  without a valid token) and requires the `COMPLIANCE_OFFICER` or
  `ADMIN` role (`require_role(...)`, 403 otherwise) — revoking access
  is a compliance/governance action, a natural, minimal choice of
  "which operation" to gate rather than gating everything. No other
  endpoint's authorization requirements changed.
- **Identity cannot be spoofed through the request body, for the three
  endpoints where the body's `actor` field represents "who is
  performing this action right now"**: `POST /retrievals`,
  `POST /copies`, `POST /uses`. Each now takes an *optional*
  authenticated user (`get_current_user_optional` — never raises); when
  a valid Bearer token is present, the request's `actor` is
  unconditionally replaced with the token's username before the
  payload reaches any service function
  (`payload.model_copy(update={"actor": ...})` at the API boundary —
  zero changes to `retrieval_service.py`/`copy_service.py`/
  `policy_service.py`). A request with **no** Authorization header
  behaves exactly as before this feature — all 120 prior backend tests
  pass unmodified. `POST /grants`'s `subject` field was deliberately
  **not** touched: it represents who a grant is issued *to*, which
  legitimately may differ from who's creating it (e.g., an admin
  issuing a grant to a researcher) — overriding it would break that
  case and doesn't fit "who is performing this action right now" the
  way `actor` does on the other three endpoints.

**Worked example (from a live server)**:
```
POST /auth/register {"username": "researcher_01", "password": "password123", "role": "RESEARCHER"}
-> 201 {"id": 1, "username": "researcher_01", "role": "RESEARCHER", ...}

POST /auth/login {"username": "researcher_01", "password": "wrong"}
-> 401 {"error_code": "invalid_credentials", ...}

POST /auth/login {"username": "researcher_01", "password": "password123"}
-> 200 {"access_token": "eyJhbGci...", "token_type": "bearer", "role": "RESEARCHER", ...}

POST /grants/3/revoke                                    (no token)
-> 401 {"error_code": "not_authenticated", ...}

POST /grants/3/revoke   Authorization: Bearer <researcher_01's token>
-> 403 {"error_code": "role_not_permitted",
        "message": "Role 'RESEARCHER' is not permitted to perform this operation."}

POST /grants/3/revoke   Authorization: Bearer <compliance_01's token>
-> 200 {"status": "REVOKED", ...}

POST /retrievals   Authorization: Bearer <researcher_01's token>
                    {"grant_id": 3, "actor": "mallory", "asset_id": 5, "operation": "VIEW"}
-> 201 {...}   -- succeeded because the grant's subject is researcher_01,
                  which is who the token says this really is; "mallory"
                  in the body was never used (confirmed by inspecting the
                  DATA_RETRIEVED audit event's `details.actor`, which
                  reads "researcher_01").
```

- **Files added**: `backend/app/models/user.py`,
  `backend/app/core/security.py`, `backend/app/schemas/auth.py`,
  `backend/app/services/auth_service.py`, `backend/app/api/deps.py`,
  `backend/app/api/auth.py`, `backend/tests/test_auth.py`.
- **Files modified**: `backend/app/models/__init__.py`,
  `backend/app/schemas/__init__.py`, `backend/app/api/__init__.py`,
  `backend/app/core/config.py` (+`secret_key`), `backend/app/core/
  errors.py` (+`UnauthorizedError` 401, +`ConflictError` 409),
  `backend/app/api/grants.py` (role-gated revoke),
  `backend/app/api/{retrievals,copies,uses}.py` (optional
  authenticated-identity override), `backend/tests/
  {test_grant_revocation,test_full_lifecycle_via_http}.py` (revoke
  calls now authenticate as a `COMPLIANCE_OFFICER` first — a
  deliberate, documented behavior change, not a weakened test).
- **Tests added** (15 in `test_auth.py`, covering all 6 required cases
  plus extras): unauthenticated protected request (`revoke` without a
  token → 401); valid login returns a usable token; wrong credentials
  → 401 (plus a nonexistent-username variant); role-restricted
  operation (`RESEARCHER`/`ANALYST`/`CLINICIAN` → 403 revoking,
  `COMPLIANCE_OFFICER`/`ADMIN` → 200); an authenticated, correctly-
  identified actor is still `DENY`/`PURPOSE_MISMATCH`'d by the policy
  engine on a wrong-purpose use (the authentication/purpose-
  authorization boundary, directly tested); identity cannot be spoofed
  through the request body (valid token for researcher_01, body claims
  `actor: "mallory"` → the retrieval succeeds as researcher_01, and the
  audit trail itself records `"actor": "researcher_01"`, never
  "mallory"); duplicate registration rejected (409); invalid/malformed
  token rejected (401); unauthenticated request behavior is provably
  unchanged from before this feature.
- **Test result**: backend `135 passed` (120 prior + 15 new; two
  revoke-related tests in other files updated to authenticate first,
  not weakened), zero regressions. Frontend `21 passed`, completely
  unaffected (the dashboard never calls `POST /grants/{id}/revoke` or
  any of the three actor-bearing write endpoints — it only reads
  `GET /grants`/`GET /assets`/lineage and runs demo scenarios, none of
  which changed). Also manually verified live via `uvicorn`: full
  register → wrong-password-rejected → login → unauthenticated-401 →
  wrong-role-403 → right-role-200 → spoofing-attempt-still-correctly-
  attributed sequence, exactly as shown above.
- **Known limitations**: registration is open to anyone for any role
  (no gate on self-assigning `ADMIN`/`COMPLIANCE_OFFICER`) — acceptable
  for a hackathon demo, would need restricting in a real deployment.
  `Grant.subject` is still an unverified free-text string with no
  role/identity restriction on `POST /grants` itself — only revocation
  and the three actor-bearing endpoints are auth-aware; a real
  deployment would likely also restrict who may issue a grant. No
  token refresh/logout/revocation (tokens are valid for their full
  60-minute lifetime once issued — fine for a demo session). No
  password reset flow. `PURPOSESEAL_SECRET_KEY` has an obviously-
  insecure default that must be overridden outside local development
  (documented inline in `core/config.py`).

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Liveness check — `{status, app_name}` |
| POST | /auth/register | Create a login identity (`username`, `password`, `role`); open to any of the five roles |
| POST | /auth/login | Exchange credentials for an access token; `401 invalid_credentials` on failure |
| GET | /auth/me | Return the authenticated caller's identity; `401` without a valid token |
| POST | /assets | Create an original/root `DataAsset` (`name`, `asset_type` only — provenance fields are server-controlled) |
| GET | /assets | List assets; optional `state`, `asset_type`, `root_only` filters |
| GET | /assets/{id} | Get one asset by id, with its purpose seal resolved |
| GET | /assets/{id}/lineage | Get the full lineage tree (`{root_asset_id, nodes, edges}`) containing this asset |
| POST | /grants | Create a purpose-bound access grant for an existing `asset_id` (requires `allowed_operations`, at least one) |
| GET | /grants | List all grants (with live-evaluated `status`) |
| GET | /grants/{id} | Get one grant by id (with live-evaluated `status`) |
| GET | /grants/{id}/status | Evaluate a grant's current status with an explanation (`reason_code`, `human_readable_reason`) |
| POST | /grants/{id}/revoke | **Requires authentication + `COMPLIANCE_OFFICER`/`ADMIN` role.** Explicitly revoke a grant (persists `status: REVOKED` + `GRANT_REVOKED` audit event); idempotent — repeat calls return the already-revoked grant without a duplicate event |
| POST | /retrievals | Retrieve data under a grant; creates a purpose-sealed `DataAsset` copy or denies with a clear `error_code`. If a valid Bearer token is present, `actor` is taken from it, not the body |
| POST | /copies | Create a copy or derived asset from an existing, already-retrieved asset; re-checks actor/status/operation against its origin grant. Same Bearer-token `actor` override as `/retrievals` |
| POST | /uses | Evaluate an attempted use of an already-retrieved/derived asset against its origin purpose; always `200`, body carries `{decision, reason_code, reason, asset_id, grant_id, evaluated_at, remediation, remediation_status}` — the last two are populated only for a purpose-lifecycle violation. Same Bearer-token `actor` override as `/retrievals` — note this never changes the decision logic itself, only whose identity it's evaluated against |
| GET | /dev/clock | **Simulation only** — current simulated time |
| POST | /dev/clock/advance | **Simulation only** — advance simulated time by `{minutes, seconds, hours}`; disable via `PURPOSESEAL_ENABLE_DEV_ENDPOINTS=false` |
| POST | /demo/scenarios/legitimate | **Simulation only** — runs the full legitimate-use journey in one call; returns the decision (`ALLOW`) and its complete audit timeline |
| POST | /demo/scenarios/expired | **Simulation only** — runs the full purpose-expiry journey in one call (advances simulated time itself); returns `DENY`/`PURPOSE_EXPIRED`, the quarantined asset id, and the full timeline |
| POST | /demo/scenarios/purpose-mismatch | **Simulation only** — runs the full purpose-mismatch journey in one call; returns `DENY`/`PURPOSE_MISMATCH`, remediation guidance, and the full timeline |

The entire lifecycle — original asset → grant → retrieval → copy/derived
asset → use evaluation → revocation — is now reachable entirely through
these APIs; nothing requires direct ORM/SQLite seeding anymore.

## Demo Journey

### Judge demo via the dashboard (recommended — no terminal needed)

This is the intended way to demo PurposeSeal to a judge: two servers,
one browser tab, three button clicks.

1. **Start the backend** (from `backend/`, with the virtualenv active):
   ```
   uvicorn app.main:app --reload
   ```
   Confirm it's up at `http://localhost:8000/health`.
2. **Start the frontend** (from `frontend/`, in a second terminal):
   ```
   npm install   # first time only
   npm run dev
   ```
   Open the printed URL — `http://localhost:5173` by default. The
   backend's default `cors_origins` already allows this origin; no
   configuration is needed for the default ports.
3. **Read the Summary row** — four metrics (Active Grants, Tracked
   Copies, Violations Detected, Quarantined Assets), all `0` on a fresh
   database.
4. **Click "Run Valid Scenario."** The button briefly shows "Running…"
   (all three buttons disable together). The Result panel then shows
   **ALLOW**, the reason, original/requested purpose (identical here),
   the grant's expiry, the asset, and "no action needed." The Journey
   Timeline below shows six steps ending in "Use Allowed." Active
   Grants and Tracked Copies in the Summary row update to reflect the
   new grant/copies. Below the timeline, **Data Lineage** now shows a
   small graph: the original asset → its retrieved copy, both labeled
   `ACTIVE`.
5. **Click "Run Expired-Purpose Scenario."** Result flips to
   **BLOCKED**, reason names the expired grant, Remediation Status
   shows `COMPLIANCE_REVIEW_REQUIRED`, and Corrective Action suggests
   issuing a new grant. The timeline ends in "Purpose Expired —
   Violation Detected" → "Asset Quarantined" → a final
   `COMPLIANCE_REVIEW_REQUIRED` step. Quarantined Assets in the Summary
   row goes from `0` to `1`; Violations Detected goes from `0` to `1`.
   Data Lineage now shows the full chain from this scenario — root
   asset `ACTIVE`, its retrieved copy `PURPOSE EXPIRED` (the shared
   grant expired), the derived copy `QUARANTINED`. **Click the
   quarantined node** — the side panel shows its asset id, parent,
   root, associated grant, original purpose, expiry, and status in one
   place.
6. **Click "Run Purpose-Mismatch Scenario."** Result again shows
   **BLOCKED**, this time naming `clinical_trial_screening` as the
   original purpose and `marketing_analytics` as the requested one.
   Timeline ends in "Purpose Mismatch — Violation Detected" → "Asset
   Quarantined." Quarantined Assets becomes `2`, Violations Detected
   becomes `2`. Data Lineage updates to this scenario's own chain,
   ending in a `QUARANTINED` node — visually answering "which copy was
   affected, and the original source data was never touched."
7. **Re-run any scenario as many times as you like** — every click
   creates a fresh asset/grant chain (no collisions, no manual
   cleanup), so the demo can be repeated live without restarting
   anything.
8. **To show an API failure recovers gracefully**: stop the backend
   process, click any scenario button — the Result panel shows "Could
   not reach the PurposeSeal backend. Is it running?" instead of a
   blank screen or a stack trace. Restart the backend and click again;
   it works immediately, no page reload needed.

### One-call deterministic scenarios (recommended for scripting/CI, not live judging)

The fastest way to show the whole product — no manual grant/retrieval
setup required. Each endpoint drives a complete, self-contained journey
server-side using canned actors/purposes and the simulated clock, then
returns the final decision plus its full audit timeline in one response.
No randomness, no network calls, no waiting. Safe to call repeatedly —
every call creates a brand-new asset/grant/copy chain (fresh
autoincrement ids), so runs never collide with each other:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/demo/scenarios/legitimate" -Method Post
Invoke-RestMethod -Uri "http://127.0.0.1:8000/demo/scenarios/expired" -Method Post
Invoke-RestMethod -Uri "http://127.0.0.1:8000/demo/scenarios/purpose-mismatch" -Method Post
```

- **`POST /demo/scenarios/legitimate`** — creates an asset, grant,
  retrieval, and a copy, then uses the copy for its original purpose
  before expiry. Returns `{"decision": "ALLOW", "reason_code":
  "WITHIN_PURPOSE_AND_VALIDITY", ...}` with a 6-event timeline
  (`SOURCE_ASSET_CREATED` → `GRANT_CREATED` → `DATA_RETRIEVED` →
  `COPY_CREATED` → `DATA_USE_ATTEMPTED` → `USE_ALLOWED`).
- **`POST /demo/scenarios/expired`** — creates a 10-minute grant,
  retrieves, derives a copy, advances the simulated clock 11 minutes
  (`clock.advance()`, no real waiting), then attempts reuse. Returns
  `{"decision": "DENY", "reason_code": "PURPOSE_EXPIRED", ...}` plus
  remediation guidance; the derived copy is quarantined
  (`PURPOSE_VIOLATION` → `ASSET_QUARANTINED` at the end of its
  7-event timeline).
- **`POST /demo/scenarios/purpose-mismatch`** — creates an asset, grant,
  and retrieval for `clinical_trial_screening`, then attempts use for
  `marketing_analytics`. Returns `{"decision": "DENY", "reason_code":
  "PURPOSE_MISMATCH", ...}` plus remediation guidance; the retrieved
  copy is quarantined (6-event timeline).

Each response's `timeline` array is a chronological (insertion-order)
list of every audit event touching that scenario's asset(s) and grant —
`{event_type, entity_type, entity_id, details, created_at}` per event —
ready to render as a judge-facing activity feed without any additional
querying.

### Manual step-by-step journey

1. Open the frontend shell — product name, layout, and a live "Backend
   connected" indicator confirm the stack is wired end-to-end.
2. `POST /assets {name: "Patient Lab Result #104", asset_type:
   "lab_result"}` — an original `DataAsset` is created, `ACTIVE`, with no
   parent/root/origin grant, and `SOURCE_ASSET_CREATED` is audited.
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
8. `POST /uses {asset_id: <retrieved copy>, actor, purpose:
   clinical_trial_screening, operation: ANALYZE}` while the grant is
   still active — `200 {"decision": "ALLOW", ...}`.
9. `POST /uses` with `purpose: marketing_analytics` on a sibling copy —
   `200 {"decision": "DENY", "reason_code": "PURPOSE_MISMATCH", ...}`,
   and that sibling's `state` becomes `QUARANTINED`.
10. `POST /dev/clock/advance {"minutes": 31}` — simulated time jumps past
    the grant's 30-minute expiry in an instant, no real waiting.
    `GET /grants/{id}/status` now shows `EXPIRED`.
11. `POST /uses` again on the original retrieved copy — `200 {"decision":
    "DENY", "reason_code": "PURPOSE_EXPIRED", ...}`, that copy becomes
    `QUARANTINED`, and `GET /assets/{root_id}` confirms the **original
    source asset stays `ACTIVE`** — only the misused copy was punished.
12. A further `POST /uses` on the now-quarantined copy is denied again
    (`ASSET_QUARANTINED`) without re-triggering quarantine, while a
    completely different sibling asset remains normally usable.
13. `POST /grants/{id}/revoke` on a still-active grant tied to a
    different, untouched copy — the grant's stored `status` becomes
    `REVOKED` and `GRANT_REVOKED` is audited. `POST /uses` on that copy
    now returns `200 {"decision": "DENY", "reason_code":
    "GRANT_REVOKED", ...}`, and that copy becomes `QUARANTINED` — proving
    the policy engine reacts to explicit revocation exactly like it does
    to natural expiry, with no duplicated logic in the revoke endpoint
    itself.

(Later steps — frontend screens for everything above, e.g. an actual
lineage graph visualization — will be appended here as each feature
lands.)

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
- **Only purpose-lifecycle DENY reasons quarantine an asset**
  (`PURPOSE_EXPIRED`, `PURPOSE_MISMATCH`, `GRANT_REVOKED`); ordinary
  access-control denials (`ACTOR_MISMATCH`, `OPERATION_NOT_PERMITTED`)
  do not — the asset isn't at fault for a request that was never
  legitimate for reasons unrelated to its purpose. See the policy
  feature's design decision.
- **`POST /uses` always returns `200`; `DENY` is data, not an HTTP
  error** — unlike retrieval/copy (mutations that succeed or get
  rejected), a use evaluation's entire job is to produce a decision, so
  the decision is the response body in both directions.
- **Dev-only endpoints live under `/dev` and behind a settings flag**
  (`enable_dev_endpoints`, default `True`) — a real deployment sets
  `PURPOSESEAL_ENABLE_DEV_ENDPOINTS=false` to remove `clock.advance()`
  from the API surface entirely, not just rely on the path naming.
- **`DataAssetCreate` doesn't merely ignore `parent_asset_id` /
  `root_asset_id` / `origin_grant_id` / `state` — it doesn't accept them
  at all** (`extra="forbid"`), the same pattern already used by
  `CopyCreate`. A client attempting to POST a "retrieved" or "derived"
  asset directly through `/assets` gets a `422`, not a silently-ignored
  field; those provenance fields can only ever come from the
  retrieval/copy services, which is the one guarantee the whole
  purpose-seal model depends on.
- **`POST /assets` and `POST /grants/{id}/revoke` reuse the same
  flush-then-audit-then-commit pattern** established by the Reliability
  Correction — no new atomicity approach was invented for these
  operational endpoints.
- **Grant revocation is idempotent, not error-on-repeat** — revoking an
  already-revoked grant returns the grant as-is (`200`, `status:
  REVOKED`) without writing a second `GRANT_REVOKED` event. A grant
  either was or wasn't explicitly revoked; the moment it happened is a
  fact the audit trail should record exactly once, not once per repeat
  click/retry. A conflict response was the rejected alternative — it
  would force every caller to special-case "already revoked" as an
  error even though nothing actually went wrong.
- **Revocation only flips `Grant.status`; it never touches the policy
  engine** — `policy_service.evaluate_use` already read
  `evaluate_grant_status(grant)` and already treated `REVOKED` as a
  purpose-lifecycle violation (quarantine + `PURPOSE_VIOLATION`) before
  this feature existed. `grant_service.revoke_grant` only had to make
  `REVOKED` reachable through the API; duplicating that check inside the
  revoke endpoint would have created two places that could disagree.
- **Demo scenarios call existing services directly, not their own HTTP
  endpoints internally** — `demo_service.py` imports and calls
  `asset_service`/`grant_service`/etc. functions the same way
  `api/assets.py` or `api/grants.py` do, rather than the scenario
  endpoint making internal HTTP requests to `/assets`, `/grants`, etc.
  Same transactional session, same atomic behavior, no self-networking.
- **Timeline ordering is by `AuditLog.id`, not `created_at`** — the
  expired scenario deliberately advances the simulated clock, and
  several events on either side of that jump can otherwise share
  indistinguishable timestamps; primary-key insertion order is the only
  sequence guaranteed to match what actually happened.
- **Demo scenarios are registered under `enable_dev_endpoints`, the same
  flag as `/dev/clock*`** — they are equally "not real product
  functionality," just canned data generators for showing the product,
  so they get the same production off-switch.
- **The dashboard fetches its own "Requested Purpose"/"Asset" details
  rather than the scenario response carrying pre-formatted display
  text** — `DemoScenarioOut` stays a decision-shaped API contract
  (`decision`, `reason_code`, `reason`, ids, `timeline`), not a
  UI-shaped one; the frontend derives everything display-specific
  (asset name, purpose strings) from that same data via `GET
  /assets/{id}` and the timeline, keeping the backend response reusable
  by any future consumer, not just this one screen.
- **"Violations Detected" is intentionally a session-local counter, not
  a live backend query** — see the dashboard feature's design decision;
  no backend change was made to support it, by design (no
  audit-log-listing endpoint exists yet).
- **All three scenario buttons disable together while any one is
  running**, not just the clicked button — prevents a second scenario's
  writes from interleaving with the first's mid-flight timeline/metrics
  refresh, keeping the result panel and summary numbers consistent with
  exactly one completed run at a time.
- **Lineage node status is computed from `GET /grants/{id}`'s live
  `status`, never a browser-side clock comparison** — this project's
  expiry is evaluated against a *simulated* clock (`clock.now()`), and
  only the backend can see it; comparing `origin_grant_expires_at`
  against the browser's real `Date.now()` would show a demo-expired
  grant as still active for real minutes after the backend already
  quarantined its data. See the lineage feature's design decision.
- **No graph-layout library (dagre/elkjs) for the lineage graph** — a
  small hand-rolled BFS-depth layout is enough for this project's
  shallow, mostly-linear lineage trees; a real layout algorithm would
  solve a harder (arbitrary-graph) problem than the one this app
  actually has.
- **The lineage graph is a section of the existing dashboard, not a
  new screen/route** — consistent with the "one main dashboard"
  decision from the previous feature; it loads automatically after
  each scenario run rather than requiring a separate navigation step.
- **`Remediation` is a real table, `UsageDecision` still isn't** — see
  the updated "Usage/policy decision entity" design decision under
  Database Model: the two ideas look similar but solve different
  problems. A `UsageDecision` table would be "every decision ever,
  queryable" (still not needed); `Remediation` exists because it has a
  field — `status` — that a future workflow would genuinely mutate,
  unlike a decision or an audit event, both permanent records of one
  moment.
- **Corrective-action text lives in `policy_service.py`
  (`CORRECTIVE_ACTIONS`), not in `demo_service.py`** — every real
  `/uses` call now gets a remediation message, not just the three demo
  scenarios, and there is exactly one place that text can be edited.
  The demo scenarios' previous hardcoded, per-scenario remediation
  strings were deleted in favor of forwarding the policy engine's own
  `decision.remediation`.
- **A repeated request against an already-quarantined asset creates no
  second `Remediation` row or `COMPLIANCE_REVIEW_REQUIRED` event** —
  this falls out of the existing `ASSET_QUARANTINED` reason code
  already being outside `PURPOSE_LIFECYCLE_REASONS` (no new logic
  needed for safety), but the repeat response still looks up and
  echoes the existing remediation's `corrective_action`/`status` so the
  caller doesn't lose visibility into the open compliance review on a
  retry.
- **No resolve/dismiss endpoint for a `Remediation`** — `status` has
  exactly one value today (`COMPLIANCE_REVIEW_REQUIRED`). The task
  asked to make remediation visible and persistent, not to build a
  full review lifecycle; a `RESOLVED`/`DISMISSED` status and an
  endpoint to set it are natural, separately-scoped future work.
- **Authentication and purpose authorization are two independent
  systems that never import from each other** — `api/deps.py` (roles,
  tokens) and `services/policy_service.py` (purpose/expiry/operation)
  don't reference one another at all. This is the central design
  decision of "Actor Roles and Minimal Authentication"; see that
  feature entry for the full explanation and worked proof.
- **Hand-rolled HS256 JWT + PBKDF2 password hashing, standard library
  only** — no `pyjwt`/`passlib`/`bcrypt` added. This project had zero
  crypto dependencies before this feature and a correct minimal HS256
  implementation was straightforward with `hmac`/`hashlib`/`base64`
  alone; judged as staying truer to "simple, appropriate to the
  existing architecture" than introducing a new dependency category.
- **Token timestamps use the simulated `clock`, not wall-clock time**
  — consistent with every other time-sensitive concept in this project
  (grant expiry, demo scenarios); a token's expiry is exactly as
  deterministic and simulated-clock-aware as a grant's.
- **Only `POST /grants/{id}/revoke` is role-gated; only
  `POST /retrievals`/`POST /copies`/`POST /uses` get the Bearer-token
  `actor` override** — a deliberately narrow first cut rather than
  retrofitting authentication onto every endpoint. `GET` endpoints and
  the dev/demo endpoints are completely unaffected, so the existing
  dashboard needed zero changes.
- **`POST /grants`'s `subject` field is not overridden by an
  authenticated identity** — unlike `actor` on the other three
  endpoints, `subject` represents who a grant is issued *to*, which
  can legitimately differ from who's creating it; overriding it would
  break "issue a grant to someone else" as a use case.

## Known Issues / Deferred Work

- No way to un-quarantine an asset (no restore/appeal flow). ~~This
  project's scope is detection and corrective action, not
  remediation.~~ **Narrowed** by the Stronger Remediation Workflow
  feature: violations now get an explicit, persistent `Remediation`
  record with a `status` — what's still missing is only a
  resolve/dismiss workflow that would *change* that status; a
  `Remediation` row itself is exactly the persistent remediation state
  this line used to say didn't exist.
- No `UsageDecision` table — resolved as unnecessary; see the design
  decision under Database Model (Usage/policy decision entity). A
  *different* table, `Remediation`, was added instead — see the same
  design decision's update and the Stronger Remediation Workflow
  feature entry.
- No way to edit/rename an original asset or delete one outright — only
  creation and listing exist; not required by any scenario so far.
- No revocation of an individual copy/derivative independent of its
  origin grant. `GET /assets/{id}/lineage` returns the entire tree with
  no pagination — fine at hackathon scale.
- No `.env.example` committed yet (nothing currently requires one to run
  locally); add one if/when a required env var appears.
- No schema migration tool (Alembic etc.) — tables are created via
  `Base.metadata.create_all`, which only adds missing tables, never alters
  existing ones. Fine for a hackathon MVP (the local dev db can just be
  deleted and recreated), but worth naming as a real limitation.
- Demo scenarios are fixed, not parameterizable, and leave their
  generated rows in the database permanently (no cleanup/reset
  endpoint) — acceptable for a hackathon demo session.
- ~~Frontend has no routing and no feature screens yet~~ **Resolved** by
  the "Minimal Judge-Ready Dashboard" feature — a single dashboard
  screen now covers the full demo journey. Still no client-side
  routing (by design — "one main dashboard," not multiple screens); a
  global audit-trail view remains future work.
- ~~A dedicated lineage-graph visualization~~ **Resolved** by
  "Interactive Data Lineage Visualization" — the dashboard now renders
  the full asset tree with per-node status and a click-for-details
  panel.
- The lineage graph's hand-rolled layout doesn't avoid node/edge
  overlap for a wide branching tree (depth/sibling-order only) — fine
  for this project's shallow, mostly-linear trees; a genuinely bushy
  tree would need a real layout library.
- No role/identity gate on `POST /auth/register` (anyone can self-
  register as `ADMIN`/`COMPLIANCE_OFFICER`), no token
  refresh/logout/revocation, no password reset flow — all deliberate
  hackathon-MVP scope cuts for the Actor Roles and Minimal
  Authentication feature. `POST /grants`'s `subject` remains an
  unverified free-text string; only revocation and the three actor-
  bearing write endpoints are auth-aware today.
- The frontend dashboard has no login screen — every write it makes
  goes through unauthenticated paths (`POST /demo/scenarios/*` calling
  services directly, bypassing the HTTP auth layer entirely) or GET
  requests that were never auth-gated, so this was a deliberate choice
  to avoid touching the working demo, not an oversight. Wiring a login
  flow into the dashboard (so real Bearer-token demos of role
  restriction and identity-override are visible in the UI, not just
  via `curl`/tests) is natural future work.
- No literal browser/click-through verification tooling in this
  environment (no Playwright/Puppeteer installed) — the dashboard
  feature's manual verification confirmed the full API request/response
  contract end-to-end against a real backend and a real Vite dev
  server, but did not simulate an actual mouse click in a rendered
  page. Frontend component tests (mocked `fetch`) plus this contract
  verification are the current substitute.
- The dashboard's "Violations Detected" metric resets on page reload
  (session-local counter, by design); "Active Grants," "Tracked
  Copies," and "Quarantined Assets" are always live from the backend
  and persist across reloads.

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
- **Expiry, Policy Evaluation, and Continued-Use Violation** (major
  checkpoint milestone): recommended commit message
  `feat(policy): enforce purpose lifecycle on retrieved data`.
- **Operational APIs — Source Asset Management and Grant Revocation**:
  recommended commit message
  `feat(assets): add source asset creation and grant revocation`.
- **Deterministic Demo Scenario Engine**: recommended commit message
  `feat(demo): add deterministic PurposeSeal scenarios`.
- **Minimal Judge-Ready Dashboard**: recommended commit message
  `feat(ui): add judge-ready PurposeSeal dashboard`.
- **Interactive Data Lineage Visualization**: recommended commit
  message `feat(lineage-ui): visualize purpose-bound data provenance`.
- **Stronger Remediation Workflow**: recommended commit message
  `feat(remediation): persist violation response workflow`.
- **Actor Roles and Minimal Authentication**: recommended commit
  message `feat(auth): add role-aware actor authentication`.

## Next Step

The full PurposeSeal loop (create grant → retrieve → copy/derive →
evaluate use → detect violation → quarantine → persist remediation) is
complete, reachable entirely over HTTP, demoable in three single-call
scenarios, has a judge-ready dashboard with a live
Summary/Result/Timeline/Lineage-graph view, and now has minimal
role-aware authentication sitting alongside (not inside) the purpose
policy engine — see "Judge demo via the dashboard" above for the
unauthenticated demo flow, and "Actor Roles and Minimal Authentication"
for the auth-specific worked example. Remaining work, in rough
priority order: a real browser/click-through verification pass once a
browser-automation tool is available (see Known Issues), a dedicated
global audit-trail view (beyond the per-scenario timeline), a
resolve/dismiss workflow for an open `Remediation` if ever requested,
and — if the auth surface needs to be judge-visible, not just
API-visible — a login screen in the dashboard.
