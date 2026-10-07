# Existing business Worker queue acceptance

Runs an owned UUID portable PostgreSQL cluster, normal latest Django migrations,
private synthetic source storage and actual existing HR/product worker commands.
All model requests use the real `generate_for_use` authorization, ModelCallLog and
HTTP gateway path, terminating at an authenticated deterministic loopback fixture.
It never forwards any request to a provider and never calls a paid model. Mock
Token values stay unknown. No environment file or existing business DB is loaded.

```powershell
python -m unittest qa.worker_acceptance.tests.test_boundaries
python -m qa.worker_acceptance.run --postgres-bin <pgsql/bin> --hr-tasks 10 --hr-concurrency 2 --product-tasks 2
```

The HR production command allows only one or two executing tasks; this runner
preserves that contract. Ten queued real resumes **does not mean ten executing
HR tasks**. Resume source reading, extraction, evidence parsing, matching, scoring,
leases, fences and batch completion run through the real existing worker. Product
tasks execute real `run_product_worker --once`/LangGraph domain workflow through
source-only manual-input blueprint preview; no formal chapter/document rendering
or short text substitution for 50k/70k quality is performed. Progress remains in
the existing Agent platform status file.

Acceptance includes:

- real PostgreSQL expired HR lease reclaim and old-fence finish rejection;
- actual HR parallel worker HTTP calls (a disclosed mock network synchronization
  barrier makes overlap observable without fake tasks or worker sleeps);
- a controlled gateway HTTP504, real failed HR state and authorized retry API;
- complete queue draining and semantic HR scores, exact blueprint count;
- empty worker polls causing no duplicate ModelCallLog or revision side effects;
- actual product cancellation API while its worker awaits a gateway response,
  followed by rejection of the late blueprint commit;
- queue/executing/completion/resource samples, successful task throughput,
  preparation vs useful worker vs total cleanup durations;
- source-hash stability, bounded task counts/time/disk, UUID evidence and verified
  owned gateway/PG shutdown. Failed/blocked/redline states exit nonzero.

Reports live under `.runtime/worker-acceptance/<UUID>/report.json` with exclusive
creation and retained private cluster/files. Reports omit source text, prompts,
passwords, bearer tokens, cookies and private payloads. Failure frames contain
filenames/function names/line numbers, not traceback source lines or local values.
Source hashes bind evidence to the actual uncommitted files. Normal passwords and
PG authentication are retained. No other services are stopped or reconfigured.

This is mockAI business-worker mechanism evidence, **not** actual-model quality,
native Runtime/AG-15/16, Linux cloud capacity, formal Office rendering, machine
crash/PG restart or complete backup/restore evidence. Lease expiry is intentionally
injected into this fixture's own PG rows; the HTTP504 is a simulated upstream
timeout, not a real network timeout. Worker commands run in threads of the owned
Python process, not as production supervisor services. The existing platform
worker concurrency caps are not raised to manufacture capacity.
