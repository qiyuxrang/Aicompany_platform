"""Kill only this script's native server, then verify its persisted checkpoint."""

import importlib.metadata
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx


def main():
    directory = Path(__file__).resolve().parent
    repository = directory.parents[1]
    suffix = "crash-" + uuid.uuid4().hex
    environment = {**os.environ, "PYTHONPATH": str(repository / "backend") + os.pathsep + str(repository),
        "DJANGO_SETTINGS_MODULE": "qa.agent_platform.test_settings", "PYTHONUTF8": "1",
        "A0_DATABASE_NAME": suffix + ".sqlite3", "A0_RUNTIME_DIRECTORY": suffix,
        "A0_AUTO_RECONCILE": "1", "A0_SERVICE_TOKEN": "a0-synthetic-loopback-service-token-never-production"}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18743))
    output = directory / ".runtime" / suffix
    output.mkdir(parents=True)

    def command(script, *arguments):
        with (output / (Path(script).stem + ".log")).open("a", encoding="utf-8") as log:
            subprocess.run([sys.executable, str(repository / script), *arguments], env=environment,
                cwd=repository, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)

    def start(label):
        log = (output / (label + ".log")).open("w", encoding="utf-8")
        process = subprocess.Popen([sys.executable, str(directory / "native_server.py"), "18743"],
            env=environment, cwd=repository, stdout=log, stderr=subprocess.STDOUT)
        try:
            for attempt in range(120):
                if process.poll() is not None:
                    raise RuntimeError(f"native server exited: {process.returncode}; see {log.name}")
                try:
                    response = httpx.get("http://127.0.0.1:18743/ok", trust_env=False, timeout=1)
                    if response.status_code == 200:
                        return process, log
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            raise TimeoutError("native server not ready")
        except BaseException:
            process.kill()
            process.wait(timeout=15)
            log.close()
            raise

    command("backend/manage.py", "migrate", "--run-syncdb", "--noinput")
    process, log = start("before-kill")
    try:
        time.sleep(12)
        command("qa/agent_platform/verify_native.py")
        time.sleep(12)
        files = [{"path": str(path.relative_to(output)), "bytes": path.stat().st_size}
            for path in output.rglob("*.pckl")]
        assert any("checkpoint" in item["path"] and item["bytes"] > 100 for item in files), files
        process.kill()
        process.wait(timeout=15)
        killed_pid = process.pid
        log.close()
        process, log = start("after-kill")
        command("qa/agent_platform/verify_restart.py")
        evidence = {"simulation": True, "termination": "Popen.kill / Windows TerminateProcess",
            "killed_owned_pid": killed_pid, "state_directory": suffix,
            "versions": {name: importlib.metadata.version(name) for name in
                ("langgraph-api", "langgraph-runtime-inmem", "langgraph-sdk")},
            "wait_before_first_run_seconds": 12, "wait_after_state_reads_seconds": 12,
            "files_before_kill": files, "restart": json.loads((directory / "restart-evidence.json").read_text()),
            "result": "PASS", "scope": "periodically flushed native dev checkpoint, not synchronous crash durability"}
        (directory / "abrupt-restart-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, indent=2))
    finally:
        if process.poll() is None:
            try:
                response = httpx.post("http://127.0.0.1:18743/a0/shutdown", trust_env=False,
                    headers={"Authorization": "Bearer " + environment["A0_SERVICE_TOKEN"]}, timeout=5)
                response.raise_for_status()
                process.wait(timeout=30)
            except (httpx.HTTPError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=15)
        log.close()


if __name__ == "__main__":
    main()
