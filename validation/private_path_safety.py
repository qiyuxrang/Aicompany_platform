"""Copy-time boundaries for ordinary private files; no services or configuration."""
import os
from pathlib import Path
import stat


def checked_path(value, *, root=None, must_exist=False):
    """Preserve link identity before resolve; reject linked ancestors as well."""
    path = Path(os.path.abspath(value))
    for current in (*reversed(path.parents), path):
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("Links and junctions cannot be copied or restored")
        try:
            attributes = current.lstat()
        except FileNotFoundError:
            continue
        if getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("Reparse points cannot be copied or restored")
    resolved = path.resolve(strict=must_exist)
    if root is not None:
        base = checked_path(root, must_exist=True)
        if not path.is_relative_to(base) or not resolved.is_relative_to(base.resolve(strict=True)):
            raise ValueError("Private file escaped its selected root")
    return path


def regular_files(value, *, skip=None):
    """Validate the whole tree before any copying; never descend through a link."""
    root = checked_path(value, must_exist=True)
    if not root.is_dir():
        raise ValueError("Private root must be an ordinary directory")
    directories, files = [root], []
    while directories:
        for path in sorted(directories.pop().iterdir()):
            if skip is not None and skip(path):
                continue
            checked_path(path, root=root, must_exist=True)
            if path.is_dir():
                directories.append(path)
            elif path.is_file():
                files.append(path)
            else:
                raise ValueError("Only ordinary directories and files can be copied")
    return tuple(sorted(files))
