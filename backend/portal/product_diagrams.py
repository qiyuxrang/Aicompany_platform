"""Runtime diagram planning and DOCX structural acceptance for product reports."""
from __future__ import annotations

import json
import re
from pathlib import Path
from zipfile import ZipFile


FAMILY_DIAGRAMS = {
    "technical-solution": [
        ("业务闭环流程", "从资料接收到方案交付的端到端业务闭环"),
        ("总体架构", "平台接入层、能力层、数据层与基础设施层的总体关系"),
        ("应用架构", "用户门户、业务服务、智能体服务与运营管理能力的协作关系"),
        ("技术架构", "前端、服务端、任务编排、模型服务和数据存储的技术分层"),
        ("部署架构", "访问区、应用区、数据区和运维区的部署与访问边界"),
        ("网络拓扑", "用户、边界防护、应用集群、数据服务和外部系统的连接拓扑"),
        ("数据流转", "资料上传、解析、生成、审核、归档与下载的数据流向"),
        ("集成架构", "平台与统一身份、知识库、模型网关和企业系统的集成方式"),
        ("安全架构", "身份、网络、应用、数据和审计五个安全域的防护关系"),
        ("身份与权限", "登录、首次改密、角色授权、访问校验和审计留痕的控制链"),
        ("日志审计", "业务日志、操作日志、模型调用日志和安全事件的汇聚分析流程"),
        ("高可用架构", "负载均衡、无状态应用、任务执行和数据服务的高可用设计"),
        ("备份与恢复", "数据备份、校验、异地保留、恢复演练和复盘改进流程"),
        ("监控告警", "指标采集、规则判断、分级通知、处置和闭环复盘流程"),
        ("发布与变更", "开发验证、审批、灰度发布、观察和回滚的受控变更流程"),
    ],
    "feasibility": [
        ("业务闭环流程", "从资料接收到方案交付的端到端业务闭环"),
        ("总体架构", "业务目标、平台能力、数据资源和基础设施之间的总体关系"),
        ("应用架构", "用户门户、业务服务、智能体服务与运营管理能力的协作关系"),
        ("技术架构", "前端、服务端、任务编排、模型服务和数据存储的技术分层"),
        ("部署架构", "访问区、应用区、数据区和运维区的部署与访问边界"),
        ("网络拓扑", "用户、边界防护、应用集群、数据服务和外部系统的连接拓扑"),
        ("数据流转", "资料上传、解析、生成、审核、归档与下载的数据流向"),
        ("集成架构", "平台与统一身份、知识库、模型网关和企业系统的集成方式"),
        ("安全架构", "身份、网络、应用、数据和审计五个安全域的防护关系"),
        ("身份与权限", "登录、首次改密、角色授权、访问校验和审计留痕的控制链"),
        ("日志审计", "业务日志、操作日志、模型调用日志和安全事件的汇聚分析流程"),
        ("高可用架构", "负载均衡、无状态应用、任务执行和数据服务的高可用设计"),
        ("备份与恢复", "数据备份、校验、异地保留、恢复演练和复盘改进流程"),
        ("监控告警", "指标采集、规则判断、分级通知、处置和闭环复盘流程"),
        ("发布与变更", "开发验证、审批、灰度发布、观察和回滚的受控变更流程"),
        ("可行性评价框架", "业务、技术、经济、实施和合规五个维度的综合评价关系"),
        ("建设范围边界", "本期建设、外部依赖、后续演进和明确排除项的边界关系"),
        ("干系人协同", "决策层、业务部门、建设团队、运维团队和供应方的协作机制"),
        ("决策门禁", "立项、方案、试点、上线和验收阶段的准入与退出条件"),
        ("效益实现路径", "能力建设、业务采用、过程改善、结果度量和持续优化的价值路径"),
    ],
}

SUBSECTION_TITLES = {
    "technical-solution": ("设计目标与约束", "方案组成与关键机制", "实施方法与控制要求", "运行保障与验收要点"),
    "feasibility": ("评价目标与分析边界", "现状依据与关键假设", "可行性论证与风险控制", "实施条件与决策建议"),
}


def minimum_diagrams(family: str) -> int:
    return 15 if family == "technical-solution" else 20


def add_structural_blocks(
    blocks: list[dict],
    family: str,
    sources: list[str],
    requirements: list[str],
    *,
    chapter_index: int = 0,
    chapter_count: int = 1,
) -> None:
    """Embed this chapter's mandatory diagrams without persisting Mermaid source.

    Diagram specifications are distributed round-robin across the report's real
    business chapters.  Consequently every figure remains below a business H1
    and the renderer never creates a detached, catch-all diagram chapter.
    """
    if chapter_count < 1 or not 0 <= chapter_index < chapter_count:
        raise ValueError("invalid diagram chapter allocation")
    for index, (title, description) in enumerate(FAMILY_DIAGRAMS[family], start=1):
        if (index - 1) % chapter_count != chapter_index:
            continue
        prefix = f"DIAGRAM_{index:02d}"
        blocks.extend([
            {"id": prefix + "_HEADING", "type": "heading", "level": 2, "text": title,
             "source_ids": sources, "requirement_ids": requirements},
            {"id": prefix + "_FIGURE", "type": "figure", "path": f"assets/diagram-{index:02d}.png",
             "caption": title, "width_mm": 145, "alt": description,
             "source_ids": sources, "requirement_ids": requirements},
            {"id": prefix + "_NOTE", "type": "paragraph", "text": description + "。图中颜色用于区分参与角色、平台能力、数据资源与控制节点，箭头表示主要依赖或处理方向。",
             "source_ids": sources, "requirement_ids": requirements},
        ])


def validate_document_requirements(document: dict) -> dict:
    family = document.get("family")
    if family not in FAMILY_DIAGRAMS:
        raise ValueError("unsupported report family")
    blocks = document.get("blocks", [])
    figures = [block for block in blocks if block.get("type") == "figure"]
    h1 = [block for block in blocks if block.get("type") == "heading" and block.get("level") == 1]
    h2 = [block for block in blocks if block.get("type") == "heading" and block.get("level") == 2]
    required = minimum_diagrams(family)
    if len(figures) < required:
        raise ValueError(f"diagram count below requirement: {len(figures)} < {required}")
    if not h1 or not h2:
        raise ValueError("reports require both level-one and level-two headings")
    if max((block.get("level", 0) for block in blocks if block.get("type") == "heading"), default=0) < 2:
        raise ValueError("table of contents must include level-two headings")
    paths = [block["path"] for block in figures]
    if len(paths) != len(set(paths)):
        raise ValueError("diagram paths must be unique")
    serialized = json.dumps(document, ensure_ascii=False)
    if re.search(r"(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)", serialized, re.I):
        raise ValueError("Mermaid source must not be persisted in report content")
    return {"figure_count": len(figures), "heading1_count": len(h1), "heading2_count": len(h2), "toc_depth": 2}


def inspect_docx_requirements(path: Path, expected: dict) -> dict:
    with ZipFile(path) as archive:
        names = archive.namelist()
        xml = b"".join(archive.read(name) for name in names if name.endswith(".xml"))
        media = [name for name in names if name.startswith("word/media/") and name.lower().endswith((".png", ".jpg", ".jpeg"))]
    # The company logo is also media, so the number must be at least the planned figure count.
    if len(media) < expected["figure_count"]:
        raise ValueError("rendered DOCX is missing diagram images")
    if re.search(rb"(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)", xml, re.I):
        raise ValueError("rendered DOCX contains Mermaid source")
    return {**expected, "embedded_raster_count": len(media), "mermaid_source_embedded": False}
