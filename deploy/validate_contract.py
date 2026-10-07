"""Offline candidate checks. No Docker/cluster/network/DB operations or secrets."""
import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def validate(compose, values, ingress, nginx, agent_dockerfile, graph_config):
    failures = []

    def require(condition, code):
        if not condition:
            failures.append(code)

    services = compose["services"]
    require(services["migrate"]["command"] == ["python", "manage.py", "migrate", "--noinput"], "one_time_migration")
    require(services["migrate"].get("restart") == "no", "migration_no_restart")
    require("migrate" not in str(services["backend"]["command"]), "web_never_migrates")
    web_command = " ".join(services["backend"]["command"])
    connection_limit = re.search(r"--connection-limit=(\d+)(?=\s|$)", web_command)
    require(connection_limit is not None and int(connection_limit[1]) == 512, "web_connection_budget")
    require("--asyncore-use-poll" in web_command.split(), "web_linux_poll")
    require(services["db"]["image"] == "postgres:17.11@sha256:ae69c452f483507a6b99fb654cf93aad7fe156ffd2c56247707eef4e36d3c12b",
            "postgres_security_patch_pin")
    require(services["backend"]["depends_on"].get("migrate", {}).get("condition") == "service_completed_successfully", "web_waits_for_migration")
    volume = "engineering_private_data:/app/.runtime/engineering-private"
    require(volume in services["backend"]["volumes"] and volume in services["engineering-worker"]["volumes"], "engineering_shared_volume")
    require(services["engineering-worker"].get("profiles") == ["engineering"], "engineering_optional")
    for name in ("apiServer", "queue"):
        section = values[name]
        require(section["deployment"]["replicaCount"] >= 1, name + "_no_scale_to_zero")
        require(section.get("service", {}).get("type", "ClusterIP") == "ClusterIP", name + "_private")
        require(section["deployment"]["terminationGracePeriodSeconds"] >= 300, name + "_draining")
        require(any(item.get("secretRef", {}).get("name") == "portal-agent-application"
                    for item in section["deployment"]["envFrom"]), name + "_application_identity")
        require(not any(item["name"] in {"LANGSMITH_API_KEY", "LANGGRAPH_CLOUD_LICENSE_KEY", "LANGGRAPH_AUTH", "LANGGRAPH_HTTP"}
                        for item in section["deployment"]["extraEnv"]), name + "_no_identity_override")
        require({item["name"] for item in section["deployment"]["volumeMounts"]} >= {"product-private", "hr-private"}, name + "_domain_files")
    require(values["config"]["existingSecretName"] == "portal-agent-license", "license_secret")
    require(values["queue"]["enabled"] is True, "official_split_queue")
    for store in ("postgres", "redis"):
        require(values[store]["external"]["enabled"] and bool(values[store]["external"].get("existingSecretName")), store + "_external_secret")
    require(bool(ingress["spec"]["tls"]) and ingress["spec"]["ingressClassName"] == "nginx-internal", "internal_tls")
    # Verify legal maximum requests through actual location selection, while an
    # unrelated administrative URL remains at the small default limit.
    rules = re.findall(r"location\s+(~|=)\s+([^\s{]+)\s*\{([^}]+)\}", nginx)
    default = re.search(r"client_max_body_size\s+(\d+)k;", nginx)
    require(default is not None and int(default[1]) == 64, "default_small_body")
    admin_import = next((body for kind, pattern, body in rules if kind == "="
                         and pattern == "/admin/portal/gatewaymodel/import/"), "")
    admin_limit = re.search(r"client_max_body_size\s+(\d+)k;", admin_import)
    require(admin_limit is not None and int(admin_limit[1]) == 128, "model_import_multipart_budget")
    # Reject broad admin overrides: the sole expanded management endpoint is
    # the exact model importer, not arbitrary admin/API prefixes or regexes.
    admin_probe = "/admin/portal/user/"
    require(not any(kind == "~" and re.search(pattern, admin_probe) or kind == "=" and pattern == admin_probe
                    for kind, pattern, body in rules if "client_max_body_size" in body), "other_admin_retains_small_body")
    prefixes = re.findall(r"location\s+(\^~\s+)?(/[^\s{]*)\s*\{([^}]+)\}", nginx)
    require(not any(admin_probe.startswith(pattern) and "client_max_body_size" in body
                    for _, pattern, body in prefixes), "no_broad_admin_body_override")
    for route, minimum in (("/api/product/tasks/12345678-abcd/sources/", 20),
                           ("/api/agent/attachments/", 20),
                           ("/api/hr/recruitment/batches/12345678-abcd/resumes/", 40),
                           ("/api/engineering/jobs/", 40),
                           ("/api/hr/recruitment/requests/upload-jd/", 2),
                           ("/api/business/ledgers/finance/import/", 2)):
        match = next((body for kind, pattern, body in rules if kind == "=" and route == pattern), None)
        if match is None:
            match = next((body for kind, pattern, body in rules if kind == "~" and re.search(pattern, route)), "")
        limit = re.search(r"client_max_body_size\s+(\d+)m;", match)
        require(limit is not None and int(limit[1]) > minimum, "upload_limit:" + route)
    require(not any(kind == "~" and re.search(pattern, "/api/ops/users/") for kind, pattern, _ in rules), "admin_retains_small_body")
    require("--group agent-runtime" not in agent_dockerfile and "/api/constraints.txt" in agent_dockerfile, "production_server_constraints")
    for variable, expected in (("LANGSERVE_GRAPHS", graph_config["graphs"]),
                               ("LANGGRAPH_AUTH", graph_config["auth"]),
                               ("LANGGRAPH_HTTP", graph_config["http"])):
        match = re.search(variable + r"='([^']+)'", agent_dockerfile)
        require(match is not None and json.loads(match[1]) == expected, "image_config:" + variable)
    return failures


def documents(root=ROOT):
    def load(relative):
        return yaml.safe_load((root / relative).read_text(encoding="utf-8"))
    return (load("compose.yaml"), load("deploy/agent/helm-values.example.yaml"),
            load("deploy/agent/internal-tls.yaml"), (root / "deploy/nginx.conf.example").read_text(encoding="utf-8"),
            (root / "deploy/agent/Dockerfile").read_text(encoding="utf-8"),
            json.loads((root / "langgraph.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    failures = validate(*documents())
    print(json.dumps({"result": "FAIL" if failures else "PASS", "scope": "offline_candidate_contract_only",
                      "failures": failures, "release_approved": False,
                      "not_executed": ["nginx_t", "docker_image_build", "helm_template", "licensed_pg_runtime", "cloud_acceptance"]}))
    raise SystemExit(bool(failures))
