# One-shot local synthetic HTTP pipeline

This Windows-only supervisor sequentially runs the real existing pressure runner
in fresh, explicit UUID PostgreSQL fixtures. It does not start itself on a
schedule, resume a partial phase, change thresholds, or certify production/cloud
capacity. `production_ready` is always false. Native license, AG-15/16, real AI,
HR/product workers, Linux Office and whole-host resources remain separate gates.

Before starting, provide explicit **current** backend and browser reports. The
backend must pass at least 1000 tests with both PostgreSQL root guards, no failures
or errors, verified cleanup, and unchanged/current source. Explicit documented
skips remain skips. The browser must pass its existing full semantic/artifact
gate, current source and process-tree/PG cleanup. A historical PASS against old
source is refused. Pipeline source binds all backend Python and document assets,
pressure/pipeline/browser Python including the named-job API, locks and actual PG
helpers and deployment/build inputs. Source changes stop this candidate; freeze before starting.
The actual PostgreSQL pool identity must match the installed locked version and
Portal bounds: min1/max4, checkout timeout5s, max_waiting16, health checks enabled,
CONN_MAX_AGE=0. Request cleanup returns checkouts to this per-process pool.

```powershell
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m unittest qa.release_pipeline.tests.test_contracts qa.release_pipeline.tests.test_job
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m qa.release_pipeline.start --backend-report <explicit-current-PG-report.json> --browser-report <explicit-current-browser-report.json> --postgres-bin .runtime/cloud-readiness-postgres/binaries/pgsql/bin
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m qa.release_pipeline.status --run-dir .runtime/release-pipeline/<returned-UUID>
.runtime/cloud-readiness-pooled-venv/Scripts/python.exe -B -m qa.release_pipeline.status --run-dir .runtime/release-pipeline/<returned-UUID> --request-abort
```

`start` launches a hidden independent supervisor and returns its status path; it
does not hold the chat tool open for 26 hours. A stdin gate establishes identity
before any phase work. A second launch commit is delivered only after the actual
supervisor PID/creation time/parent identity is registered and checked; losing
the launching parent before commit cannot initialize a pressure fixture.
For each phase, the supervisor creates an exclusive named
Windows Job with `KILL_ON_JOB_CLOSE`. The worker actively joins this exact Job
**before** importing the pressure runner, including a venv launcher child race.
PostgreSQL/server/disk-observer descendants are inherited Job members. Abrupt
supervisor death closes its sole Job handle and kills only that owned tree.
Normal phase completion additionally requires the pressure runner's verified
Web/observer/PG cleanup and verified empty Job. There is no PID/name scan and no
termination by port. Machine logout/reboot can abort a run; elapsed work is not
resumed or relabeled PASS. Source/evidence directories are retained.
A workspace-scoped Windows mutex refuses concurrent pipeline supervisors and
releases automatically on process death; no stale PID lock is removed.

The fixed sequence is:

| Phase | Workload |
| --- | --- |
| Renewal preflight | 8 clients, 4 logical ops/s, 180s; real renewal interval 60s; each of 8 + denied clients renews at least once |
| Step | 100/20 then 200/40, each 180s |
| Burst | 100/20 180s, 500/100 60s, 100/20 recovery 180s |
| 2h soak | 100/20, 7200s |
| 24h soak | 100/20, 86400s; all 100 + denied clients have at least 5 real renewals with per-client actual time/coverage, no early loss/missed renewal |

Numbers denote clients / **logical** ops/s, not HTTP RPS. The denied client is
separate. Thresholds stay at read P95 1000ms/write P95 2000ms, unexpected error
rate at most 0.1%, offered delivery at least 95%, 512MiB Web memory, 1GiB entire
owned fixture disk, queue 128, 4 threads and connection limit 512. Complete 300s
windows independently enforce the pressure runner's semantic/SLO gates. Memory
growth requires warmup 900s followed by at least 3600s/30 samples, max 32MiB/h.
Renewal is 4h except preflight. Each phase must really elapse, exit zero, have a
PASS report with exact UUID/parameters/current source and all cleanup verified.
Missing report, auth/timeout/window failure, source drift, cleanup uncertainty,
nonzero exit or explicit abort stops the sequence. No later phase is launched.
At the final workload boundary no new renewal round may start. An already active
real login is drained for at most 60s; drain time is recorded separately and never
added to measured business time. Missing/failed drain, post-boundary new rounds,
or renewal failures reject the phase.
The process pool keeps min 1/max 4, a 5s checkout limit and at most 16 waiters.
Each new PostgreSQL connection also has a fixed 3s connect deadline, verified
from both Django settings and the actual pool parameters. The checkout deadline
alone does not bound background connection establishment after a DB outage.
Step/burst phases are shorter than 300s: whole-phase gates apply without claiming
a complete five-minute window. The 2h soak does not reach a 4h renewal.

Atomic `status.json` is updated at least every 30s during each active worker and
retains only bounded allowlisted numeric resource tails. Business window and
renewal detailed evidence remains runner-owned and is validated at exit. No
unelapsed window is displayed as PASS. The status command checks PID **and**
creation time: a dead supervisor with persisted RUNNING displays ABORTED, never
PASS. `owner.json` records actual supervisor identity; launcher and worker identity,
command hashes, phase UUID and own Job identity remain private ignored evidence.
Only UUID/result/paths are emitted from launch/final stdout. Reports/logs live
under ignored `.runtime`; no password/token/cookie or response body is copied to
status. Poll status every 30–60s. A FAIL/ABORTED requires diagnosis and a new full
pipeline UUID; this tool provides no hidden retries or threshold adjustment.

The short Job integration test deliberately kills its own direct controller,
verifies the joined venv worker and grandchild die, and verifies an unrelated
control process stays alive. It creates no database or HTTP load. Unit tests
exercise wrong source/UUID/exit/cleanup, incomplete elapsed windows, changed
thresholds and per-client renewal gaps. Prepared commands are not evidence that
the five phases or 24h have passed.
