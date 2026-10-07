"""Non-overwriting snapshot of source worktrees, private artifacts and a pre-created PG dump."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

if __package__:
    from .private_path_safety import checked_path, regular_files
else:
    from private_path_safety import checked_path, regular_files


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source, destination, base, records, source_root):
    source = checked_path(source, root=source_root, must_exist=True)
    if not source.is_file():
        raise ValueError("Snapshot input must be a regular file")
    target = destination / base
    checked_path(target, root=destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    records.append({"path": base.as_posix(), "sha256": _sha(source)})


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def create_snapshot(destination, roots, storage_root, dump_path):
    destination = checked_path(destination)
    storage_root, dump_path = checked_path(storage_root, must_exist=True), checked_path(dump_path, must_exist=True)
    roots = [checked_path(root, must_exist=True) for root in roots]
    private_files = regular_files(storage_root)
    if any(destination == root or root.is_relative_to(destination) for root in [*roots, storage_root, dump_path]):
        raise ValueError("Snapshot overlaps source")
    if destination.is_relative_to(storage_root) or destination.is_relative_to(dump_path):
        raise ValueError("Snapshot overlaps source")
    for root in roots:
        if destination.is_relative_to(root):
            relative = destination.relative_to(root).as_posix()
            ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", relative], capture_output=True).returncode == 0
            if not ignored or destination.is_relative_to(storage_root):
                raise ValueError("Repository snapshot destination must be ignored")
    if dump_path.is_symlink() or not dump_path.is_file() or dump_path.stat().st_size == 0:
        raise ValueError("Missing database dump")
    if not storage_root.is_dir() or storage_root.is_symlink():
        raise ValueError("Invalid private storage root")
    destination.mkdir(parents=True, exist_ok=False)
    destination.chmod(0o700)
    records = []
    repos = []
    for index, root in enumerate(roots):
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Invalid source repository")
        prefix = Path("repos") / str(index)
        try:
            head = _git(root, "rev-parse", "HEAD").decode().strip()
            statuses = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
            entries = statuses.split(b"\0")
            paths = []
            for entry in entries:
                if not entry:
                    continue
                status, relative = entry[:2], entry[3:]
                if status[:1] in (b"R", b"C") or status[1:] in (b"R", b"C"):
                    raise ValueError("Renamed paths require manual review")
                name = Path(relative.decode("utf-8", "surrogateescape"))
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("Unsafe Git path")
                if any(part == ".runtime" for part in name.parts) or name.suffix == ".env":
                    continue
                source = root / name
                paths.append(name.as_posix())
                if source.exists() or source.is_symlink():
                    _copy_file(source, destination, prefix / name, records, root)
            diff = _git(root, "diff", "--binary", "HEAD", "--")
            diff_path = destination / prefix / "tracked.diff"
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            diff_path.write_bytes(diff)
            records.append({"path": (prefix / "tracked.diff").as_posix(), "sha256": _sha(diff_path)})
            repos.append({"head": head, "dirty_paths": paths})
        except (OSError, subprocess.CalledProcessError):
            # Test fixtures may not be Git repositories; actual backups always record HEAD.
            if (root / ".git").exists():
                raise
            for source in regular_files(root, skip=lambda path: path.name == ".runtime" or path.suffix == ".env"):
                _copy_file(source, destination, prefix / source.relative_to(root), records, root)
            repos.append({"head": None, "dirty_paths": []})
    for source in private_files:
        _copy_file(source, destination, Path("private") / source.relative_to(storage_root), records, storage_root)
    _copy_file(dump_path, destination, Path("database.dump"), records, dump_path.parent)
    manifest = {"repos": repos, "files": records}
    (destination / "backup_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    verify_snapshot(destination)
    return manifest


def verify_snapshot(destination):
    destination = checked_path(destination, must_exist=True)
    manifest_path = checked_path(destination / "backup_manifest.json", root=destination, must_exist=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["files"]:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe manifest path")
        target = destination / relative
        try:
            target = checked_path(target, root=destination, must_exist=True)
        except (OSError, ValueError) as error:
            raise ValueError("Snapshot file missing or unsafe") from error
        if not target.is_file():
            raise ValueError("Snapshot file missing or unsafe")
        if _sha(target) != record["sha256"]:
            raise ValueError("Snapshot checksum mismatch")
    return manifest
