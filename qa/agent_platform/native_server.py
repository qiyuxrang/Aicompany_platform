"""Development Runtime only; synthetic test process refuses non-loopback egress."""

import ipaddress
import os
import sys
from pathlib import Path


def audit_network(event, arguments):
    if event == "socket.connect":
        address = arguments[1]
        if isinstance(address, tuple):
            try:
                allowed = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                allowed = address[0] == "localhost"
            if not allowed:
                raise OSError("A0 synthetic runtime refuses external egress")


if __name__ == "__main__":
    if os.environ.get("DJANGO_SETTINGS_MODULE") != "qa.agent_platform.test_settings":
        raise RuntimeError("A0 isolated settings required")
    sys.addaudithook(audit_network)
    os.environ["LOG_COLOR"] = "false"
    from langgraph_api.cli import run_server
    directory = Path(__file__).parent.resolve()
    state_name = os.environ.get("A0_RUNTIME_DIRECTORY", "")
    if state_name and (not state_name.replace("-", "").isalnum()):
        raise RuntimeError("A0 runtime directory must be a safe local name")
    state_directory = directory / ".runtime" / state_name
    state_directory.mkdir(parents=True, exist_ok=True)
    os.chdir(state_directory)
    run_server(host="127.0.0.1", port=int(sys.argv[1]), n_jobs_per_worker=6,
        reload=False, open_browser=False, allow_blocking=False,
        graphs={name: (directory / "native_graphs.py").as_posix() + ":" + name
                for name in ("supervisor", "researcher", "reviewer")},
        auth={"path": str(directory / "native_graphs.py") + ":auth", "disable_studio_auth": True},
        http={"app": str(directory / "native_graphs.py") + ":app"},
        env={"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
             "LANGGRAPH_METRICS_ENABLED": "false", "LANGGRAPH_LOGS_ENABLED": "false"})
