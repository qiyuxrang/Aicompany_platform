"""Locations for isolated native QA evidence; never include credentials."""

import os
from pathlib import Path


def evidence_path(name: str) -> Path:
    if Path(name).name != name or not name.endswith(".json"):
        raise ValueError("Evidence must use a simple JSON filename")
    directory = Path(os.environ.get("A0_EVIDENCE_DIRECTORY", Path(__file__).parent))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


def runtime_url() -> str:
    port = int(os.environ.get("A0_RUNTIME_PORT", "18743"))
    if not 1024 <= port <= 65535:
        raise ValueError("Native QA requires an unprivileged loopback port")
    return f"http://127.0.0.1:{port}"
