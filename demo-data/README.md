# demo-data

Synthetic data for the Baton demo. Everything here is invented: the people, the project (CampusConnect), the numbers, and every secret and personal detail. Nothing is real, and nothing real may ever be added.

**Scenario.** Arjun Mehta, backend lead of the CampusConnect student app, leaves on 2026-11-30 and Priya Nair takes over. The data is as of 2026-10-20.

| File | Kind | Clearance |
|---|---|---|
| `github/export.json` | GitHub export (README, issues, PRs, commits) | 1 internal |
| `notes/architecture-overview.md` | notes | 1 internal |
| `notes/meeting-notes-2025-02-18.md` | notes | 1 internal |
| `notes/deployment-runbook.md` | notes | 2 confidential |
| `notes/vendor-contracts.md` | notes | 3 restricted |
| `tasks/tasks.csv` | task tracker export | 1 internal |
| `chat/dev-channel.json` | chat export | 1 internal |
| `chat/leads-private.json` | chat export | 3 restricted |
| `leaver_qa/exit-qa.md` | leaver Q&A | 2 confidential |

Clearance levels: 0 public, 1 internal, 2 confidential, 3 restricted.

**`manifest.json`** tells the app how to seed itself: each file's kind and clearance, and the project's `as_of` date. It is separate from the ground truth on purpose, and a test keeps the two in agreement.

**`ground_truth.json`** is a test oracle only. It lists what was planted on purpose and what the pipeline should find: 22 expected claims with their label and exact evidence quotes, 3 conflicts, 1 stale decision, 1 missing owner, 6 fake secrets, 12 pieces of fake personal data, 2 prompt-injection attempts, and decoys that look like conflicts but are not. `backend/tests/test_demo_data.py` checks that the files and the ground truth agree.

**Safe by construction.** Emails use reserved `.example` domains, phone numbers use the invalid prefix `+91 5550x`, and key formats are invented (no real provider format). The one JWT decodes to `"note": "synthetic"`. Secret scanners will still flag some values; the repo's `.gitleaks.toml` skips this folder. If GitHub push protection flags a value, it is fake: choose "It's used in tests".
