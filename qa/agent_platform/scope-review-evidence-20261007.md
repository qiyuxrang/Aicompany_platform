# Agent Scope Review Evidence — 2026-10-07

## Scope and instructions

- Read the user-provided review instructions, global `$CODEX_HOME/AGENTS.md`, and `docs/SPEC.md` section 9 (lines 197–211). No repository-root `AGENTS.md` was present; the only discovered repository copy is under a vendored `node_modules` tree and is outside this review.
- Read the three implementation paths, their callers, and the related finance, management API, and signed-source tests. Changes are limited to this evidence file and `backend/portal/tests/test_agent_scope_review.py`.
- One integrity counterexample was found in the GM business-reference resolver. Finance completion and signed-source revocation checks held in the inspected paths and targeted cases. This is scoped evidence, not a claim that all AGENTS or platform gates pass.

## Findings by chain

### Finance completion

- `backend/portal/agent_finance.py:14` reconciles only running or confirmation-waiting finance work; `:24–27` calls `lock_scope` with the work's current requirement version and the root's owner/grant/session/fence identity; `:29–34` selects references for the locked root, work, and current root requirement.
- `backend/portal/agent_finance.py:37–53` checks each referenced draft revision, author, draft actor, row, and checksum. `:54–68` requires an owner's published revision newer than the drafts and matching exact record metadata and row checksums before completion at `:69–75`.
- Existing `backend/portal/tests/test_agent_finance_completion.py:65–137` covers draft-not-delivery, exact self-publish, all referenced drafts, another employee, idempotence, cancelled work, old requirement, and revoked grant. These cases passed.

### Management history and read-only boundary

- `backend/portal/agent_api.py:211–223` only serializes the manager reference allowlist; `:605–630` lists business work fields and typed references without goals or messages.
- `backend/portal/agent_management.py:35–51` validates reference/work/root owner and conversation consistency plus the user-message/requirement linkage. `:186–224` resolves an explicit read/download allowlist. `:206–228` exposes GET endpoints only; manager authorization is checked before resolving or returning details.
- New `backend/portal/tests/test_agent_scope_review.py:33–104` creates an old completed Work and a newer Work on the same root, attaches a published reference to the old one and a private-only reference to the new one, then verifies manager listing/detail contain only the old allowed reference and no goals, message text, or backend marker. It also checks an ordinary employee receives 403 and a write-method request receives 405.
- **Counterexample:** `backend/portal/agent_management.py:167–184` checks the revision's own checksum against its records, but does not compare `reference.digest` with `revision.checksum`. A `business_revision` reference with the valid owner's published revision ID and revision number but a mismatched digest still returns HTTP 200 and the published data. The new assertion at `backend/portal/tests/test_agent_scope_review.py:89–97` expects 409 and fails with `AssertionError: 200 != 409`; the response includes the published `records` and actual checksum.
- **Minimal fix:** in `_business_revision`, reject a mismatch between `reference.digest` and `revision.checksum` before returning the payload; keep the negative assertion as the regression check. Production code was not changed.
- Existing `backend/portal/tests/test_agent_api.py:127–150` verifies typed manager links; `backend/portal/tests/test_agent_read_sources.py:251–295` verifies reference allowlisting and role revocation. These cases passed.

### Signed read-source revocation

- `backend/portal/agent_read_sources.py:149–178` reloads the persisted root, validates each source shape and root-bound signature, and rejects duplicate source keys. `:181–212` locks the root and starts from all existing signed sources before adding new ones; it does not replace earlier entries. `:439–453` rechecks every registered pointer, failing closed on authorization changes.
- Existing `backend/portal/tests/test_agent_read_sources.py:214–232` proves a newer JD registration does not mask deletion of an older JD. New `backend/portal/tests/test_agent_scope_review.py:108–141` repeats the sequence across Work versions on the same root: register old HR source, switch the root to newer Work, register new JD, verify both signed pointers remain, then delete the old JD and require `source_authorization_changed`. It passed in the initial focused run.

## Verification

- Runtime: `.runtime/main-acceptance-venv/Scripts/python.exe`; `DJANGO_SETTINGS_MODULE=config.settings`; `PORTAL_DEBUG=1`; a new UUID path was assigned to `PORTAL_SQLITE_PATH` (`.runtime/scope-review-20261007-94062d7ce5e54c09be55abe0108df859.sqlite3`). Existing `PORTAL_DB_*` settings were removed from the process environment. Model and tracing credentials were blank; LangChain/LangSmith tracing and OpenTelemetry were disabled. No service or production database was used.
- Working directory: `backend/`.
- Command: `.runtime/main-acceptance-venv/Scripts/python.exe -m django test portal.tests.test_agent_scope_review portal.tests.test_agent_finance_completion portal.tests.test_agent_read_sources portal.tests.test_agent_api --settings=config.settings --verbosity 2`
- Baseline before adding the digest-mismatch assertion: exit code `0`; 31 tests ran in 41.833 seconds; `OK`; Django system check reported no issues. SQLite's test runner used its isolated in-memory test database.
- Counterexample command: `.runtime/main-acceptance-venv/Scripts/python.exe -m django test portal.tests.test_agent_scope_review.AgentScopeReviewTests.test_manager_history_stays_read_only_when_a_new_work_reuses_the_root --settings=config.settings --verbosity 2`, from `backend`, with a fresh `PORTAL_SQLITE_PATH` and the same model/tracing-disabled environment.
- Counterexample SQLite: `.runtime/scope-review-counterexample-20261007-a844736bf043440fb9b8345d6bd01a33.sqlite3` was new and absent before the run; Django used its isolated in-memory test database.
- Counterexample result: exit code `1`; 1 test ran and failed at `backend/portal/tests/test_agent_scope_review.py:97`, observing HTTP `200` and a response containing published records where the reference-digest integrity check requires `409`. System check reported no issues. This regression test intentionally remains red until the production resolver is fixed.
- The review worker fork inherited the active turn and duplicated its focused test run in the same repository directory; it was stopped after reporting the digest mismatch. Both Django test runs used isolated in-memory databases. No production implementation, dependency, service, or production/shared database was changed. The broader SQLite suite was not run as part of this review.
