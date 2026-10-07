"""Pure safety boundaries for owned Windows Worker crash rehearsals."""
import ast
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/worker-fault-acceptance"

def require(value, code):
    if not value:
        raise ValueError(code)

def owned_directory(path, fixture_id):
    token = uuid.UUID(str(fixture_id)).hex
    require(str(fixture_id) == token, "canonical_fixture_id_required")
    path = Path(path).resolve()
    require(path == (RUNTIME / token).resolve(), "owned_fixture_directory_required")
    return path

def validate_gate(gate, directory, label):
    require(isinstance(gate, dict), "object_gate_required")
    owned_directory(directory, gate.get("fixture_id"))
    require(gate.get("label") == label and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", label)), "worker_label_mismatch")
    require(gate.get("kind") in ("control", "hr", "product") and type(gate.get("once")) is bool, "worker_command_not_allowed")
    require(bool(re.fullmatch(r"portal-qa-[a-f0-9]{32}", gate.get("job_name", ""))), "owned_job_name_required")
    return gate

def validate_options(args):
    require(math.isfinite(args.deadline) and 900 <= args.deadline <= 3600, "bounded_deadline_900_3600_required")
    require(math.isfinite(args.phase_timeout) and 360 <= args.phase_timeout <= 600, "bounded_phase_timeout_360_600_required")
    require(math.isfinite(args.max_fixture_disk_mb) and 256 <= args.max_fixture_disk_mb <= 2048, "bounded_disk_256_2048_required")

def registration_valid(value, gate, members, parent_pid):
    validate_gate(gate, gate["run_dir"], gate["label"])
    require(value.get("fixture_id") == gate["fixture_id"] and value.get("label") == gate["label"]
            and value.get("job_name") == gate["job_name"], "worker_registration_mismatch")
    identity = value.get("identity", {})
    require(type(identity.get("pid")) is int and identity["pid"] in members and identity.get("active") is True
            and type(identity.get("creation_filetime")) is int and identity["creation_filetime"] > 0, "worker_identity_not_owned")
    require(value.get("parent_pid") == parent_pid or value.get("parent_pid") in members, "worker_parent_not_owned")
    require(len(members) <= 16, "worker_subtree_limit")
    return identity

def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)

def source_manifest():
    from qa.run_portable_postgres import source_snapshot, source_files
    files = dict(source_snapshot()["files"])
    for name in ("qa/worker_fault_acceptance", "qa/browser_acceptance/process_job.py", "qa/release_pipeline/identity.py"):
        path = ROOT / name
        paths = source_files(path, ".py") if path.is_dir() else [path]
        for p in paths:
            files[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def expiry_valid(lease_until, now, *, elapsed_seconds):
    require(lease_until is not None and lease_until.tzinfo is not None and now.tzinfo is not None, "aware_real_lease_required")
    require(elapsed_seconds >= 0, "negative_observation_time")
    return now >= lease_until


def hr_lease_contract(source):
    """Read both real claim/renew literals; fail if the frozen contract changes."""
    values = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in ("claim_one", "renew_one"):
            leases = []
            for assignment in ast.walk(node):
                if not isinstance(assignment, ast.Assign) or not any(
                        isinstance(target, ast.Attribute) and target.attr == "lease_until"
                        for target in assignment.targets):
                    continue
                for call in ast.walk(assignment.value):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "timedelta":
                        for keyword in call.keywords:
                            if keyword.arg == "seconds" and isinstance(keyword.value, ast.Constant):
                                leases.append(keyword.value.value)
            require(leases == [300], "hr_actual_lease_contract_changed")
            values[node.name] = leases[0]
    require(set(values) == {"claim_one", "renew_one"}, "hr_actual_lease_contract_missing")
    return {"source": "backend/portal/hr_screening_worker.py", "claim_seconds": values["claim_one"],
            "renew_seconds": values["renew_one"], "evidence_type": "frozen actual production function literals; real DB deadline also observed"}
