import hashlib
import json
import math
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/postgres-fault-acceptance"

def require(value, code):
    if not value:
        raise ValueError(code)

def owned_directory(directory, token):
    require(isinstance(token, str) and re.fullmatch(r"[a-f0-9]{32}", token), "canonical_fixture_id_required")
    directory = Path(directory).resolve()
    require(directory == (RUNTIME / token).resolve(), "owned_fixture_directory_required")
    return directory

def gate_valid(gate, directory, kind):
    require(isinstance(gate, dict), "object_gate_required")
    owned_directory(directory, gate.get("fixture_id"))
    require(gate.get("kind") == kind and kind in ("coordinator", "web"), "gate_kind_mismatch")
    require(bool(re.fullmatch(r"portal-qa-[a-f0-9]{32}", gate.get("job_name", ""))), "owned_job_name_required")
    return gate

def validate_budgets(deadline, disk_mb):
    require(math.isfinite(deadline) and 180 <= deadline <= 900, "bounded_deadline_180_900_required")
    require(math.isfinite(disk_mb) and 256 <= disk_mb <= 2048, "bounded_disk_256_2048_required")

def durability_valid(values):
    require(all(values.get(key) == "on" for key in ("fsync", "full_page_writes", "synchronous_commit")), "normal_durability_required")
    require(values.get("in_recovery") is False, "standalone_primary_required")

def owner_valid(token, cluster, marker, lines, identity, process, port):
    require(marker == {"token": token, "cluster": str(cluster)}, "pg_owner_marker_mismatch")
    require(cluster.resolve() == cluster, "pg_cluster_path_changed")
    require(process.poll() is None and identity.get("active") is True and identity.get("pid") == process.pid
            and type(identity.get("creation_filetime")) is int and identity["creation_filetime"] > 0, "pg_creation_identity_required")
    require(len(lines) >= 4 and int(lines[0]) == process.pid and Path(lines[1]).resolve() == cluster
            and int(lines[3]) == port, "pg_pid_directory_port_mismatch")

def rollback_valid(before, after, pending_exists):
    require(before == after and pending_exists is False, "committed_hash_or_uncommitted_rollback_failed")

def redo_valid(log_segment):
    # initdb locale C; preserve the original log privately, expose booleans only.
    lower = log_segment.lower()
    evidence = {"automatic_recovery": "automatic recovery in progress" in lower,
                "redo_started": "redo starts at" in lower,
                "redo_finished": "redo done at" in lower,
                "ready": "ready to accept connections" in lower}
    require(all(evidence.values()), "actual_wal_redo_evidence_missing")
    return evidence

def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)

def source_manifest():
    from qa.run_portable_postgres import source_snapshot, source_files
    files = dict(source_snapshot()["files"])
    for name in ("qa/postgres_fault_acceptance", "qa/browser_acceptance/process_job.py", "qa/release_pipeline/identity.py"):
        path = ROOT / name
        for item in source_files(path, ".py") if path.is_dir() else [path]:
            files[item.relative_to(ROOT).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    return {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
