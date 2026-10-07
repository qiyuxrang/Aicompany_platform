# Release HTTP pressure acceptance

This is an executable **real HTTP** baseline against Django's real session, CSRF,
authorization and ledger services, served by Waitress. It is not a recorder
microbenchmark and does not constitute a production cloud or complete Agent pass.
Progress remains in the existing Agent platform status file; this directory only
contains tooling and repeatable evidence instructions.

Use the existing locked project Python with Django, Waitress and httpx installed.
No load-testing platform, environment file, actual business data or provider is
loaded. Default runs create an owned UUID directory, SQLite database, synthetic
accounts/records and loopback server on a dynamic port. The runner stops only its
own child process and retains the isolated database and evidence for review.
It never deletes an existing database or overwrites evidence.

```powershell
python -m unittest qa.release_acceptance.tests.test_acceptance qa.release_acceptance.tests.test_long_run
python -m qa.release_acceptance.run --mode target --concurrency 8 --duration 30
python -m qa.release_acceptance.run --mode step --concurrency 16 --duration 60
python -m qa.release_acceptance.run --mode burst --concurrency 32 --duration 60 --burst-duration 15
python -m qa.release_acceptance.run --mode soak --concurrency 16 --duration 86400 --fault-mode timeout --max-memory-mb 512 --max-fixture-disk-mb 1024 --max-queue 128
```

Each operation and each phase must have at least `--min-samples` successful semantic samples
(default 20). Short runs or very low target rates fail rather than manufacture a
PASS. `--target-rps` paces the total logical operation arrivals across concurrent
users; zero means closed-loop unpaced load. HTTP requests per operation vary:
ledger writes first read the revision and then PATCH, so logical operation rate
is explicitly different from HTTP RPS. Client scheduling delays show generator
lag. This is a bounded concurrency baseline, not an open-loop arrival generator.
Step mode runs target then 2x target concurrency; burst runs target, 5x target,
then target recovery. A nonzero paced logical operation rate scales by the same
2x/5x multipliers. Each paced phase must deliver at least 95% of its offered rate
or fail; concurrency alone never claims a target RPS. Unpaced scheduling delay
is zero by construction and does not prove generator headroom. Peak phase
concurrency provisions the synthetic users (at most 1000). Default Waitress
threads are 4, matching the current Compose/local configuration; hold-only
synthetic admission defaults to 2. These can be explicitly overridden.
Waitress's socket-map connection limit is explicitly 512 (`--connection-limit`),
distinct from worker threads and active business requests. Validation reserves
peak users + a denial pool bounded to the thread count + 4 probe/control slots;
500 users with 4 threads require 508 slots. Windows uses `select` without poll
and rejects limits above 512; supported non-Windows fixtures explicitly use poll.
Actual Waitress limit/poll settings appear in identity and each resource sample,
alongside socket-map size. Missing or mismatched configuration evidence fails.
These are synthetic budgets; target-cloud file-descriptor and queue capacity
still require deployment evidence.

The mix rotates through authenticated session reads, fixture finance reads,
presales metadata writes with optimistic revision conflicts, a separately logged
in HR account's finance denial, and disabled Agent API's explicit unavailable
503. Successful writes must advance exactly the expected revision; at least
`--min-samples` successful writes per phase are mandatory, so mostly-conflict
runs with insufficient useful writes fail. Successful write P95 is checked
separately so fast conflicts cannot conceal slow successful mutations. Unexpected session
401/CSRF403/429 are errors. Only the operation's exact expected denial, conflict,
or disabled response is counted separately. JSON failed/error/unavailable HTTP
200, transport errors, all timeouts, zero load and insufficient samples fail.

Candidate defaults are ordinary read P95 <= 1000 ms, write P95 <= 2000 ms,
unexpected errors <= 0.1%. These are **not frozen capacity commitments**. Options
can tighten thresholds and specify successful throughput, process memory and
Waitress queue limits. P50/P95/P99, status/result categories, success throughput,
process memory/CPU/thread samples and Waitress queue depth are retained. Expected
errors do not become successful throughput. Resource and queue evidence is
required; external runs without it fail. Baseline measures the Web process and
Waitress task queue, not HR/product/Agent worker queues or whole-machine resource
use. SQLite runs are explicitly **not production PostgreSQL evidence**.
Paced `20 logical RPS` includes expected permission denials, disabled-Agent
responses and ledger conflicts; useful successful operations per second are
separately reported. Complete five-minute business windows independently enforce
the same latency/error/success-sample criteria and at least 95% of offered logical
rate. Empty windows fail. A short trailing window is labeled PARTIAL and still
checks auth, timeout, error rate and latency; whole-phase sample/rate gates remain.
Observed authentication errors, failed200 and timeouts stop new business load
immediately; they are never repaired by a login retry.

Long runs aggregate latency into conservative 1ms histograms rather than retain
millions of samples in memory; quantiles are upper-rounded with <=1ms precision.
Histograms retain at most 60001 buckets. Values at/above 60s share an overflow
bucket; any percentile landing there reports the actual maximum as a conservative
upper bound, not a truncated 60s value. Client schedule delays use the same bound.
The owned fixture's disk usage is scanned by a separate hidden owned subprocess
every 5 seconds (`--disk-interval` permits 5 to 10). The Web resource sampler
only reads its atomic numeric cache, avoiding recursive filesystem traversal in
the Web process/GIL. Each sample records measurement wall time, scan duration,
file count and validity; reports retain maximum scan duration and cache age.
Missing, failed or stale capacity evidence fails and stops load; default maximum
age is 15 seconds. Scan overhead remains host I/O/CPU overhead and is measured,
not removed from the host. A candidate 1024MiB
fixture disk safety guard stops new workload and fails the run if exceeded;
`--max-fixture-disk-mb` can be explicitly adjusted (zero disables it). Configured
process-memory and queue limits also stop load as they are observed, not only
after the run. These are candidate fixture safeguards, not production capacity
standards. Remote resource files must include fresh wall-clock/queue/CPU/memory
and fixture-disk measurements; stale or missing telemetry cannot PASS.
Resource JSONL is read as a stream. Reports retain the final 4096 numeric Web
snapshots plus first/last, all-run maxima/validation and bounded five-minute
segments (512 closed segments plus the current one). No full-day resource file
is loaded into memory. Generator CPU/RSS is sampled separately and explicitly
labeled as the client process; it never substitutes for Web, PG, host or worker
metrics. Linux reads current RSS from `/proc/self/statm`; where unavailable,
observed peak RSS remains a clearly recorded fallback. Safety memory caps use
the greater current/peak value, so a high peak is not hidden by later recovery.

Soak requires explicit positive memory/disk/queue caps. The declared local
synthetic candidates are Web current/peak memory 512MiB, total owned fixture disk
1024MiB and Waitress pending queue 128. They are diagnostic safeguards and
are **not frozen target-cloud capacity**. After a 15-minute warmup, memory growth
is evaluated over actual non-overlapping observations of at least one hour and
30 samples. Observed OLS growth over 32MiB/hour fails and stops load. Current RSS
is preferred for growth; a peak fallback and actual metric/window are recorded.
Short samples report NOT_ENOUGH_OBSERVATION rather than diagnosing a memory leak.
Soak lasting at least warmup + one-hour window requires a completed observation.

Overall and per-phase fixture disk growth use actual scan wall times; 24h linear
projections are diagnostic only and do not grant PASS. The previous synthetic
100-session/20-logical-RPS/180s baseline `a038f6a7fed949f084657d2cf2926c84` grew
1,205,345 bytes in 188.150 observed scan seconds (~6406 bytes/s), corresponding
to ~528MiB additional disk per 24h under a short linear projection. PG checkpoint,
WAL, revision history and sampling/log growth may change that rate. No 2h or 24h
execution is claimed by that projection; the 1GiB guard remains unchanged.

A separate authenticated `/__release__/hold` probe checks this **QA-only synthetic
admission gate** responds with retryable429 and subsequently serves a normal
session. Only the hold probe acquires the synthetic semaphore; ordinary business
routes use the actual Waitress queue and database connection limits. It is
deliberately excluded from ordinary business latency/error
statistics. It does not prove production overload policy. `--fault-mode timeout`
injects a slow synthetic HTTP request, expects client timeout and checks recovery;
it does not prove process crash, database restart or Runtime recovery.
After all business phases finish, their clients/connections are closed. The
independent hold probe offers exactly twice the Waitress thread count, then a
new session performs a real password login and business read for recovery.
Its login and probe latency are separately reported and never count as measured
business samples or evidence of production admission under the full session load.

## Isolated PostgreSQL and explicit external fixtures

For owned PostgreSQL, precreate a **fresh empty** database named
`release_acceptance_<UUIDhex>` in an owned isolated instance. Explicitly provide
its DSN through `RELEASE_ACCEPTANCE_PG_DSN` and run:

```powershell
python -m qa.release_acceptance.run --fixture-id <UUID> --postgres-dsn-env RELEASE_ACCEPTANCE_PG_DSN --duration 60
```

Alternatively, reuse the repository's portable PostgreSQL context and let it
create its own UUID cluster and fresh empty database on a dynamic loopback port:

```powershell
python -m qa.release_acceptance.run --postgres-bin <directory-containing-initdb-pg_ctl-psql> --duration 60
```

The pressure fixture owns migrations/seeding; the portable context owns only its
cluster lifecycle. Each pressure run has an independent cluster and port.

The server verifies its exact UUID database name and refuses a nonempty public
schema before migrating. It never infers PostgreSQL from PORTAL_DB variables or
local.env. Credentials stay out of reports and stdout. Server fixture manifests
report database kind. This remains a synthetic isolated PG run, not production
infrastructure verification. The PostgreSQL owner's normal TLS/network policy
must be applied to its connection; this entry point does not claim to test it.

For a remote synthetic fixture, provision the same fixture server in its own
UUID directory with explicit `--allow-external-bind --host <address> --port <port>`
and ephemeral `RELEASE_FIXTURE_PASSWORD`, `RELEASE_FIXTURE_TOKEN`,
`RELEASE_FIXTURE_SECRET` process variables. A restricted caller-provided JSON file
contains `password`, `token` and the exact twelve-field `identity` from ready.json
(exclude port/owned_pid/parent_pid). Run with **all three** explicit external arguments:

```powershell
python -m qa.release_acceptance.run --target https://synthetic.example --allow-external-synthetic-target --external-fixture <restricted-fixture.json> --resource-file <sanitized-resources.jsonl> --duration 60
```

Remote credentials/identity are never printed. Target identity must confirm
synthetic data, disabled Agent and no model calls, matching users/rows, before any
business load. The runner does not provision or stop remote services. The
external resource file must be updated/copied by the fixture owner, contains only
numeric measurements, and must cover the run; target resources cannot be inferred
from client-side CPU. Existing business/cloud targets without this fixture
identity are rejected. Use secure private networking/TLS for exposed fixtures.
The external fixture owner must also launch `python -m
qa.release_acceptance.disk_monitor --run-dir <owned-UUID-directory> --fixture-id
<UUID> --interval 5` with a private `RELEASE_DISK_STOP_TOKEN` environment variable.
It only accepts an existing UUID directory beneath this checkout's
`.runtime/release-acceptance`; stop it by exclusively creating that directory's
`disk-monitor.stop` containing the same private token. The external runner does
not start or stop either remote process.

## Evidence and remaining scope

Reports go to `.runtime/release-acceptance/<UUID>/report.json`; the directory and
files use exclusive creation. They contain environment, commit and source hashes,
candidate workload/thresholds, individual phase summaries, resource samples,
overload/fault results and coverage limits. Source changes during a run fail it.
Preparation (PG/server/fixture/login), effective workload, post-workload probes
and total time including cleanup are reported separately. The effective workload
duration includes finishing in-flight operations at the phase boundaries.
Identity and serial session login preparation has a separate 30-second per
request timeout and 600-second deadline (`--setup-timeout`, `--setup-deadline`).
Measured requests retain the default 10-second timeout. Numeric stage duration,
status, error class and completed-login counts are retained even when preparation
fails; a setup timeout never becomes a business load result. Standard password
hash verification is retained and login is not part of the five measured operations.
Scheduled session renewal defaults to 4h (`--renewal-interval 14400`), strictly
before the actual `session_cookie_age` advertised by the fixture (currently 8h).
It first verifies the existing session, then calls the real password login route
and synchronizes the rotated CSRF token. A per-client read/exclusive gate allows
normal requests concurrently but prevents renewal from racing that client's
in-flight requests. Early 401/403, expiry missed by the scheduler, verification
failure, login/CSRF failure or interrupted renewal fails separately and never
triggers a hidden business retry. Renewal timestamps, counts, elapsed time and
safe HTTP stage evidence are separate from business latency metrics. Production
expiry, password hashing and session-version policies are not changed.

Repeatable isolated PostgreSQL candidate soak commands (run one at a time):

```powershell
.runtime/cloud-readiness-patched-venv/Scripts/python.exe -m qa.release_acceptance.run --postgres-bin .runtime/cloud-readiness-postgres/binaries/pgsql/bin --mode soak --duration 7200 --concurrency 100 --target-rps 20 --threads 4 --connection-limit 512 --max-memory-mb 512 --max-fixture-disk-mb 1024 --max-queue 128 --renewal-interval 14400 --health-window-seconds 300 --memory-warmup-seconds 900 --memory-growth-window-seconds 3600 --max-memory-growth-mib-hour 32 --fault-mode timeout
.runtime/cloud-readiness-patched-venv/Scripts/python.exe -m qa.release_acceptance.run --postgres-bin .runtime/cloud-readiness-postgres/binaries/pgsql/bin --mode soak --duration 86400 --concurrency 100 --target-rps 20 --threads 4 --connection-limit 512 --max-memory-mb 512 --max-fixture-disk-mb 1024 --max-queue 128 --renewal-interval 14400 --health-window-seconds 300 --memory-warmup-seconds 900 --memory-growth-window-seconds 3600 --max-memory-growth-mib-hour 32 --fault-mode timeout
```

These commands are prepared entry points, not evidence that either duration has
passed. The 2h run does not reach a scheduled 4h renewal; short clock/HTTP tests
cover its boundary logic and the actual 24h run must supply renewal evidence.
Server migration, seeding and normal password hashing have a separately bounded
600-second readiness deadline (`--startup-deadline`, allowed 10 to 1800 seconds)
to prepare larger synthetic populations. Deadline and actual readiness duration
are reported on success or setup failure. No fast test password hasher is used.
The source manifest binds all backend Python, this acceptance directory,
the actual PortablePostgres/verify_portal_pg dependencies, `uv.lock` and
`pyproject.toml`, pruning `node_modules`, `__pycache__`, `.runtime` and `.venv`
before recursive traversal. Renderer installed assets and compiled manifests
have separate security/asset acceptance; this pressure source check does not
certify them. Independent browser and worker QA that this runner does not call
are excluded; changes to business source or actual dependencies still fail.
No response body, prompt, cookie, token, password or real business record is
included in the report. Owned-server diagnostic logs and synthetic SQLite data
remain private ignored local evidence; don't upload them with the code.

This baseline has **no real or mock model execution** and no asynchronous HR,
product or Agent task load. The proposed 100 concurrent sessions/10 asynchronous
business tasks requires a separately frozen workload and worker/Runtime telemetry;
passing this baseline cannot certify it. Real paid-model quality, AG-15/16,
production license, worker queue saturation, faults involving process/PG/Redis,
consistent full-stack backup/restore, TLS/security and target-cloud deployment
remain separate acceptance requirements. All missing/blocked scope stays explicit.
