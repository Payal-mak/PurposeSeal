# PurposeSeal

Purpose-bound access lifecycle enforcement for retrieved data — not just access grants.

> Full build history, every design decision, and every feature's test
> results live in [`docs/PROJECT_PROGRESS.md`](docs/PROJECT_PROGRESS.md).
> This README describes the system as it exists today.

## Problem

Access to sensitive data is often granted for a specific, bounded
purpose — a clinical trial, a fraud investigation, a limited-time
research project. The common mistake is treating **revoking the
access grant** as equivalent to **enforcing the purpose**.

It isn't. Once data has been legitimately retrieved under a valid
purpose, revoking or expiring the grant that authorized that retrieval
does nothing to the copy that already left the system: a spreadsheet
export, a derived report, a downstream analysis dataset. The grant
being gone doesn't reach back into wherever that data ended up. If the
original purpose was "clinical trial screening" and someone later
reuses that same retrieved data for "marketing analytics," or keeps
using it after the trial's window closes, **no access-control check on
the grant will ever catch it** — the grant was already exercised
legitimately; the problem is what happens *after*.

PurposeSeal's premise: purpose limitation has to be enforced at the
point of **use**, against data that's already been retrieved and
copied, not only at the point of **retrieval**.

## Solution

**Purpose-bound lifecycle enforcement.** Every retrieval and every
subsequent copy or derivative carries its originating grant's identity
forward — its "purpose seal" — for as long as that data exists in the
system. Every attempted *use* of that data (not just retrieval) is
evaluated live, at the moment of use, against that seal: does the
actor match, is the purpose still what was originally authorized, is
the grant still within its validity window, has it been revoked?
A violation doesn't just get logged — the offending asset is
quarantined and a remediation record is created, while everything
upstream and unrelated (the original source data, sibling copies)
stays untouched.

## Core Journey

```
Grant → Retrieve → Propagate purpose → Copy → Expire →
Continued-use attempt → Policy decision → Remediation → Audit
```

1. **Grant** — a purpose-bound authorization is issued: who (`subject`),
   why (`purpose`), over what (`asset_id`), for how long
   (`duration_minutes`), for which operations (`allowed_operations`).
2. **Retrieve** — the actor pulls the data under that grant. A new,
   purpose-sealed `DataAsset` is created, linked back to both the
   source asset and the grant that authorized it.
3. **Propagate purpose** — the seal (`origin_grant_id` → purpose →
   expiry) is inherited, not copied as a value, by every subsequent
   copy or derivative — resolved live by joining back to the grant,
   never duplicated onto each row.
4. **Copy** — the actor makes an exact copy or a transformed
   derivative. Either way, the child inherits its root and origin
   grant from its parent; a "copy" can never claim a different origin
   than the data it was actually copied from.
5. **Expire** — time passes (in this simulation, the abstracted clock
   is advanced instantly) and the grant's validity window closes.
6. **Continued-use attempt** — someone tries to use the already-
   retrieved/copied data again: for its original purpose past expiry,
   for a *different* purpose than it was retrieved for, or after its
   grant was explicitly revoked.
7. **Policy decision** — the policy engine evaluates that attempt
   fresh, every time: actor match, purpose match, operation
   permitted, grant's live status. Returns `ALLOW` or `DENY` with a
   specific `reason_code`.
8. **Remediation** — a purpose-lifecycle `DENY` (expired, mismatched,
   or revoked — as opposed to an ordinary access-control denial like
   the wrong actor) quarantines the offending asset and persists a
   `Remediation` record: reason, corrective action, review status.
9. **Audit** — every step above writes an immutable, append-only audit
   event, from `SOURCE_ASSET_CREATED` through `COMPLIANCE_REVIEW_REQUIRED`,
   forming a complete, queryable timeline for any asset or grant.

## Architecture

```
┌─────────────────────┐        HTTP/JSON        ┌──────────────────────────┐
│      Frontend        │ ───────────────────────▶│         Backend          │
│ React + Vite +        │                          │        FastAPI          │
│ Tailwind + React Flow │◀─────────────────────── │                          │
└─────────────────────┘                          │  api → services → models │
                                                    │       ↓                 │
                                                    │  Policy Engine           │
                                                    │  (deterministic Python)  │
                                                    │       ↓                 │
                                                    │  SQLAlchemy → SQLite     │
                                                    └──────────────────────────┘
```

- **Frontend** — React 19 + Vite 8 + Tailwind v4, a single judge-ready
  dashboard (no client-side routing by design): live summary metrics,
  one-click deterministic scenario controls, a result panel, a full
  audit-event timeline, and an interactive lineage graph
  (`@xyflow/react`) with a per-node detail panel. Talks to the backend
  over plain `fetch`.
- **Backend** — FastAPI, layered `api → services → models/schemas →
  db → core`. Routers are thin (parse request, call a service, shape
  the response); all business logic lives in `services/`. Pydantic
  models validate every request at the boundary (`extra="forbid"` on
  every create schema).
- **SQLite** — via SQLAlchemy, one file (`backend/purposeseal.db` by
  default, overridable via `PURPOSESEAL_DATABASE_URL`), foreign keys
  explicitly enforced (`PRAGMA foreign_keys=ON`, off by default in
  SQLite). Chosen for hackathon-scale simplicity — see Limitations.
- **Policy Engine** — `services/policy_service.py`, plain deterministic
  Python/SQL. `evaluate_use()` re-checks actor, purpose, operation, and
  live grant status on *every* use attempt against the asset's
  inherited origin grant — never against a cached/stale decision.
- **Lineage** — every `DataAsset` carries `parent_asset_id` (immediate
  predecessor) and `root_asset_id` (denormalized straight to the true
  root, so a whole tree resolves in one query, no recursive walk).
  Rows are never reparented after creation, so a lineage cycle is
  impossible by construction, not by a runtime check.
- **Audit** — `AuditLog` is append-only; nothing is ever deleted or
  edited. A fixed, documented vocabulary of event types
  (`SOURCE_ASSET_CREATED`, `GRANT_CREATED`, `DATA_RETRIEVED`,
  `COPY_CREATED`, `DERIVED_ASSET_CREATED`, `DATA_USE_ATTEMPTED`,
  `USE_ALLOWED`, `USE_BLOCKED`, `PURPOSE_VIOLATION`,
  `ASSET_QUARANTINED`, `COMPLIANCE_REVIEW_REQUIRED`, `GRANT_REVOKED`,
  `DATA_RETRIEVAL_DENIED`, `COPY_CREATION_DENIED`) is extended, never
  repurposed, as features are added.

## Setup

Tested on Windows with Python 3.11.5, Node 22.22.3, npm 11.3.0.

**Backend** (from `backend/`):
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Confirm it's up: `http://localhost:8000/health` → `{"status": "ok", "app_name": "PurposeSeal"}`.
Interactive API docs (FastAPI's built-in Swagger UI): `http://localhost:8000/docs`.

**Frontend** (from `frontend/`, in a second terminal):
```
npm install
npm run dev
```
Open the printed URL (`http://localhost:5173` by default). The
backend's default CORS origins already allow this; no configuration
needed for the default ports.

Both default configs work together out of the box — no `.env` file is
required to run the demo locally.

## Testing

**Backend** (from `backend/`, with the virtualenv active):
```
pytest -q
```
Currently: **165 passed**, 0 failed, 0 skipped.

With coverage (measured, not assumed — `pytest-cov` is a dev/test-only
dependency):
```
pytest -q --cov=app --cov-report=term-missing
```
Currently: **97% line coverage** (1033 statements, 27 missed — a
handful of narrow error-handling branches; this is line coverage, not
a claim of complete behavioral verification).

A dedicated end-to-end regression suite
(`backend/tests/test_e2e_journeys.py`) drives the three central
journeys — legitimate, expired-purpose, wrong-purpose — through the
real HTTP API and checks the API response, the persisted database
state, and the audit trail together for each one:
```
pytest tests/test_e2e_journeys.py -v
```

**Frontend** (from `frontend/`):
```
npm test -- --run
```
Currently: **24 passed**, 0 failed, 0 skipped.

Every backend test runs against its own isolated, temporary SQLite
file (via pytest's `tmp_path`) — the suite can be re-run any number of
times with no manual cleanup.

## Demo

**Recommended — no terminal needed, three clicks:**

1. Start the backend and frontend as in Setup, above.
2. Open the dashboard. The Summary row shows four metrics (Active
   Grants, Tracked Copies, Violations Detected, Quarantined Assets),
   all `0` on a fresh database.
3. **Click "Run Valid Scenario."** Result shows **ALLOW**, the reason,
   matching original/requested purpose, the grant's expiry, and "no
   action needed." The Journey Timeline shows six steps ending in "Use
   Allowed." Data Lineage shows the original asset → its retrieved
   copy, both `ACTIVE`.
4. **Click "Run Expired-Purpose Scenario."** Result flips to
   **BLOCKED** — reason names the expired grant, Remediation Status
   shows `COMPLIANCE_REVIEW_REQUIRED`, Corrective Action suggests
   issuing a new grant. Quarantined Assets and Violations Detected
   both go from `0` to `1`. Click the now-`QUARANTINED` lineage node
   to see its full detail panel (parent, root, grant, purpose, expiry,
   status, and its fingerprint).
5. **Click "Run Purpose-Mismatch Scenario."** Result again shows
   **BLOCKED**, naming `clinical_trial_screening` as the original
   purpose and `marketing_analytics` as the requested one. Quarantined
   Assets and Violations Detected both increment again.
6. Re-run any scenario as many times as you like — every click creates
   a fresh asset/grant chain, so nothing needs to be reset between
   runs.
7. To see graceful failure handling: stop the backend, click any
   scenario button — the Result panel shows "Could not reach the
   PurposeSeal backend. Is it running?" instead of a blank screen or a
   crash. Restart the backend and click again; it recovers immediately.

**Fastest way to see the whole product from a terminal** (no manual
grant/retrieval setup, safe to call repeatedly):
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/demo/scenarios/legitimate" -Method Post
Invoke-RestMethod -Uri "http://127.0.0.1:8000/demo/scenarios/expired" -Method Post
Invoke-RestMethod -Uri "http://127.0.0.1:8000/demo/scenarios/purpose-mismatch" -Method Post
```
Each call drives one complete journey server-side and returns the
final decision plus its full audit timeline in one response.

A full manual, step-by-step walkthrough (raw `POST`/`GET` calls for
every step, including revocation) is in
[`docs/PROJECT_PROGRESS.md`](docs/PROJECT_PROGRESS.md#manual-step-by-step-journey).

## API Overview

Current routes only — see `/docs` (Swagger UI) for full request/response
schemas.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/auth/register` | Create a login identity (`username`, `password`, one of five roles) |
| POST | `/auth/login` | Exchange credentials for a Bearer access token |
| GET | `/auth/me` | Return the authenticated caller's identity |
| POST | `/assets` | Create an original/root `DataAsset` |
| GET | `/assets` | List assets (`state`, `asset_type`, `root_only` filters) |
| GET | `/assets/{id}` | Get one asset, with its purpose seal resolved |
| GET | `/assets/{id}/lineage` | Full lineage tree (`{root_asset_id, nodes, edges}`) |
| POST | `/grants` | Create a purpose-bound access grant |
| GET | `/grants` | List all grants (live-evaluated status) |
| GET | `/grants/{id}` | Get one grant (live-evaluated status) |
| GET | `/grants/{id}/status` | Evaluate a grant's status with an explanation |
| POST | `/grants/{id}/revoke` | Revoke a grant — requires `COMPLIANCE_OFFICER`/`ADMIN`; idempotent |
| POST | `/retrievals` | Retrieve data under a grant, creating a purpose-sealed copy |
| POST | `/copies` | Create a copy or derived asset from an already-retrieved one |
| POST | `/uses` | Evaluate an attempted use against its origin purpose — always `200`, decision is the payload |
| GET | `/dev/clock` | **Simulation only** — current simulated time |
| POST | `/dev/clock/advance` | **Simulation only** — advance simulated time |
| POST | `/demo/scenarios/legitimate` | **Simulation only** — full legitimate-use journey in one call |
| POST | `/demo/scenarios/expired` | **Simulation only** — full purpose-expiry journey in one call |
| POST | `/demo/scenarios/purpose-mismatch` | **Simulation only** — full purpose-mismatch journey in one call |

`/dev/*` and `/demo/scenarios/*` exist only when
`PURPOSESEAL_ENABLE_DEV_ENDPOINTS` is true (the default for this
build); a production deployment would set it to `false` to remove
them from the API surface entirely.

Authentication is intentionally minimal and **separate from purpose
authorization** — see Key Engineering Decisions below. Only
`/grants/{id}/revoke` and, when a Bearer token is present, the
`actor` field on `/retrievals`/`/copies`/`/uses` are auth-aware; every
`GET` and the dev/demo endpoints are unauthenticated by design.

## Beyond the PRD

The original problem statement asked for one specific thing: a system
that manages simulated purpose-bound grants, tracks what happens to
data retrieved under them, and — in a convincing demonstration —
correctly identifies and addresses continued use of that data after
its authorizing purpose no longer applies. Everything below goes
beyond that literal ask; none of it was required by the PRD.

- **A full web dashboard.** The PRD only required "a convincing
  demonstration" — it didn't call for a UI at all. Instead of a script
  or raw API calls, PurposeSeal has a real React/Tailwind dashboard
  with live metrics, one-click scenarios, a decision panel, and a
  rendered audit timeline.
- **An interactive lineage graph.** Provenance is exposed as a
  clickable visual tree (`@xyflow/react`), not just rows in a table —
  click any node to see its parent, root, grant, purpose, expiry,
  status, and fingerprint in one panel.
- **Role-aware authentication**, kept strictly separate from purpose
  authorization. The PRD's scope was the purpose-enforcement engine
  itself; login/roles (Researcher, Clinician, Analyst, Compliance
  Officer, Admin) are an added layer, deliberately designed so that
  being authenticated never implies a use is purpose-valid.
- **SHA-256 content fingerprinting** as supporting provenance evidence
  for exact copies — layered on top of the lineage tracking the PRD
  actually asked for, with its limitations (can't detect transformed
  content) stated explicitly rather than oversold.
- **A persistent remediation workflow.** The PRD asked the system to
  "identify and address" violations; this build goes further by
  persisting a structured `Remediation` record (reason, corrective
  action, review status) for every violation, not just logging a
  denial and moving on.
- **Explicit, RBAC-gated, idempotent grant revocation** — a dedicated
  `POST /grants/{id}/revoke` endpoint (repeat calls are safe, no
  duplicate audit events), rather than relying only on natural
  time-based expiry to end a grant's validity.
- **Deterministic one-call demo scenarios** (`/demo/scenarios/*`) built
  specifically so a live judged demo can be re-run indefinitely with
  no timing dependencies or manual setup — a reliability concern the
  PRD didn't ask about but that matters a great deal in a judging room.
- **A dedicated reliability/idempotency hardening pass** — a review
  pass, after the core was stable, specifically hunting for the class
  of bugs that embarrass a live demo (double-clicks, duplicate
  requests, a quarantined asset that could still be copied through an
  unguarded path) and fixing what was found with regression tests.
- **A full end-to-end regression suite with honestly measured
  coverage** — 165 backend tests, 24 frontend tests, 97% line coverage
  measured with `pytest-cov`, not claimed from memory.
- **A consistent, frontend-friendly error contract** across the entire
  API (`{error_code, message}` everywhere, including validation
  errors), rather than leaking framework-default error shapes.

## Key Engineering Decisions

- **Deterministic policy engine, not a model.** `evaluate_use()` is
  plain Python/SQL: fixed comparisons against actor, purpose,
  operation, and a live-evaluated grant status. Given the same inputs,
  it always returns the same decision, and every decision traces to an
  exact `reason_code` a compliance reviewer can point to. There is no
  ambiguity to audit around.
- **SQLite for a hackathon MVP.** One file, zero infrastructure to
  stand up, restart-safe, and sufficient for the single-writer,
  demo-scale access pattern this project needs. Foreign keys are
  explicitly turned on (SQLite doesn't enable them by default) so an
  invalid lineage/grant reference fails loudly instead of silently
  persisting.
- **A controlled simulation clock, not `datetime.now()`.** Every "now"
  in this codebase flows through a single `Clock` abstraction. In
  production it behaves like the real clock; in this build, an
  `advance()` call lets a 30-minute grant expiry be demonstrated in
  under a second, with zero branching in business logic for "are we
  demoing." All expiry comparisons are exactly as deterministic and
  testable as everything else.
- **Persistent, structural lineage**, not a point-in-time snapshot.
  `parent_asset_id`/`root_asset_id`/`origin_grant_id` are set once at
  creation and never rewritten — a full provenance tree is always a
  single query away, and a cycle is impossible because nothing is ever
  reparented.
- **Purpose inheritance by live join, not by copying values.** A
  descendant asset doesn't store its own "purpose" and "expiry" —
  it stores an `origin_grant_id`, and every purpose/expiry check
  resolves that reference at the moment it's needed. One source of
  truth, so a grant's status can never drift out of sync with what a
  descendant asset believes about it.
- **No LLM anywhere in the decision path.** Whether a specific use is
  authorized is a compliance-relevant, auditable decision — it must be
  reproducible, explainable by exact rule, and not subject to model
  drift, prompt sensitivity, or hallucination. Every `ALLOW`/`DENY` in
  this system is a deterministic function of stored, structured data.

## Limitations

- **SHA-256 fingerprinting only identifies exact content.** It proves
  two blobs are byte-identical; it cannot detect a summarized,
  reworded, or otherwise transformed derivative. Lineage — not the
  hash — is this project's primary provenance mechanism; the
  fingerprint is supporting evidence for a narrower claim. See
  `backend/app/services/fingerprint.py` and the "Fingerprinting and
  Provenance Evidence" entry in `docs/PROJECT_PROGRESS.md`.
- **SQLite is a prototype-scale choice.** No concurrent-writer scaling,
  no read replicas, no schema migration tool (tables are created via
  `create_all`, which never alters existing ones).

The full, continuously-updated list — including everything resolved
along the way — is in the Known Issues / Deferred Work section of
[`docs/PROJECT_PROGRESS.md`](docs/PROJECT_PROGRESS.md).

## Future Production Evolution

A real deployment of this concept would likely need:

- **Enterprise identity** — SSO/OIDC against a real IdP, proper role
  provisioning/de-provisioning, MFA, session/token revocation — in
  place of this build's self-registration and hand-rolled JWT.
- **Event streaming** — audit events and quarantine/remediation
  triggers published to a durable stream (Kafka, EventBridge, etc.)
  instead of living only in a local table, so downstream compliance
  tooling and alerting can react in near-real-time.
- **A dedicated policy engine** — externalizing purpose/operation
  rules into something like OPA/Rego or a rules-as-data system, so
  policy can change without a code deploy, while keeping the same
  deterministic, auditable evaluation model this project already
  establishes.
- **Stronger provenance** — content-similarity/watermarking techniques
  layered on top of (never instead of) structural lineage, to narrow
  the gap this project is explicit about: an exact hash can't catch a
  transformed derivative.
- **Scalable relational/graph storage** — a real RDBMS (or a
  graph database specifically for lineage traversal at scale) behind a
  connection-pooled, horizontally-scalable backend, replacing SQLite's
  single-file, single-writer model.

None of the above exists in this build. They're named here as the
honest next steps a production version would need, not as features
currently in progress.
