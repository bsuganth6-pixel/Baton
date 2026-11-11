# Baton

Baton turns authorised project activity (GitHub export, notes, task CSV, chat export, leaver Q&A) into a reviewed, permission-aware handover, so critical knowledge does not leave with the people who hold it.

Flow: **Connect, Reconstruct, Verify, Review, Handover.** Built solo for CodePuneThinkers, Round 2.

## Status

| Step | What | State |
|---|---|---|
| 1 | Demo data, ground truth, models, first migration | done |
| 2 | Ingest, chunker, redaction | |
| 3 | Extract and verify | |
| 4 | Confidence, conflicts, questions | |
| 5 | API, auth and roles, audit log, background jobs, `/health` | |
| 6 | Review UI with evidence side panel | |
| 7 | Role views, render filtering, acknowledgement | |
| 8 | Deploy (Render, Vercel), CI, final README | |

## Rules the build is held to

1. Every claim has at least one evidence quote, and the server checks it is an exact substring of the cited chunk. Otherwise the claim is dropped and logged.
2. Confidence labels (Supported, Single source, Conflicting, Stale, Missing) come from rules in `confidence.py`, never from the LLM.
3. Secrets and PII are redacted before any LLM call.
4. A claim is visible only if every source behind it is within the viewer's clearance. Otherwise: "N items withheld".
5. Source text is data, never instructions.
6. Demo data is synthetic. No secrets are committed.

## Run the backend tests

Python 3.11 or newer (tested on 3.12).

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                      # SQLite; the Postgres half is skipped
TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/baton_test pytest   # SQLite and Postgres
```

The Postgres run wipes the `public` schema of that database, so its name must contain `test`. To apply the migration to a database: `DATABASE_URL=... alembic upgrade head` (a `postgres://` URL is accepted).

## Demo data

`demo-data/` is a synthetic scenario (CampusConnect: a backend lead leaves and a successor takes over) with planted conflicts, one stale decision, one missing owner, fake secrets and fake personal data. `demo-data/ground_truth.json` records everything planted. See `demo-data/README.md`.

## Limitations (so far)

- `audit_log` is append-only by convention; the database does not enforce it.
- No application code yet beyond settings and the schema. The decisions behind the schema are in `CHANGELOG.md`.

## AI assistance and libraries (running list)

- AI assistance: Claude (Anthropic) wrote the step 1 scaffolding, synthetic demo data, models, migration and tests, and ran them. No LLM is used at runtime yet.
- Libraries: SQLAlchemy 2.1.8, Alembic 1.20.1, psycopg2-binary 2.9.13, pydantic 2.14.3, pydantic-settings 2.16.2, pytest 9.2.0.
- Verification tools, not shipped: PostgreSQL 16, gitleaks 8.28.0, ruff.
