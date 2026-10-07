# A0 scoped verification evidence — 2026-09-30

This is an A0 evidence record, not the project status document or business acceptance. All content and accounts are synthetic. No real provider calls, production migration, deployment, commits, service restarts on 8100/18411, or shared `.venv` modifications were performed.

## Results and gates

- **54 tests PASS** across the five A0 modules, coordinating-agent `test_agent_execution`, and Hubble `test_agent_source_permissions`; `.runtime/tests-current-final.log`. Scoped tests without Hubble's module: **40 PASS**, `.runtime/tests-scoped-current.log`. Tests use SQLite, not a PostgreSQL production concurrency proof. The first Hubble suite attempt had 14 setup errors (`self.root` accessed before creation); Hubble fixed its own file, and the joint rerun passes. An inventory refresh also encountered an older isolated database lacking `execution_state`; a fresh uniquely named test database was created instead of modifying/resetting existing data.
- **Native development Runtime simulation PASS** for two asynchronous children, overlapping distinct synthetic model adapters, independent main work, automatic same-deployment reconciliation, two consumed/deduplicated native notifications, and one-level delegation. `native-evidence.json` contains timestamps, native IDs and cumulative counts.
- **Actual native auth HTTP negatives PASS**: no identity 401; other owner/root state and cancellation 403; same-owner other root 403; child-to-parent/sibling 403; foreign history 403; global Store 403; Studio bypass 401; revoked identity state 403. Revoked-root non-LLM child cancellation succeeds. This uses production `Auth` handlers and authenticated same-process ASGI calls, not only graph guards.
- **AG07 mechanism simulation PASS**: a synthetic committed database effect deliberately raises before graph checkpoint; native `resume(input=None)` replays and reconciles the same effect key, leaving exactly one effect. This is not the business DocumentTask/HR/finance crash acceptance.
- **Graceful native process restart PASS**: native checkpoint messages remain readable, historical start returns the original native run, no duplicate events/notices, counters `(actions, model, tool, launch) = (13,6,2,5)` stay unchanged. Evidence: `restart-evidence.json`; log `.runtime/native-current-restart-verify.log`.
- **Stable inmem 0.35.1 abrupt termination recovery FAIL**, retained as original evidence (`.runtime/native-restart-verify.log`, empty checkpoint state). Official issue 8298 matches both installed defects. Official PyPI wheel inspection shows 0.36.0rc2 still broken; 0.37.0.dev3 fixes both. `persistence-release-evidence.json` records URLs, SHA256 and source locations; no SDK patch was applied.
- **Published prerelease fix native forced-kill recovery PASS** with API 0.17.0.dev3 / inmem 0.37.0.dev3. `abrupt-restart-evidence.json` records Windows TerminateProcess of the script-owned native server, checkpoint files present before kill, historical run replay, unchanged `(13,6,2,5)` counts and deduplicated consumed notifications. Deliberately wait 12 seconds before the first run and after state reads, exercising both reported defect paths and native 10-second flush. This is periodic-flush crash recovery, NOT zero-loss immediate durability; no production approval of a prerelease is implied.
- **Native summary and large-tool offload PASS**: 51 input messages invoke the guarded model twice (summary + answer, distinct physical IDs), history is written through the guarded backend; a 140k-character tool result is actually evicted by native filesystem middleware. Revoked history/large-result reads deny. The production harness now uses native message-count (48) and token (60k) triggers with 12 messages retained, avoiding the gateway's 64-message cap without a custom loop.
- **Unknown-stop regressions PASS**: unknown child update/root resume cannot be marked cancelled from an older run's cancellation ack; unknown physical/model reservations persist. Prepared-only intents can be safely aborted without sending. All known native IDs are cancelled idempotently. Completed work allows root question/read only, not writes/domain launch/new children; old requirement child events cannot wake the new requirement. Counters/deadline/fence do not reset.
- **Full AG15 and AG16: NOT PASSED; real-data gate remains closed.** Source-specific `check_sources(root)` is integrated into runtime authorization and stop persistence, but full source/domain recovery and late-result cleanup acceptance remains cross-agent work. All domain-worker physical calls/derived tasks, native PostgreSQL durability and deployment crash guarantees remain unverified. A synthetic graph is not business wiring or formal-deliverable acceptance.
- Real-model overlap (two genuinely different authorized models) **NOT EXECUTED**. Isolated PG/Redis is authorized and was actually attempted, not blocked for missing infrastructure permission. Official native PG server fails license validation without an authorized development key or enterprise/offline entitlement; see `native-pg-preflight-evidence.json`. External credential validation/telemetry authorization remains a separate gate.

## Verified environment

Windows; Python 3.13.9; `.runtime/agent-platform-sdk-venv` (repository-relative). Locked versions: Deep Agents 0.7.19, LangChain 1.4.2, core 1.6.6, LangGraph 1.2.12, SDK 0.4.5, Agent Server 0.17.0.dev3, native runtime-inmem 0.37.0.dev3, CLI 0.4.32, Django 5.2.17. `sdk-evidence.json` records installed signatures/versions and actual tool inventories. `pip check`: no broken requirements. Root config validates with native CLI schema. No project Docker build or production deployment was run.

Native PG preflight: Docker Server29.3.1, official image `langchain/langgraph-api:0.15.1-py3.13`, digest `sha256:56f8b6d8f31b37830b36de14b4fd7cbee6b4273393baaf9d6ed19aebe7422025`. Exclusive randomly named Postgres16 / Redis6 containers, an internal network, no host ports, no repository mounts, no secrets, no beacon access. The native runtime applied migrations only to its new disposable database, then exited3: `License verification failed`. All created resources removed. The initial preflight summary string matcher missed this wording (`INSPECT_NATIVE_LOGS`); it was corrected and a fresh preflight reproduces `BLOCKED_LICENSE`. No existing container, port6379, or production data was used.

Minimum next authorization for native PG: provide an existing authorized development `LANGSMITH_API_KEY` with LangGraph Cloud access and permit its configured validation endpoint, or provide a valid enterprise entitlement (with required beacon verification/reporting) / offline entitlement. We neither obtained credentials nor disabled license verification. The official standalone and egress docs identify these alternatives; purchase is not assumed necessary.

UV sync initially failed downloading an OpenTelemetry wheel after retries. Lock export + pip into the separate SDK environment succeeded. The earlier exploratory `qa/agent_platform/.venv` remains separate. No cleanup of shared or other agents' files was performed.

The only test server binds **127.0.0.1:18743**; synthetic server audit hook rejects non-loopback socket connects. It was shut down gracefully after evidence collection. Native dev logs explicitly report no license/control-plane key and skip the metadata loop. Pydantic `ToolRuntime` context serialization warnings remain visible in logs; they are not hidden or claimed fixed.

## Commands (PowerShell, repository root)

Dependency setup used:

```powershell
uv export --locked --group agent-runtime --no-hashes --no-emit-project --format requirements-txt --output-file qa/agent_platform/.runtime/locked-requirements.txt
qa/agent_platform/.venv/Scripts/python.exe -m pip --python .runtime/agent-platform-sdk-venv/Scripts/python.exe install -r qa/agent_platform/.runtime/locked-requirements.txt
qa/agent_platform/.venv/Scripts/python.exe -m pip --python .runtime/agent-platform-sdk-venv/Scripts/python.exe check
```

Common isolated environment (use in both server and verification terminals):

```powershell
$env:PYTHONPATH="$PWD/backend;$PWD"
$env:DJANGO_SETTINGS_MODULE='qa.agent_platform.test_settings'
$env:PYTHONUTF8='1'
$env:A0_DATABASE_NAME='native-v4.sqlite3'
$env:A0_SERVICE_TOKEN='a0-synthetic-loopback-service-token-never-production'
$env:A0_AUTO_RECONCILE='1'
$python='.runtime/agent-platform-sdk-venv/Scripts/python.exe'
```

Tests and inventory:

```powershell
& $python backend/manage.py test portal.tests.test_agent_runtime portal.tests.test_agent_termination portal.tests.test_agent_harness portal.tests.test_agent_isolation portal.tests.test_agent_model portal.tests.test_agent_execution portal.tests.test_agent_source_permissions --noinput --keepdb
& $python qa/agent_platform/inspect_sdk.py
```

Additional self-contained reproductions (automatically create/stop only their own isolated services):

```powershell
& $python qa/agent_platform/inspect_persistence_release.py
& $python qa/agent_platform/verify_abrupt_restart.py
docker pull langchain/langgraph-api:0.15.1-py3.13
& $python qa/agent_platform/verify_pg_preflight.py
```

The forced-kill script selects its own fresh SQLite filename/runtime directory and verifies port18743 is free. It leaves evidence, never resets an existing root. PG preflight returns a blocked result rather than claiming Harness acceptance.

Native mechanism reproduction (use a new safe SQLite filename for a clean run; tests reject paths outside the isolated directory):

```powershell
& $python backend/manage.py migrate --run-syncdb --noinput
& $python qa/agent_platform/native_server.py 18743
```

The migration command above is **only for the isolated test database**. Test settings disable portal migrations and synchronize the current parallel-working models; it is not verification of the production migration chain.

In another terminal with the same common environment:

```powershell
& $python qa/agent_platform/verify_native.py
& $python -c "import os,httpx; response=httpx.post('http://127.0.0.1:18743/a0/shutdown',headers={'Authorization':'Bearer '+os.environ['A0_SERVICE_TOKEN']},trust_env=False); response.raise_for_status()"
```

Wait for server exit, then rerun the same `native_server.py 18743` command and promptly run `verify_restart.py` before the original five-minute root deadline. Do not extend deadlines or reset counters to pass. Afterwards use the same `/a0/shutdown` command. The shutdown route exists only in the isolated QA app, never in production `runtime_app`.

## Tool/storage/model boundary

Actual product work main inventory has 19 tools: `begin_work`, native async `start_async_task/check_async_task/update_async_task/cancel_async_task/list_async_tasks`, filesystem `ls/read_file/write_file/edit_file/glob/grep`, and A2 `product_create_task/product_read_task/product_read_source/product_search_sources/product_queue_blueprint/product_queue_outputs/product_list_outputs`. Child work inventory has 13: no async delegation and no `begin_work`. Before work creation the deployed model sees the thin `begin_work` tool instead of business tools; tests prove the same graph exposes authorized tools after work creation without resetting counts. Other department/business tools belong to their agents' acceptance, not this inventory.

`execute`, synchronous/general-purpose `task`, and delete are disabled. Native StoreBackend only: no host filesystem, shell, or link traversal. Namespaces are bound to owner/root/run; shared skill bundles are server-published from A1's reviewed catalog, installed-digest-bound and read-only. Internal upload/download/offload and native skill discovery use the same guarded backend. Native Store HTTP APIs are deny-all; checkpoint/thread APIs require current root identity. Changing the installed skill bundle invalidates the existing root before model/restore reads.

Every gateway adapter attempt gets its own root admission and physical UUID. A1 `ModelCallLog` scope and unique physical ID are saved atomically before HTTP; direct unadmitted or duplicate IDs are rejected. Unknown outcomes retain both log and active reservation. Model cache is disabled; protocol/authorization are checked before and after transport. Gateway protocol end-to-end acceptance remains the coordinating agent's responsibility.

## Official primary sources and installed API checks

- https://docs.langchain.com/oss/python/deepagents/async-subagents
- https://docs.langchain.com/oss/python/deepagents/customization
- https://docs.langchain.com/oss/python/deepagents/permissions
- https://reference.langchain.com/python/langgraph-sdk/auth/Auth
- https://docs.langchain.com/langsmith/custom-lifespan
- https://docs.langchain.com/langsmith/local-dev-testing
- https://docs.langchain.com/langsmith/deploy-standalone-server
- https://docs.langchain.com/langsmith/self-host-egress
- https://github.com/langchain-ai/langgraph/issues/8298
- https://pypi.org/project/langgraph-runtime-inmem/0.37.0.dev3/

Web search/open was used, then actual installed public SDK signatures/source were inspected. Important version-specific finding: default SDK `get_client(url=None)` creates ASGI transport with `/noauth`; A0 instead builds the public `LangGraphClient` on HTTPX ASGI transport without this bypass, preserving native authentication even inside the same deployment. Runs-list filtering is not guessed: SDK supports `thread_id`, `limit`, `offset`, `status`, `select`, not an operation-key parameter; A0 scans a bounded list and matches operation metadata itself.
