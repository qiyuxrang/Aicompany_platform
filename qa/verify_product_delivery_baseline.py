"""Reproducible acceptance gate for the frozen product-delivery baseline.

The gate checks the machine-readable policy, frozen-pack integrity and the
historical v10 format references separately from actual formal outputs. It performs no model, RAGFlow or
network calls and therefore cannot fabricate a knowledge-base hit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from verify_product_deliverables import CHARACTER_COUNT_SCOPE, inspect_docx, inspect_pptx


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "backend" / "portal" / "product_assets" / "bj_docs"
DEFAULT_BASELINE = PACK / "assets" / "delivery-baseline.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, code: str, issues: list[str]) -> None:
    if not condition:
        issues.append(code)


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"object_required:{path}")
    return payload


def validate_contract(baseline: dict, policy: dict, rules: dict) -> dict:
    issues: list[str] = []
    check(baseline.get("schema") == "PRODUCT_DELIVERY_BASELINE_V1", "baseline_schema_invalid", issues)
    check(baseline.get("status") == "formal_length_required_pending_live_acceptance", "baseline_status_invalid", issues)
    check(baseline.get("character_count_scope") == CHARACTER_COUNT_SCOPE, "character_count_scope_invalid", issues)
    check("accepted_artifacts" not in baseline and isinstance(baseline.get(
        "historical_format_reference_artifacts_not_formal_length_acceptance"), dict),
        "historical_reference_scope_invalid", issues)
    check(policy.get("schema") == "BJ_PRODUCT_DOCUMENT_FORMAT_POLICY_V2", "format_policy_schema_invalid", issues)
    check(policy.get("acceptance_evidence_revision") == "format-governance-acceptance-20260927-v10",
          "format_acceptance_revision_invalid", issues)

    word = baseline.get("word_format", {})
    check(word.get("ordinary_margins_mm") == {
        "top": 25.4, "bottom": 25.4, "left": 25.4, "right": 25.4, "gutter": 0,
    }, "ordinary_margins_not_frozen", issues)
    check(word.get("all_visible_text_color") == "#000000", "word_text_not_black", issues)
    check("table_of_contents" in word.get("all_visible_text_scope", []), "toc_black_scope_missing", issues)
    check("diagram_labels" in word.get("all_visible_text_scope", []), "diagram_black_scope_missing", issues)
    check(word.get("heading1_starts_new_page") is True, "chapter_page_break_not_required", issues)
    check(word.get("document_title_header_required") is True, "header_not_required", issues)
    check(word.get("page_number_required") is True, "page_number_not_required", issues)
    check(word.get("figures_embedded_in_relevant_chapter") is True, "chapter_figure_embedding_not_required", issues)
    check(word.get("mermaid_source_retained") is False, "mermaid_source_must_not_be_retained", issues)
    check(policy.get("page", {}).get("margins_mm") == word.get("ordinary_margins_mm"),
          "policy_margin_drift", issues)
    check(policy.get("typography", {}).get("all_visible_text_color") == word.get("all_visible_text_color"),
          "policy_text_color_drift", issues)
    check(policy.get("technical_diagrams", {}).get("label_text_color") == word.get("all_visible_text_color"),
          "policy_diagram_text_color_drift", issues)
    check(policy.get("pagination", {}).get("heading1_starts_new_page") is True,
          "policy_chapter_page_break_drift", issues)
    check(policy.get("figures", {}).get("embedded_in_relevant_chapter") is True,
          "policy_chapter_figure_embedding_drift", issues)
    toc = policy.get("table_of_contents", {})
    check((toc.get("minimum_level"), toc.get("maximum_level")) == (1, 2),
          "policy_toc_depth_drift", issues)

    deliverables = baseline.get("deliverables", {})
    technical = deliverables.get("technical-solution", {})
    feasibility = deliverables.get("feasibility", {})
    presentation = deliverables.get("presentation", {})
    check((technical.get("target_characters"), technical.get("minimum_characters"), technical.get("minimum_diagrams")) == (50000, 50000, 15),
          "technical_target_invalid", issues)
    check((feasibility.get("target_characters"), feasibility.get("minimum_characters"), feasibility.get("minimum_diagrams")) == (70000, 70000, 20),
          "feasibility_target_invalid", issues)
    check(technical.get("toc_levels") == [1, 2] and feasibility.get("toc_levels") == [1, 2],
          "two_level_toc_not_frozen", issues)
    check((presentation.get("slides"), presentation.get("visual_style")) == (5, "business_technology"),
          "presentation_target_invalid", issues)
    check(presentation.get("editable_native_objects_required") is True,
          "presentation_editability_not_required", issues)
    check("maximum_characters" not in technical and "maximum_characters" not in feasibility,
          "obsolete_maximum_characters", issues)
    check("test_version_targets" not in policy, "obsolete_test_version_targets", issues)
    policy_targets = policy.get("formal_version_targets", {})
    check(policy_targets.get("technical_solution") == {
        key: technical.get(key) for key in (
            "target_characters", "minimum_characters", "minimum_diagrams")
    }, "technical_policy_target_drift", issues)
    check(policy_targets.get("feasibility_report") == {
        key: feasibility.get(key) for key in (
            "target_characters", "minimum_characters", "minimum_diagrams")
    }, "feasibility_policy_target_drift", issues)
    check(policy_targets.get("presentation") == {
        key: presentation.get(key) for key in (
            "slides", "aspect_ratio", "visual_style", "minimum_native_charts",
            "minimum_native_connectors", "minimum_embedded_visual_assets",
            "editable_native_objects_required")
    }, "presentation_policy_target_drift", issues)

    ragflow = baseline.get("ragflow", {})
    check(ragflow.get("formal_mode") == "ragflow_required", "ragflow_formal_mode_invalid", issues)
    check(ragflow.get("formal_generation_requires_real_completed_snapshot") is True,
          "ragflow_real_snapshot_not_required", issues)
    check(ragflow.get("formal_mode_fail_closed") is True, "ragflow_not_fail_closed", issues)
    check(ragflow.get("preview_mode") == "source_only_preview", "preview_mode_invalid", issues)
    check(ragflow.get("preview_may_pass_formal_release_gate") is False,
          "preview_can_pass_formal_gate", issues)
    check(ragflow.get("fabricated_hits_prohibited") is True, "fabricated_hits_not_prohibited", issues)
    check(ragflow.get("internet_search_is_automatic_fallback") is False,
          "internet_search_fallback_must_be_disabled", issues)

    exclusion_ids = {item.get("id") for item in rules.get("exclusions", []) if isinstance(item, dict)}
    check({"short-test-length-targets", "legacy-figure-minimum", "workbuddy-runtime"} == exclusion_ids,
          "p1_legacy_exclusions_missing", issues)
    sys.path.insert(0, str(ROOT / "backend"))
    from portal.product_rules import ProductRulesUnavailable, _load_rules
    try:
        verified_rules, rules_digest = _load_rules()
        check(rules == verified_rules, "p1_rules_payload_mismatch", issues)
    except ProductRulesUnavailable:
        rules_digest = sha256(PACK.parent / "p1_rules.json")
        issues.append("p1_rules_integrity_failed")
    return {
        "passed": not issues,
        "issues": issues,
        "p1_rules_sha256": rules_digest,
        "note": "正式正文最低50,000/70,000字；历史v10仅为格式参考，不证明正式长文验收通过。",
    }


def validate_manifest() -> dict:
    issues: list[str] = []
    manifest_path = PACK / "manifest.json"
    manifest = load_json(manifest_path)
    files = manifest.get("files", [])
    for entry in files:
        relative = entry.get("path", "")
        target = (PACK / relative).resolve()
        if not target.is_relative_to(PACK.resolve()):
            issues.append(f"manifest_path_escape:{relative}")
            continue
        if not target.is_file():
            issues.append(f"manifest_file_missing:{relative}")
            continue
        if target.stat().st_size != entry.get("bytes"):
            issues.append(f"manifest_size_mismatch:{relative}")
        if sha256(target) != entry.get("sha256"):
            issues.append(f"manifest_hash_mismatch:{relative}")
    expected = {"assets/document-format-policy.json", "assets/delivery-baseline.json"}
    recorded = {entry.get("path") for entry in files}
    for missing in sorted(expected - recorded):
        issues.append(f"manifest_entry_missing:{missing}")
    return {
        "passed": not issues,
        "issues": issues,
        "manifest_sha256": sha256(manifest_path),
        "checked_files": len(files),
    }


def validate_artifacts(baseline: dict, artifact_root: Path | None = None) -> dict:
    historical = artifact_root is None
    accepted = baseline["historical_format_reference_artifacts_not_formal_length_acceptance"]
    artifact_root = (ROOT / accepted["root"]).resolve() if historical else artifact_root.resolve()
    if historical and not artifact_root.is_relative_to(ROOT.resolve()):
        return {"passed": False, "issues": ["historical_artifact_root_escape"]}
    deliverables = baseline["deliverables"]
    paths = {
        family: (artifact_root / deliverables[family]["filename"]).resolve()
        for family in ("technical-solution", "feasibility", "presentation")
    }
    identity_issues: list[str] = []
    for family, path in paths.items():
        if not path.is_relative_to(artifact_root):
            return {"passed": False, "issues": [f"artifact_path_escape:{family}"]}
        identity = accepted[family]
        if not path.is_file():
            identity_issues.append(f"accepted_artifact_missing:{family}")
            continue
        if historical and path.stat().st_size != identity["bytes"]:
            identity_issues.append(f"accepted_artifact_size_mismatch:{family}")
        if historical and sha256(path) != identity["sha256"]:
            identity_issues.append(f"accepted_artifact_hash_mismatch:{family}")

    technical = deliverables["technical-solution"]
    feasibility = deliverables["feasibility"]
    results = {
        "identity": {"passed": not identity_issues, "issues": identity_issues},
        "technical_solution": inspect_docx(
            paths["technical-solution"],
            minimum_characters=technical["minimum_characters"],
            minimum_figures=technical["minimum_diagrams"],
            expected_header="技术方案",
        ),
        "feasibility": inspect_docx(
            paths["feasibility"],
            minimum_characters=feasibility["minimum_characters"],
            minimum_figures=feasibility["minimum_diagrams"],
            expected_header="可行性研究报告",
        ),
        "presentation": inspect_pptx(paths["presentation"]),
    }
    results["passed"] = all(
        value.get("structural_passed", value["passed"]) if historical else value["passed"]
        for value in results.values()
    )
    results["acceptance_scope"] = "historical_format_reference_only" if historical else "actual_formal_output_length_and_structure"
    results["formal_output_accepted"] = not historical and results["passed"]
    results["business_content_and_visual_review"] = "not_evaluated"
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact-root", type=Path, help="Actual generated deliverables; omission never passes formal acceptance")
    args = parser.parse_args()
    baseline = load_json(args.baseline.resolve())
    policy = load_json(PACK / "assets" / baseline["format_policy"])
    rules = load_json(PACK.parent / "p1_rules.json")
    result = {
        "schema": "PRODUCT_DELIVERY_BASELINE_ACCEPTANCE_V1",
        "baseline_sha256": sha256(args.baseline.resolve()),
        "contract": validate_contract(baseline, policy, rules),
        "manifest": validate_manifest(),
        "historical_format_reference": validate_artifacts(baseline),
        "actual_formal_output_acceptance": validate_artifacts(baseline, args.artifact_root) if args.artifact_root else {
            "passed": False, "status": "not_evaluated", "issues": ["actual_formal_outputs_not_supplied"],
        },
        "external_calls": {"model": 0, "ragflow": 0, "network": 0},
    }
    result["structural_baseline_passed"] = all(result[key]["passed"] for key in (
        "contract", "manifest", "historical_format_reference"))
    result["passed"] = result["structural_baseline_passed"] and result["actual_formal_output_acceptance"]["passed"]
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output.resolve().write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
