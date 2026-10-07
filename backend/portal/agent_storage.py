"""Native virtual StoreBackend with server-bound scope and read-only skills."""

import re
from pathlib import PurePosixPath

from asgiref.sync import sync_to_async
from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol

from .agent_runtime import AgentDenied


def valid_path(path, *, shared=False):
    if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
            or any(character in path for character in ("\\", ":", "%", "\x00"))
            or any(part in {"..", ".", "~"} for part in path.split("/"))):
        raise AgentDenied("invalid_virtual_path")
    if not shared and path != "/" and PurePosixPath(path).parts[1] not in {
            "workspace", "large_tool_results", "conversation_history"}:
        raise AgentDenied("path_not_allowed")
    return path


class GuardedBackend(BackendProtocol):
    def __init__(self, backend, guard, *, readonly=False, skill_digest=None):
        self.backend, self.guard, self.readonly = backend, guard, readonly
        self.skill_digest = skill_digest

    def _validate(self, paths, write=False):
        self.guard.check()
        if self.skill_digest:
            from .agent_skills import skill_bundle
            if skill_bundle(self.guard.binding.owner_id)["digest"] != self.skill_digest:
                raise AgentDenied("skill_not_installed")
        if write and self.readonly:
            raise AgentDenied("readonly_skills")
        for path in paths:
            valid_path(path, shared=self.readonly)

    def _call(self, name, paths, *args, write=False, **kwargs):
        self._validate(paths, write)
        result = getattr(self.backend, name)(*args, **kwargs)
        self._validate(paths, write)
        return result

    async def _acall(self, name, paths, *args, write=False, **kwargs):
        await sync_to_async(self._validate)(paths, write)
        result = await getattr(self.backend, name)(*args, **kwargs)
        await sync_to_async(self._validate)(paths, write)
        return result

    def ls(self, path):
        return self._call("ls", [path], path)

    async def als(self, path):
        return await self._acall("als", [path], path)

    def read(self, file_path, offset=0, limit=2000):
        return self._call("read", [file_path], file_path, offset, limit)

    async def aread(self, file_path, offset=0, limit=2000):
        return await self._acall("aread", [file_path], file_path, offset, limit)

    def write(self, file_path, content):
        return self._call("write", [file_path], file_path, content, write=True)

    async def awrite(self, file_path, content):
        return await self._acall("awrite", [file_path], file_path, content, write=True)

    def edit(self, file_path, old_string, new_string, replace_all=False):
        return self._call("edit", [file_path], file_path, old_string, new_string, replace_all, write=True)

    async def aedit(self, file_path, old_string, new_string, replace_all=False):
        return await self._acall("aedit", [file_path], file_path, old_string, new_string, replace_all, write=True)

    def grep(self, pattern, path=None, glob=None, max_count=None):
        return self._call("grep", [path or "/"], pattern, path, glob, max_count=max_count)

    async def agrep(self, pattern, path=None, glob=None, max_count=None):
        return await self._acall("agrep", [path or "/"], pattern, path, glob, max_count=max_count)

    def glob(self, pattern, path=None):
        if ".." in pattern.split("/") or "\\" in pattern or ":" in pattern:
            raise AgentDenied("invalid_glob")
        return self._call("glob", [path or "/"], pattern, path)

    async def aglob(self, pattern, path=None):
        if ".." in pattern.split("/") or "\\" in pattern or ":" in pattern:
            raise AgentDenied("invalid_glob")
        return await self._acall("aglob", [path or "/"], pattern, path)

    def upload_files(self, files):
        return self._call("upload_files", [path for path, _ in files], files, write=True)

    async def aupload_files(self, files):
        return await self._acall("aupload_files", [path for path, _ in files], files, write=True)

    def download_files(self, paths):
        return self._call("download_files", paths, paths)

    async def adownload_files(self, paths):
        return await self._acall("adownload_files", paths, paths)


def scoped_backend(guard, *, store=None, skill_digest=None):
    bound = guard.binding
    namespace = ("agent", str(bound.owner_id), bound.root_id, bound.run_id)
    private = GuardedBackend(StoreBackend(store=store, namespace=lambda _: namespace), guard)
    if skill_digest is None:
        return private
    if not re.fullmatch(r"[0-9a-f]{64}", skill_digest):
        raise AgentDenied("invalid_skill_digest")
    shared = GuardedBackend(StoreBackend(store=store,
        namespace=lambda _: ("agent-skills", skill_digest)), guard, readonly=True, skill_digest=skill_digest)
    return CompositeBackend(default=private, routes={"/skills/": shared})
