"""Native image license preflight on an exclusive, egress-disabled Docker network."""

import json
import subprocess
import time
import uuid
from pathlib import Path


def docker(*arguments, check=True):
    return subprocess.run(["docker", *arguments], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=check)


def main():
    prefix = "a0-native-pg-" + uuid.uuid4().hex[:12]
    names = {kind: prefix + "-" + kind for kind in ("postgres", "redis", "api")}
    image = "langchain/langgraph-api:0.15.1-py3.13"
    evidence = {"simulation": True, "purpose": "license preflight only, not harness acceptance",
        "network": prefix, "egress": "Docker internal network; no published ports or credentials",
        "image": image, "containers": names}
    created = []
    docker("network", "create", "--internal", prefix)
    try:
        for kind, arguments in (
            ("postgres", ["-e", "POSTGRES_USER=a0", "-e", "POSTGRES_PASSWORD=a0-synthetic-only",
                          "-e", "POSTGRES_DB=a0", "postgres:16"]),
            ("redis", ["redis:6-alpine"]),
        ):
            docker("run", "-d", "--name", names[kind], "--network", prefix, *arguments)
            created.append(names[kind])
        for attempt in range(60):
            if docker("exec", names["postgres"], "pg_isready", "-U", "a0", check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise TimeoutError("isolated Postgres not ready")
        docker("run", "-d", "--name", names["api"], "--network", prefix,
            "-e", f"DATABASE_URI=postgres://a0:a0-synthetic-only@{names['postgres']}:5432/a0?sslmode=disable",
            "-e", f"REDIS_URI=redis://{names['redis']}:6379",
            "-e", "LANGSERVE_GRAPHS={}", "-e", "LANGSMITH_TRACING=false",
            "-e", "LANGGRAPH_METRICS_ENABLED=false", "-e", "LANGGRAPH_LOGS_ENABLED=false", image)
        created.append(names["api"])
        for attempt in range(40):
            status = json.loads(docker("inspect", names["api"]).stdout)[0]
            if not status["State"]["Running"]:
                break
            time.sleep(0.5)
        logs = docker("logs", names["api"], check=False)
        evidence["image_id"] = status["Image"]
        evidence["api_state"] = status["State"]
        evidence["native_logs"] = logs.stdout + logs.stderr
        evidence["license_missing"] = "License verification failed" in evidence["native_logs"]
        evidence["result"] = "BLOCKED_LICENSE" if evidence["license_missing"] else "INSPECT_NATIVE_LOGS"
    finally:
        for name in reversed(created):
            docker("rm", "-f", "-v", name)
        docker("network", "rm", prefix)
        evidence["own_containers_and_network_removed"] = True
        (Path(__file__).parent / "native-pg-preflight-evidence.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in evidence.items() if key != "native_logs"}, indent=2))


if __name__ == "__main__":
    main()
