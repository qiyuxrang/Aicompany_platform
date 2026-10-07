"""Fail-closed archive checks, separate from Django and PostgreSQL."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil

VOLUMES = {"hr", "product", "tender"}
UUID_DATABASE = re.compile(r"portal_pg_[a-f0-9]{32}\Z")


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def require_restore_target(source, target):
    if not UUID_DATABASE.fullmatch(source) or not UUID_DATABASE.fullmatch(target) or source == target:
        raise ValueError("restore requires a distinct, fresh portal_pg UUID database")


def assert_same_snapshot(before, after, label):
    if before != after:
        raise RuntimeError(f"{label} changed or did not restore exactly")


def safe_relative(name):
    if not isinstance(name, str) or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("unsafe archive path")
    path = PurePosixPath(name)
    if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] not in VOLUMES
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("unsafe archive path")
    return path


def no_links(root, target):
    root, target = Path(root), Path(target)
    if (root.is_symlink() or getattr(root, "is_junction", lambda: False)()
            or not target.resolve().is_relative_to(root.resolve())):
        raise ValueError("private archive escaped its root")
    relative = target.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        # Windows junctions are reparse points too; refuse them before any copy.
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("links/junctions cannot be archived or restored")


def file_manifest(root):
    root = Path(root)
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("archive root must be an ordinary directory")
    result = {}
    for volume in sorted(VOLUMES):
        directory = root / volume
        no_links(root, directory)
        if not directory.is_dir():
            raise ValueError("all scoped private volume directories must exist")
        for path in sorted(directory.rglob("*")):
            no_links(root, path)
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                safe_relative(relative)
                result[relative] = {"sha256": digest(path), "size": path.stat().st_size}
            elif not path.is_dir():
                raise ValueError("special files cannot be archived")
    return result


def validate_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    for name, entry in manifest.items():
        safe_relative(name)
        if (not isinstance(entry, dict) or set(entry) != {"sha256", "size"}
                or not isinstance(entry["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                or type(entry["size"]) is not int or entry["size"] < 0):
            raise ValueError("invalid file checksum/size metadata")


def copy_verified(source, destination, manifest):
    """Only copy an entire verified archive into a never-existing directory."""
    source, destination = Path(source), Path(destination)
    validate_manifest(manifest)
    if destination.exists() or destination.is_symlink():
        raise ValueError("restore destination must not exist; no overwrite or merge")
    no_links(destination.parent, destination)
    assert_same_snapshot(manifest, file_manifest(source), "archive files")
    destination.mkdir(mode=0o700)
    for volume in VOLUMES:
        (destination / volume).mkdir(mode=0o700)
    for name in manifest:
        relative = safe_relative(name)
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source.joinpath(*relative.parts), target)
        target.chmod(0o600)
    assert_same_snapshot(manifest, file_manifest(destination), "copied files")


def manifest_digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
