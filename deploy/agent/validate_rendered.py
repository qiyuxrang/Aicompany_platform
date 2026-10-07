"""Inspect offline Helm output without printing credentials or touching a cluster."""
import argparse
import json
from pathlib import Path

import yaml


def evaluate(documents, expected_image=None):
    failures = []
    deployments = {item.get("metadata", {}).get("name"): item for item in documents
                   if isinstance(item, dict) and item.get("kind") == "Deployment"}
    for suffix, jobs in (("api-server", "0"), ("queue", "4")):
        name = "portal-agent-" + suffix
        item = deployments.get(name)
        if item is None:
            failures.append("missing_" + suffix)
            continue
        pod = item["spec"]["template"]["spec"]
        container = pod["containers"][0]
        env = {value["name"]: value for value in container.get("env", [])}
        for variable, secret, key in (
            ("POSTGRES_URI", "portal-agent-postgres", "postgres_connection_url"),
            ("REDIS_URI", "portal-agent-redis", "redis_connection_url"),
            ("LANGSMITH_API_KEY", "portal-agent-license", "api_key"),
            ("LANGGRAPH_CLOUD_LICENSE_KEY", "portal-agent-license", "langgraph_cloud_license_key"),
        ):
            reference = env.get(variable, {}).get("valueFrom", {}).get("secretKeyRef", {})
            if reference.get("name") != secret or reference.get("key") != key:
                failures.append(suffix + ":secret_ref:" + variable)
        if env.get("N_JOBS_PER_WORKER", {}).get("value") != jobs:
            failures.append(suffix + ":split_queue")
        if "LANGGRAPH_AUTH_TYPE" in env or "LANGGRAPH_AUTH" in env:
            failures.append(suffix + ":custom_auth_override")
        if not any(value.get("secretRef", {}).get("name") == "portal-agent-application"
                   for value in container.get("envFrom", [])):
            failures.append(suffix + ":application_identity")
        if pod.get("terminationGracePeriodSeconds", 0) < 300:
            failures.append(suffix + ":drain_window")
        if not container.get("securityContext", {}).get("runAsNonRoot"):
            failures.append(suffix + ":nonroot")
        if any(container.get("securityContext", {}).get(key) != 10001 for key in ("runAsUser", "runAsGroup")):
            failures.append(suffix + ":shared_file_identity")
        volumes = {value["name"]: value for value in pod.get("volumes", [])}
        mounts = {value["name"]: value for value in container.get("volumeMounts", [])}
        for name, claim, path in (("product-private", "portal-product-private", "/app/.runtime/product-private"),
                                  ("hr-private", "portal-hr-private", "/app/.runtime/hr-private")):
            if (volumes.get(name, {}).get("persistentVolumeClaim", {}).get("claimName") != claim
                    or mounts.get(name, {}).get("mountPath") != path):
                failures.append(suffix + ":shared_pvc:" + name)
        if not container.get("image") or (expected_image is not None and container["image"] != expected_image):
            failures.append(suffix + ":candidate_image")
        if item["spec"].get("replicas", 0) < 1:
            failures.append(suffix + ":scale_to_zero")
    services = [item for item in documents if isinstance(item, dict) and item.get("kind") == "Service"]
    api_ports = {value.get("name"): value.get("containerPort") for value in deployments.get(
        "portal-agent-api-server", {}).get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("ports", [])}
    if not any(item["metadata"]["name"] == "portal-agent-api-server"
               and item["spec"].get("type", "ClusterIP") == "ClusterIP"
               and any(port.get("port") == 80 and (port.get("targetPort") == 8000 or api_ports.get(port.get("targetPort")) == 8000)
                       for port in item["spec"].get("ports", [])) for item in services):
        failures.append("private_api_service")
    if any(item["spec"].get("type") in {"LoadBalancer", "NodePort"} for item in services):
        failures.append("public_runtime_service")
    if any(isinstance(item, dict) and item.get("kind") == "Secret" for item in documents):
        failures.append("unexpected_rendered_credentials")
    images = {item["spec"]["template"]["spec"]["containers"][0].get("image") for item in deployments.values()}
    if len(images) != 1:
        failures.append("api_queue_image_mismatch")
    return {"result": "FAIL" if failures else "PASS", "failures": failures,
            "scope": "official_chart_render_only", "release_approved": False,
            "candidate_image_requires_real_release": any("REPLACE_" in (value or "") for value in images),
            "not_executed": ["runtime_license", "image_start", "persistence_and_draining", "cloud_acceptance"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--expected-image", help="Exact reviewed image reference to check in both API and queue.")
    options = parser.parse_args()
    report = evaluate(list(yaml.safe_load_all(options.manifest.read_text(encoding="utf-8-sig"))), options.expected_image)
    print(json.dumps(report))
    raise SystemExit(report["result"] != "PASS")
