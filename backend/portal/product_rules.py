import hashlib
import hmac
import json
import re
from pathlib import Path


_RULES_PATH = Path(__file__).resolve().parent / "product_assets" / "p1_rules.json"
_RULES_SHA256 = "7ed4d2eabea00402e63bfd26e85f5551e79e449e0ad4f859b150816d1edfec55"
_SCHEMA = "portal-product-p1-rules-v1"
_STAGES = {"blueprint", "write", "review"}
_SOURCE_IDS = {"intake", "technical-solution", "review", "plan"}
_EXCLUSION_IDS = {"legacy-length-targets", "legacy-figure-minimum", "workbuddy-runtime"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ProductRulesError(RuntimeError):
    pass


class ProductRulesUnavailable(ProductRulesError):
    pass


def _unavailable():
    raise ProductRulesUnavailable("product_rules_unavailable")


def _load_rules():
    try:
        raw = _RULES_PATH.read_bytes()
    except OSError:
        _unavailable()
    digest = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(digest, _RULES_SHA256):
        _unavailable()
    try:
        payload = json.loads(raw)
        if (type(payload) is not dict or set(payload) != {"schema", "source_repository", "sources", "stages", "exclusions"}
                or payload["schema"] != _SCHEMA or not isinstance(payload["source_repository"], str)
                or not payload["source_repository"].strip()):
            _unavailable()
        sources = payload["sources"]
        if not isinstance(sources, list) or len(sources) != len(_SOURCE_IDS):
            _unavailable()
        source_ids = set()
        source_paths = set()
        for source in sources:
            if type(source) is not dict or set(source) != {"id", "path", "sha256"}:
                _unavailable()
            if (source["id"] in source_ids or source["path"] in source_paths
                    or not isinstance(source["id"], str) or not isinstance(source["path"], str)
                    or not source["path"].strip() or not isinstance(source["sha256"], str)
                    or not _SHA256.fullmatch(source["sha256"])):
                _unavailable()
            source_ids.add(source["id"])
            source_paths.add(source["path"])
        if source_ids != _SOURCE_IDS or type(payload["stages"]) is not dict or set(payload["stages"]) != _STAGES:
            _unavailable()
        for stage in payload["stages"].values():
            if type(stage) is not dict or set(stage) != {"source_ids", "rules"}:
                _unavailable()
            used_sources = stage["source_ids"]
            rules = stage["rules"]
            if (not isinstance(used_sources, list) or not used_sources or len(used_sources) != len(set(used_sources))
                    or not set(used_sources) <= source_ids or not isinstance(rules, list) or not rules
                    or any(not isinstance(rule, str) or not rule.strip() for rule in rules)):
                _unavailable()
        exclusions = payload["exclusions"]
        if (not isinstance(exclusions, list) or len(exclusions) != len(_EXCLUSION_IDS)
                or any(type(item) is not dict or set(item) != {"id", "text"}
                       or not isinstance(item["id"], str) or not isinstance(item["text"], str)
                       or not item["text"].strip() for item in exclusions)
                or {item["id"] for item in exclusions} != _EXCLUSION_IDS):
            _unavailable()
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        _unavailable()
    return payload, digest


def rules_hash():
    return _load_rules()[1]


def stage_rules(route):
    payload, digest = _load_rules()
    if not isinstance(route, str) or route not in _STAGES:
        raise ProductRulesError("product_rules_stage_invalid")
    stage = payload["stages"][route]
    sources = {source["id"]: source for source in payload["sources"]}
    lines = [
        f"P1规则版本：{payload['schema']}",
        f"rules_hash：{digest}",
        f"适用阶段：{route}",
        "通用有据写作与审查约束：",
        *(f"{index}. {rule}" for index, rule in enumerate(stage["rules"], start=1)),
        "来源追溯：",
        *(f"- {sources[source_id]['path']} sha256={sources[source_id]['sha256']}" for source_id in stage["source_ids"]),
        "明确排除：",
        *(f"- {item['text']}" for item in payload["exclusions"]),
    ]
    return "\n".join(lines)
