import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT.parent / "监控看板"
WORKTREE = ROOT.parent / "ledger-portal-bridge-isolated"
REPORT = ROOT / "docs/evidence/closure-integration/source-baseline.json"


def git(directory, *arguments):
    return subprocess.check_output(["git", "-C", str(directory), *arguments], stderr=subprocess.DEVNULL)


def original_state():
    paths = git(LEGACY, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
    sources = {}
    for relative in sorted(set(paths)):
        path = LEGACY / relative
        if relative and path.is_file() and (relative.startswith("backend/") or relative == "requirements.txt"):
            if path.suffix == ".py" or relative == "requirements.txt":
                sources[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "head": git(LEGACY, "rev-parse", "HEAD").decode().strip(),
        "branch": git(LEGACY, "branch", "--show-current").decode().strip(),
        "index_sha256": hashlib.sha256(git(LEGACY, "ls-files", "--stage", "-z")).hexdigest(),
        "sources": sources,
    }


def main():
    if REPORT.exists() or WORKTREE.exists():
        raise SystemExit("Existing baseline or worktree: refuse overwrite")
    before = original_state()
    selected = []
    for relative in before["sources"]:
        if "/tests/" in relative and relative not in {"backend/ledger/tests/base.py", "backend/ledger/tests/test_auth_permissions.py"}:
            continue
        if "/management/" in relative:
            continue
        path = LEGACY / relative
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8-sig"))
        selected.append(relative)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    report = {"approved_scope": "INTEGRATION_APPROVAL.md sections 2, 3, 6; user approved 2026-09-21",
              "before": before, "snapshot_files": selected,
              "rationale": "Current backend startup/routing dependency closure and existing migrations; native auth regression and base only; excludes frontend, data, attachments, env, logs, caches and management seed commands.",
              "worktree": str(WORKTREE), "portal_index_sha256": hashlib.sha256(git(ROOT, "ls-files", "--stage", "-z")).hexdigest()}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess.run(["git", "-C", str(LEGACY), "worktree", "add", "-b", "feature/portal-read-bridge", str(WORKTREE), before["head"]], check=True)
    for relative in selected:
        destination = WORKTREE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(LEGACY / relative, destination)
    if original_state() != before:
        raise SystemExit("Original source changed concurrently; inspect before proceeding")
    subprocess.run(["git", "-C", str(WORKTREE), "add", "--", *selected], check=True)
    staged = git(WORKTREE, "diff", "--cached", "--name-only").decode().splitlines()
    if not set(staged).issubset(selected):
        raise SystemExit("Unexpected staged paths")
    report["staged_snapshot_files"] = staged
    report["source_unchanged"] = True
    subprocess.run(["git", "-C", str(WORKTREE), "commit", "-m", "Snapshot approved current backend sources for isolated portal bridge"], check=True)
    report["snapshot_commit"] = git(WORKTREE, "rev-parse", "HEAD").decode().strip()
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Isolated snapshot created: {len(staged)} existing-source changes; original source and index unchanged")


if __name__ == "__main__":
    main()
