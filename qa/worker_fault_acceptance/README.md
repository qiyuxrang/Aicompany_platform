# Real existing Worker process fault acceptance

Run only after the candidate is frozen and other load/testing is stopped:

```powershell
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -m unittest qa.worker_fault_acceptance.tests.test_guards -v
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -m qa.worker_fault_acceptance.run --postgres-bin .runtime/cloud-readiness-postgres/binaries/pgsql/bin --deadline 1200 --phase-timeout 420 --max-fixture-disk-mb 1024
```

The real fault command is prepared, **not executed or passed by unit tests**.
Windows only: every Worker/venv child joins its own newly-created named Job via a
bounded stdin gate before Django/business imports. PID plus Windows creation time,
registration and Job membership must agree. Only that exact Job is killed. An
independent control Job, authenticated deterministic loopback gateway and fresh
UUID PostgreSQL cluster must survive every Worker crash. All subprocesses are
hidden. No environment file, operator provider credentials or existing database
is loaded. Normal settings/URLs/migrations/PBKDF2 are retained. The driver and
each real business Worker record verified numeric PostgreSQL pool configuration;
the pooled locked environment is required. HR claim/renew lease literals and the
observed real DB lease deadline/remaining time are recorded together.

Four real cases run serially: HR and product each crash before and after commit.
Before-commit death occurs during a real existing Worker HTTP model request. The
coordinator reads the genuine lease deadline and waits for it to expire without
editing lease time, timezone, hasher or production code. HR currently uses 300s;
product uses 180s. The restart is the real management command. A second controlled
HTTP barrier observes its new RUNNING/unexpired lease before proving that the old
fence cannot submit or change business results, then releases and requires real
completion. HR score must match its synthetic SQL evidence. Product is source-only
blueprint preview with exactly one revision and no document artifact.

After-commit death kills a still-live normal long-poll Worker only after DB
semantic completion is observed. A fresh `--once` process must find no work and
leave score/blueprint/version/attempt/audit side effects and ModelCallLog count
unchanged. Network calls made before a crash may be repeated after restart;
upstream calls, model charges and token counts are **not exactly-once claims**.
Crashed model-call log status may remain unknown/running and is reported honestly.

Evidence is exclusively created under `.runtime/worker-fault-acceptance/<UUID>`:
report, gate/creation-time identity markers, private Worker logs, 1s streaming
owned-process CPU/RSS/peak samples and retained private PG/storage. Reports expose
synthetic IDs, hashes, states, counts and timestamps only, never source text,
response bodies, prompts, credentials or cookies. Overall/phase deadlines, disk
budget, request body size, model wait and Worker subtree counts are bounded.
All Workers/control/gateway/PG require verified cleanup; source drift or any
failed/unknown cleanup fails the run. The minimum genuine lease waits total about
8min, plus migrations/process startup/business work. Do not run beside pressure.

This proves only the tested existing HR/product Worker mechanism using mockAI.
It is not real-model judgment, formal 50k/70k/Office quality, Native Runtime,
AG15/16 or cloud/process-supervisor production acceptance. **PostgreSQL actual
stop/restart, WAL/PITR, Redis and full-stack recovery are NOT_EXECUTED by this
entry**. PG is deliberately kept alive; its observed responsiveness is not crash
recovery evidence. Those missing dimensions remain separate, without loosening
current production or pressure gates.
