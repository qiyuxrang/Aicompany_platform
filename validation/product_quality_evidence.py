"""Verify an immutable representative three-output evidence directory."""

import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_file(record):
    path = (ROOT / record["path"]).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise AssertionError(f"missing evidence file: {record['path']}")
    if sha256(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
        raise AssertionError(f"evidence hash or size mismatch: {record['path']}")
    return path


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: product_quality_evidence.py <version-chain.json>")
    chain_path = Path(sys.argv[1]).resolve()
    if not chain_path.is_relative_to(ROOT) or not chain_path.is_file():
        raise SystemExit("version chain must be an existing workspace file")
    chain = json.loads(chain_path.read_text(encoding="utf-8"))
    boundary = chain["boundary"]
    assert chain["authority"] == "docs/SPEC.md"
    assert boundary == {
        **boundary,
        "representative_input_only": True,
        "formal_database_modified": False,
        "real_customer_data_used": False,
        "model_calls": 0,
        "ragflow_calls": 0,
        "business_signoff": "blocked",
    }

    for record in chain["representative_input_files"]:
        checked_file(record)
    source_hashes = {record["sha256"] for record in chain["sources"]}
    assert source_hashes == {record["sha256"] for record in chain["representative_input_files"]}
    assert chain["input"]["sha256"] and chain["blueprint"]["sha256"]
    assert {record["kind"] for record in chain["versions"]} >= {"input", "blueprint", "chapter", "report"}

    delivered = chain["delivered"]
    assert set(delivered) == {"technical_solution_word", "feasibility_word", "presentation"}
    technical = checked_file(delivered["technical_solution_word"])
    feasibility = checked_file(delivered["feasibility_word"])
    presentation = checked_file(delivered["presentation"])
    with ZipFile(technical) as archive:
        body = archive.read("word/document.xml").decode()
        assert "园区安全接入与集中审计技术方案（代表性草稿）" in body
        assert "TOC" in body and "PAGEREF" in body and "w:hyperlink" in body
    with ZipFile(feasibility) as archive:
        body = archive.read("word/document.xml").decode()
        assert "园区安全接入与集中审计可行性研究报告（代表性草稿）" in body
        assert "不形成投资回报、成本节约或收益结论" in body
        assert "TOC" in body and "PAGEREF" in body and "w:hyperlink" in body
    with ZipFile(presentation) as archive:
        slides = [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        assert len(slides) == chain["ppt_render"]["page_count"] >= 3
        slide_xml = "".join(archive.read(name).decode() for name in slides)
        presentation_record = next(record for record in chain["artifacts"] if record["family"] == "presentation")
        for source in presentation_record["render_evidence"]["source_versions"]:
            assert source["family"] in slide_xml and source["sha256"] in slide_xml

    assert set(chain["word_renders"]) == {"technical_solution", "feasibility"}
    for key, render in chain["word_renders"].items():
        assert render["renderer"] == "Microsoft Word" and render["status"] == "rendered"
        assert render["page_count"] >= 1 and len(render["pages"]) == render["page_count"]
        delivered_key = "technical_solution_word" if key == "technical_solution" else "feasibility_word"
        word = delivered[delivered_key]
        assert word["office_fields_refreshed"] is True
        assert word["sha256"] == render["rendered_docx_sha256"]
        assert word["rendered_docx_differs_from_source"] == render["rendered_docx_differs_from_source"]
        assert any(artifact["id"] == word["source_artifact_id"]
                   and artifact["sha256"] == word["source_artifact_sha256"]
                   for artifact in chain["artifacts"])
        checked_file(render["pdf"])
        for page in render["pages"]:
            checked_file(page)
    render = chain["ppt_render"]
    assert render["renderer"] == "Microsoft PowerPoint 16.0" and render["status"] == "rendered"
    assert render["page_count"] >= 3 and len(render["pages"]) == render["page_count"]
    assert render["quality_claim"] == "not_ppt_master"
    checked_file(render["pdf"])
    for page in render["pages"]:
        checked_file(page)

    artifacts = chain["artifacts"]
    assert {record["family"] for record in artifacts} == {"technical-solution", "feasibility", "presentation"}
    assert all(record["sha256"] and record["generation_hash"] and record["input_hash"]
               and record["blueprint_hash"] and record["template_hash"] for record in artifacts)
    outputs = chain["outputs"]
    assert {record["family"] for record in outputs} == {"technical-solution", "feasibility", "presentation"}
    assert all(record["current"] and record["draft"] for record in outputs)
    assert all(attempt["status"] == "done" and attempt["model_calls"] == 0
               for attempt in chain["worker_attempts"])
    assert set(chain["formal_artifact_approval_attempts"]) == {
        "technical-solution", "feasibility", "presentation"}
    assert all(record["code"] == "formal_release_blocked"
               for record in chain["formal_artifact_approval_attempts"].values())
    scale = chain.get("scale_validation", {"enabled": False})
    if scale.get("enabled"):
        assert scale["metric"] == "non_whitespace_characters"
        assert scale["actual"]["technical-solution"] >= scale["targets"]["technical-solution"]
        assert scale["actual"]["feasibility"] >= scale["targets"]["feasibility"]
    print(json.dumps({
        "result": "PASS",
        "chain": chain_path.relative_to(ROOT).as_posix(),
        "sources": len(chain["sources"]),
        "versions": len(chain["versions"]),
        "approvals": len(chain["approvals"]),
        "artifacts": len(artifacts),
        "renders": {
            "technical_word_pages": chain["word_renders"]["technical_solution"]["page_count"],
            "feasibility_word_pages": chain["word_renders"]["feasibility"]["page_count"],
            "ppt_slides": render["page_count"],
        },
        "external_calls": {"model": 0, "ragflow": 0},
        "formal_approval": "blocked",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
