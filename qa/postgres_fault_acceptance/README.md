# Owned Portal PostgreSQL immediate-crash recovery

Prepared driver; **actual fault has not been run by the pure guards**. Run only
after candidate freeze, with no other load and explicit root orchestration:

```powershell
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m unittest qa.postgres_fault_acceptance.tests.test_guards -v
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m unittest qa.postgres_fault_acceptance.tests.test_pool -v
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m unittest qa.postgres_fault_acceptance.tests.test_observer -v
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m unittest qa.postgres_fault_acceptance.tests.test_parent_death -v
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m qa.postgres_fault_acceptance.run --postgres-bin .runtime/cloud-readiness-postgres/binaries/pgsql/bin --deadline 600 --max-fixture-disk-mb 1024
```

The parent creates a fresh UUID directory and named Windows Job. Coordinator and
venv children must self-join through a bounded stdin gate **before any PG import
or startup**. Normal Portal Django settings/URLs/migrations/PBKDF2 and the locked
per-process pool are retained. Outer Job membership is queried through transient
open/query/finally-close handles, never retained by its descendants. A real dummy
controller-death integration verifies joined coordinator/grandchild death and
independent control survival without starting PG/HTTP. The Web startup checkout
is returned normally before serving; the process pool is kept open and numeric
ready evidence must show all current connections available and no waiters. Normal
psycopg-pool growth reserves can briefly exceed available connections: startup
observes this for at most five seconds with the wrapper checkout already None,
without borrowing/warming connections or closing the pool. Initial/final numeric
stats and actual wait time are recorded. A nested owned Job hosts normal
`config.wsgi.application` using real Waitress (four threads, 512 connections,
loopback only). It stays alive and retains its PID/creation time across PG outage.
No environment file, business DB, provider credential, Native or external service
is read or contacted; all business fixtures are synthetic.

After actual `fsync`, `full_page_writes`, `synchronous_commit=on`/primary checks,
a checkpoint is taken before the baseline real HTTP recruitment create commits.
A second real transaction inserts another business row and changes the committed
row without sending COMMIT. An independent reader confirms isolation. Before
stopping, PG owner marker, resolved cluster path, exact Popen argv, PID/creation
time, postmaster.pid data/port, real SQL data_directory and owned listener agree.
Only official `pg_ctl -D <this cluster> -m immediate stop` is invoked. Every
captured owned postgres.exe generation member must exit; the PID file must be
removed and no listener may remain. Unknown port occupation is FAIL, never killed.

A fresh hidden direct Popen reopens the **same UUID cluster/data/database/user/
port/private storage**. No initdb, database recreation, PID deletion, helper
context reuse, `pg_ctl restart`, or durability weakening occurs. The current
helper Popen/PID is updated while prior generation identities remain immutable.
Normal helper final cleanup therefore targets the new generation. Actual log
segment must show automatic recovery, redo start/finish and ready; SQL readiness
alone cannot pass. System identifier and normal durability must remain unchanged.
Committed row hash/version and private-file bytes must survive; the real
uncommitted insert/update must be absent/rolled back. Sequence gaps are legitimate
and are not incorrectly treated as transactional rollback failures.

One real HTTP request while verified PG is stopped records its controlled 5xx or
transport failure without retaining its body. Afterwards the surviving Web must
serve the original authenticated session, business read, new actual CSRF/PBKDF2
login, versioned business PATCH and confirming read. Healthy post-recovery calls
have a ten-second transport timeout and no blind retries. Fresh driver ORM and
verified numeric pool evidence are separate from the surviving Web proof.

Exclusive evidence: `.runtime/postgres-fault-acceptance/<UUID>/report.json`,
coordinator report/log, streaming phase events, Web ready/log, private PG cluster,
owner marker and server/command logs. No cookie/password/token/prompt/response body
is reported. Overall wall time, subtree count, fixture disk, startup, stop, query
connect and HTTP timeout budgets are bounded. Parent verifies the entire outer
Job is empty; nested Web and current PG cleanup, WAL proof and source stability
must all pass. Source evidence binds this new QA, actual helper/job/identity,
backend/gateway/validation/locks/deployment/frozen document assets.

Local diagnostics keep the actual Web pool object and sample numeric get_stats()
every two seconds without checkout/close. WSGI begin/end records contain only
allowlisted method, normalized fixed QA path, label, elapsed time and error class;
no cookie, query string, username, body, token or exception text. Detail UUIDs are
normalized away. start_response, yielded bytes, original exceptions and iterable
close are passed through. Each >=8s active physical request triggers at most one
CPython all-thread stack dump with code file/line/function only, no locals. JSONL
quota is 1MiB; stack target quota 16MiB, reserved space per dump and at most sixteen
dumps; quota/observer errors are sticky and fail evidence rather than silently
dropping it. The monitor stops via Event plus bounded three-second join on normal
exit; actual final Job termination verifies all Web threads die with the process.
Files: web-observer.jsonl, web-observer-summary.json, web-stacks.log. The coordinator
logs outage/healthy HTTP phase begin/end, preserves its caller in at most sixteen
safe traceback frames, and retains the summary in its report. Missing, errored or
stale (>6s at final cleanup) diagnostics fail. This does not add retries, change
the ten-second HTTP timeout, restart Web or turn controlled outage into healthy
capacity evidence.

This proves only synthetic **local Windows Portal PostgreSQL WAL crash recovery**.
It does not prove hardware power-loss durability, backup restore/PITR, Native
licensed Runtime/AG15/16/Redis, Linux/cloud capacity or topology, real-model and
formal-document quality. Older Worker reports' PG NOT_EXECUTED entries remain
unchanged. The driver changes no production or pressure acceptance thresholds.
