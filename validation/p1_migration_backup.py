"""Non-overwriting snapshot of source worktrees, private artifacts and a pre-created PG dump."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source, destination, base, records):
    if source.is_symlink() or not source.is_file():
        raise ValueError("Snapshot input must be a regular file")
    target = destination / base
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    records.append({"path": base.as_posix(), "sha256": _sha(source)})


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def create_snapshot(destination, roots, storage_root, dump_path):
    destination = Path(destination).absolute()
    storage_root, dump_path = Path(storage_root).absolute(), Path(dump_path).absolute()
    roots = [Path(root).absolute() for root in roots]
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
                    _copy_file(source, destination, prefix / name, records)
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
            for source in root.rglob("*"):
                if source.is_file() and not source.is_symlink() and ".runtime" not in source.parts and source.suffix != ".env":
                    _copy_file(source, destination, prefix / source.relative_to(root), records)
            repos.append({"head": None, "dirty_paths": []})
    for source in storage_root.rglob("*"):
        if source.is_symlink():
            raise ValueError("Symlinks in private storage are forbidden")
        if source.is_file():
            _copy_file(source, destination, Path("private") / source.relative_to(storage_root), records)
    _copy_file(dump_path, destination, Path("database.dump"), records)
    manifest = {"repos": repos, "files": records}
    (destination / "backup_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    verify_snapshot(destination)
    return manifest


def verify_snapshot(destination):
    destination = Path(destination).resolve(strict=True)
    manifest = json.loads((destination / "backup_manifest.json").read_text(encoding="utf-8"))
    for record in manifest["files"]:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe manifest path")
        target = destination / relative
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(destination):
            raise ValueError("Snapshot file missing or unsafe")
        if _sha(target) != record["sha256"]:
            raise ValueError("Snapshot checksum mismatch")
    return manifest
