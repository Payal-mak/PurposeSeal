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
  SQLite database (file-based, survives restarts).
- **Frontend**: not yet scaffolded (planned: React + Vite + Tailwind).
- **Time handling**: a single `Clock` singleton (`app/clock.py`) is the only
  source of "now" for business logic. It wraps real UTC time plus an
  in-memory offset that can be advanced (`clock.advance(minutes=...)`) so a
  30-minute expiry can be demoed in seconds. No business code calls
  `datetime.now()` directly.
- **Audit trail**: a dedicated `AuditLog` table, written to via
  `app/audit.py:write_audit_log`, decoupled from any single feature's
  router so every future feature can log into the same trail.

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

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Liveness check |
| POST | /grants | Create a purpose-bound access grant |
| GET | /grants | List all grants |
| GET | /grants/{id} | Get one grant by id |

## Demo Journey

1. `POST /grants` with a subject, purpose, resource, and duration — grant
   is created, `ACTIVE`, and audited.

(Later steps — retrieval, copy tracking, expiry, violation detection,
blocking/quarantine, frontend walkthrough — will be appended here as each
feature lands.)

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

## Known Issues / Deferred Work

- Frontend not started.
- No grant expiry transition, data retrieval, copy/lineage tracking,
  purpose-violation policy engine, or quarantine action yet — these are
  the next features per the hackathon priority list.
- No revoke-grant endpoint yet.

## Git History

- **Feature 1 — Create purpose-bound access grant**: recommended commit
  message `feat(grants): add purpose-bound access grant creation`
  (+ `test(grants): cover grant creation happy/invalid/edge cases`, or
  combined into one commit — see Git Commands below).

## Next Step

Retrieve simulated sensitive data under an active grant, and associate the
retrieved data with the purpose it was accessed under (attach/inherit the
purpose seal), per the hackathon priority list.
