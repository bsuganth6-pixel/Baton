# Changelog and decision log

One entry per build step. A decision is something the spec left open; each is open to review.

## Step 1: demo data, ground truth, models, first migration

Decisions

1. **Only redacted text is stored** (`Source.text`, `Chunk.text`). The original upload is never persisted; `content_sha256` of the original bytes makes seeding idempotent. Evidence quotes are checked against `Chunk.text`, which is exactly what the LLM sees.
2. **Clearance** is an integer 0 to 3 on each source (public, internal, confidential, restricted), CHECK-constrained, defaulting to 3 so a forgotten value fails closed in both the ORM and the database. Viewer clearance will be derived from role in `protect/acl.py`.
3. **A claim inherits the clearance of every source that shaped its label**, including its conflict counterparts. Otherwise a "Conflicting" tag would reveal that a hidden source exists. `ground_truth.json` encodes this as `required_clearance`; the backup-frequency conflict spans levels on purpose.
4. **Label rules assumed by the ground truth**, in precedence order: Missing (no value), Conflicting (two or more distinct values), Stale (newest evidence older than 365 days before `as_of`), Supported (two or more distinct sources agree), Single source. Step 4's `confidence.py` follows these or amends them together with `ground_truth.json`.
5. `projects.as_of` fixes the staleness reference date (demo: 2026-10-20) so results do not drift as the calendar moves.
6. Enums are stored as lowercase strings validated by the ORM, not native Postgres enums (`ALTER TYPE` is painful on a hosted database). CHECK constraints that rely on those strings are covered by tests.
7. `Claim.version` increments on every review action (created, edited, accepted, rejected); `claim_versions` keeps one snapshot per version.
8. `evidence.chunk_id` has no ON DELETE rule: a source that backs evidence cannot be deleted from under a claim, while deleting a project cascades cleanly (tested on SQLite and Postgres).
9. Failed ingests are stored (`status=failed`, user-safe `error`) so the Sources page can show what is missing instead of hiding it.
10. `DATABASE_URL`: `postgres://` and `postgresql://` are rewritten to `postgresql+psycopg2://` because Render hands out `postgres://`. A blank value is an error, not a silent default.
11. The Postgres half of the suite wipes the `public` schema, so it runs only against a database whose name contains `test`.
12. Planted secrets use invented formats, so scanners stay quiet except for the JWT-shaped token, which gitleaks flags. `.gitleaks.toml` allowlists `demo-data/` and nothing else.
13. Additions outside the given tree: `CHANGELOG.md`, `.gitleaks.toml`, `backend/pytest.ini`, `demo-data/README.md`.

Deferred by the build order: `docker-compose.yml`, `render.yaml`, `vercel.json`, CI and the frontend.
