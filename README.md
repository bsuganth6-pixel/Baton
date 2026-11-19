# Baton

Baton turns authorised project activity (GitHub export, notes, task CSV, chat export, leaver Q&A) into a reviewed, permission-aware handover, so critical knowledge does not leave with the people who hold it.

Flow: **Connect, Reconstruct, Verify, Review, Handover.** Built solo for CodePuneThinkers, Round 2.

## Status

| Phase | What | State |
|---|---|---|
| 0 | Foundation: schema, migration, synthetic demo data, ground truth (commit `5fc1225`) | done |
| 1 | Backend core: FastAPI app, `/health`, sign-in, roles, audit trail, safe errors | done |
| 2 | Connect: upload, validate, redact, chunk, store, list, search, demo seeding | done |
| 3 | Extract and verify: LLM claims, exact-quote check, prompt-injection defence, background jobs | done |
| 4 | Deterministic confidence labels, conflicts, missing‑knowledge questions | done |
| 5 | Review API: accept, edit, reject, versions, conflict resolution, answers | done |
| 6 | Dashboard UI (FastAPI‑served HTML, CSS and JavaScript) | done |
| 7 | Handover views by role, acknowledgement | done |
| 8 | Deployment (Render + Vercel), CI, smoke test, final docs | API deployed; dashboard served. Full‑stack verified, tests pass (2 env‑blocked psycopg2 failures remain) |

After the initial foundation, the build order was adjusted for clarity:
- Phase 3 (LLM extraction) now completes with deterministic confidence rules (Phase 4) fully integrated.
- Phase 5 (review) now includes the new `GET /api/review/claims` endpoint for the frontend Review Workspace.
- Phase 7 (handover) includes the additional `/api/handover/acknowledge` endpoint and resolves `view_sha256` handling.
- The premium frontend (Phase 6) is delivered as a single vanilla‑JS SPA with the following views:
  - **Extraction** – run the pipeline, monitor jobs, view results
  - **Knowledge base** – claim list, label filters, evidence drawer
  - **Review** – proposed‑claim queue with edit/accept/reject and version tracking
  - **Conflicts** – side‑by‑side claim values, status open/resolved
  - **Questions** – open questions and answer form (manager/leaver permission)
  - **Assistant** – chat interface for the successor AI, with verified citations
  - **Handover** – successor view with stats, withheld counts and acknowledge button
  - **Audit** – historical log of all actions (pipeline, review, answer, handover, acknowledge)
- **UI/UX enhancements** include hover micro‑animations, responsive design, dark/light theme toggle, and a “one dealt moment” per page (dealt moment). The design uses taste‑skill’s equal‑edge grid layout and ui‑ux‑pro‑max’s data‑dense dashboard guidance.

## Rules the build is held to

1. Every claim has at least one evidence quote, and the server checks it is an exact substring of the cited chunk. Otherwise the claim is dropped and logged.
2. Confidence labels (Supported, Single source, Conflicting, Stale, Missing) come from deterministic rules in `app/ingest/confidence.py`, never from the LLM.
3. Secrets and PII are redacted before anything is stored or sent to an LLM. Original uploads are never persisted.
4. A claim is visible only if every source behind it is within the viewer's clearance.
5. Source text is data, never instructions.
6. Demo data is synthetic and no secrets are committed. `ground_truth.json` is a test oracle; the live app never reads it.
7. After a pipeline run, any claim that fails quote verification (including missing quotes) is dropped and counted as “dropped” in the result.
8. All new endpoints use `require_permission`/`current_user`/clearance checks; successor/teammate have only `ACKNOWLEDGE`; assistant is permission‑gated only by clearance.
9. Responses follow the single `ApiError` shape with request id; no input is echoed.
10. The frontend SPA is served from the FastAPI app, no framework migration, and all navigation is client‑side with lazy view loading.

## Run it locally

Python 3.11 or newer (tested on 3.12), PostgreSQL 16.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env          # then set DATABASE_URL and, for the demo, DEMO_MODE=true and DEMO_PASSWORD
alembic upgrade head
uvicorn app.main:create_app --factory --reload
```

Interactive API docs: `http://localhost:8000/api/docs`. With `DEMO_MODE=true` the server creates these users (all with `DEMO_PASSWORD`) and ingests `demo-data/` on start. Starting again changes nothing.

| Email | Role |
|---|---|
| `admin@baton.example` | admin |
| `manager@baton.example` | manager |
| `leaver@baton.example` | leaver |
| `successor@baton.example` | successor |
| `teammate@baton.example` | teammate |

## Deploy Baton

The FastAPI app serves the interactive dashboard at `/` and the API under `/api`. The same app can be deployed to both providers. Both deployments need a persistent PostgreSQL database with a connection URL reachable from each provider. Do not use SQLite for hosted deployments.

### Render

Create a new Blueprint from this repository and use the root `render.yaml`. It updates the existing `Baton` web service and creates a free PostgreSQL database in Singapore. The API runs on Render's free web-service plan, and Render runs `alembic upgrade head` before starting it. Set `DEMO_PASSWORD` in the service's Environment settings; the Blueprint intentionally does not store it. Render's free database expires after 30 days and is for testing/prototyping, not durable production data.

### Vercel

Import this repository and set **Root Directory** to `backend`. Vercel detects the FastAPI entry point in `index.py`; the `.python-version` file selects Python 3.12, and `vercel.json` includes the dashboard assets in the function bundle. Set these environment variables for the deployment:

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `DATABASE_URL` | The external connection URL for the same Render PostgreSQL database |
| `SECRET_KEY` | The same randomly generated secret used by Render |
| `COOKIE_SECURE` | `true` |
| `DEMO_MODE` | `true` |
| `DEMO_PASSWORD` | Set the same shared demo password as Render |

Deploy Render first so its startup applies database migrations; Vercel does not run migrations. Keep `SECRET_KEY` private and set it through each provider's environment settings, not in Git or chat. The dashboard is served from the same origin as the API, so no CORS origin is needed. If you host a separate frontend, set `ALLOWED_ORIGINS` to its exact HTTPS origin on both deployments.

In demo mode, the sign-in page offers a **Demo login** button that signs directly into the public demo account. Production seeding creates only `teammate@baton.example` (limited teammate access, internal clearance, no upload permission); local demo mode retains all five role accounts. `DEMO_PASSWORD` is intentionally exposed by the demo-credentials endpoint while demo mode is enabled, so use only synthetic data and a password that is not used elsewhere. Public registration remains disabled.

## Roles and clearance

Sources carry a clearance level (0 public, 1 internal, 2 confidential, 3 restricted). A role sees sources up to its own level; anything above answers `404`, exactly like a source that does not exist.

| Role | Sees up to | Can |
|---|---|---|
| admin, manager | 3 | everything |
| leaver | 2 | upload, review claims, answer questions |
| successor | 2 | read, acknowledge |
| teammate | 1 | read, acknowledge |

## API so far

| Method and path | Who | Purpose |
|---|---|---|
| `GET /health`, `/api/health` | anyone | liveness and database check |
| `POST /api/auth/login` | anyone | sign in: bearer token and `HttpOnly` session cookie |
| `POST /api/auth/logout` | anyone | clear the cookie |
| `GET /api/auth/me` | signed in | user, clearance, permissions |
| `GET /api/sources` | signed in | sources within your clearance |
| `POST /api/sources` | upload permission | multipart `file`, `clearance` (0 to 3), optional `kind` |
| `GET /api/sources/{id}`, `/chunks` | signed in | metadata, redacted chunks |
| `GET /api/search?q=` | signed in | search redacted chunks within your clearance |
| `GET /api/review/claims` | reviewer (admin/manager) | claim queue with pending reviews |
| `PATCH /api/review/claims/{claim_id}` | reviewer | edit claim text |
| `POST /api/review/claims/{claim_id}/accept`, `/api/review/claims/{claim_id}/reject` | reviewer | accept/reject claim with optional comment |
| `GET /api/pipeline/jobs`, `GET /api/pipeline/jobs/{job_id}` | anyone | background pipeline job status |
| `POST /api/pipeline/run` | upload permission | run extraction pipeline on sources |
| `GET /api/questions`, `POST /api/questions/{question_id}/answer` | leaver/manager | ask knowledge questions, answer with evidence |
| `GET /api/handover` | successor (clearance >=2) | handover view with withheld counts |
| `POST /api/handover/acknowledge` | successor | acknowledge handover with view fingerprint |
| `GET /api/audit` | admin | audit log of all actions |
| `POST /api/assistant/ask` | anyone (clearance gated) | successor AI assistant with citations |

Cookie sessions must send `X-Baton-Client: web` on anything that changes data (CSRF defence); bearer tokens need no header.

## Tests

```bash
cd backend
pytest                                                                                    # SQLite
TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/baton_test pytest        # SQLite and Postgres
```

The Postgres run wipes the `public` schema of that database, so its name must contain `test`.

## Provenance and disclosure

- **Foundation, commit `5fc1225` (2026-11-11):** the database models, Alembic migration `0001`, the synthetic CampusConnect demo data, `ground_truth.json` and their tests. It predates the Round 2 build prompt; if the event rules treat it as pre-existing scaffolding, that is what it is. History is preserved, never rewritten.
- **Everything after it** (`git log 5fc1225..`) is the Round 2 application: API, sign-in, ingestion, redaction, audit, middleware and their tests.
- **AI assistance:** Claude (Anthropic) wrote the code and tests in this repository, ran them, and fixed what failed. The author directs the work.
- **Libraries:** FastAPI 0.162.4, Starlette 1.10.0, uvicorn 0.54.0, python-multipart 0.0.33, SQLAlchemy 2.1.8, Alembic 1.20.1, psycopg2-binary 2.9.13, pydantic 2.14.3, pydantic-settings 2.16.2, PyJWT 2.15.1, bcrypt 5.0.0; tests use pytest 9.2.0 and httpx2 2.16.0.
- **Verification tools, not shipped:** PostgreSQL 16, gitleaks 8.28.0, ruff.

## Limitations (so far)

- PII detection is pattern based. Names, street addresses and free-form identifiers are not detected; people's names are kept on purpose because a handover must say who owns what.
- Uploads are limited to `.md`, `.txt`, `.csv` and `.json` files of at most 1 MiB, so they are never spooled to disk.
- Session tokens cannot be revoked one by one (they last 8 hours); deactivating a user cuts access at once. The login throttle lives in memory and resets on restart.
- Search is a plain substring match, which is fine at demo scale.
- `audit_log` is append-only by convention; the database does not enforce it.
- One project per deployment.
- LLM extraction runs through OpenRouter (`OPENROUTER_API_KEY`) and falls back to a deterministic rule-based extractor when no key is configured, so the workflow always works offline.

