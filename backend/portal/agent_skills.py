import hashlib
import json

from .agent_models import AgentSkillInstallation


BUILTIN_SKILLS = {
    "source-check": ("来源核对", "1", """# 来源核对
先列出当前问题的资料来源、对象标识和版本，再区分原文事实、推断、冲突与缺项。仅引用当前获准资料；缺少依据写待补，不能将未知写为通过。来源撤权或版本变化时重新核对。
"""),
    "deliverable-review": ("成果检查", "1", """# 成果检查
检查成果是否回应当前要求版本，逐项核对事实、来源、关键数字、文档间一致性和可打开的产物版本。发现缺口或矛盾时列明位置与影响，不补造数据，不替代人操作确认或发布。
"""),
}


def reviewed_catalog():
    return {skill_id: {"id": skill_id, "name": name, "version": version,
                       "digest": hashlib.sha256(source.encode("utf-8")).hexdigest()}
            for skill_id, (name, version, source) in BUILTIN_SKILLS.items()}


def approved_skill_source(skill_id, digest):
    item = reviewed_catalog().get(skill_id)
    if item is None or item["digest"] != digest:
        return None
    return BUILTIN_SKILLS[skill_id][2]


def skill_bundle(owner):
    catalog = reviewed_catalog()
    versions = []
    files = {}
    for installation in AgentSkillInstallation.objects.filter(owner=owner, enabled=True).order_by("skill_id"):
        item = catalog.get(installation.skill_id)
        if item is None or (installation.version, installation.digest) != (item["version"], item["digest"]):
            continue
        versions.append({"id": item["id"], "version": item["version"], "digest": item["digest"]})
        files[f"/skills/{item['id']}/SKILL.md"] = BUILTIN_SKILLS[item["id"]][2]
    if not versions:
        return {"digest": None, "versions": [], "files": {}}
    canonical = json.dumps(versions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "versions": versions, "files": files}
