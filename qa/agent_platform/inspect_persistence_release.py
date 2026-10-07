"""Read official release artifacts without installing or modifying the SDK."""

import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def main():
    package = "langgraph-runtime-inmem"
    index = fetch_json(f"https://pypi.org/pypi/{package}/json")
    evidence = {"latest_stable": index["info"]["version"], "releases": []}
    directory = Path(__file__).parent / ".runtime" / "official-releases"
    directory.mkdir(parents=True, exist_ok=True)
    for version in ("0.35.1", "0.36.0rc2", "0.37.0.dev3"):
        release = fetch_json(f"https://pypi.org/pypi/{package}/{version}/json")
        artifact = next(item for item in release["urls"] if item["filename"].endswith(".whl"))
        with urllib.request.urlopen(artifact["url"], timeout=30) as response:
            content = response.read()
        digest = hashlib.sha256(content).hexdigest()
        assert digest == artifact["digests"]["sha256"]
        (directory / artifact["filename"]).write_bytes(content)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            persistence = archive.read("langgraph_runtime_inmem/_persistence.py").decode()
            checkpoint = archive.read("langgraph_runtime_inmem/checkpoint.py").decode()
        selected = {}
        for filename, source in (("_persistence.py", persistence), ("checkpoint.py", checkpoint)):
            selected[filename] = [{"line": number, "text": line.strip()}
                for number, line in enumerate(source.splitlines(), 1)
                if any(term in line for term in ("store :=", "__persistence_hook__", "os.environ", "os.getenv"))]
        evidence["releases"].append({"version": version, "url": artifact["url"],
            "sha256": digest, "uploaded": artifact["upload_time_iso_8601"], "source": selected,
            "requires": release["info"]["requires_dist"]})
    target = Path(__file__).parent / "persistence-release-evidence.json"
    target.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
