"""Read an owned pipeline status; request abort without arbitrary process termination."""
import argparse
import json
import time
from .contracts import atomic_json, owned_run, read_json, require
from .identity import identity_alive


def inspect(run):
    state = read_json(run / "status.json")
    require(state.get("pipeline_id") == run.name, "status_uuid_mismatch")
    owner = read_json(run / "owner.json")
    require(owner.get("pipeline_id") == run.name, "owner_uuid_mismatch")
    active = identity_alive(owner["identity"])
    result = state["result"]
    if result == "RUNNING" and not active:
        result = "ABORTED"
    if result == "PASS":
        final = read_json(run / "report.json")
        require(final == state and len(final.get("phases", [])) == 5
                and all(p.get("result") == "PASS" and p.get("owned_job_cleanup", {}).get("verified") is True
                        for p in final["phases"])
                and final.get("source_before") == final.get("source_after"), "final_pass_evidence_incomplete")
    return {"pipeline_id": run.name, "result": result, "persisted_result": state["result"],
            "supervisor_identity_alive": active, "status_age_seconds": max(0, time.time()-state["updated_at"]),
            "current_phase": state.get("current_phase"),
            "completed_phases": sum(p.get("result") == "PASS" for p in state.get("phases", [])),
            "production_ready": False, "status": str(run / "status.json"),
            "cleanup_after_abrupt_exit": "named job KILL_ON_JOB_CLOSE; retained RUNNING is never a PASS"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--request-abort", action="store_true")
    args = parser.parse_args()
    run = owned_run(args.run_dir)
    result = inspect(run)
    if args.request_abort:
        require(result["result"] == "RUNNING" and result["supervisor_identity_alive"], "live_owned_pipeline_required")
        require(not (run / "abort.request.json").exists(), "abort_already_requested")
        atomic_json(run / "abort.request.json", {"pipeline_id": run.name, "requested_at": time.time()})
        result["abort_requested"] = True
    print(json.dumps(result), flush=True)
    return 0 if result["result"] in ("RUNNING", "PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
