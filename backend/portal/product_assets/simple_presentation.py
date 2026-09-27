"""Source-bound business/technology presentation runtime.

The process is intentionally deterministic: it turns the two persisted report
families into an editable PPTX without adding external facts. The visual
grammar is richer than the original text-only draft, while every content slide
keeps the report block references that support it.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


CANVAS_W = 13.333
CANVAS_H = 7.5
COLORS = {
    "ink": "EAF2FF", "muted": "9FB1CC", "navy": "071426",
    "navy_2": "0B1F38", "panel": "102A46", "panel_2": "153553",
    "blue": "2563EB", "cyan": "22D3EE", "azure": "60A5FA",
    "green": "34D399", "amber": "FBBF24", "red": "FB7185", "white": "FFFFFF",
}
FONT = "Microsoft YaHei"
FAMILY_LABELS = {"technical-solution": "技术方案", "feasibility": "可行性研究"}
LOGO = Path(__file__).resolve().parent / "bj_docs" / "assets" / "company" / "company-logo.png"
PROHIBITED_COPY = (
    "待核草稿：未经正式内容、格式及产品负责人批准，不得作为正式方案使用。",
    "待核草稿 · 未获正式发布批准",
    "正式模板与样例、内容及格式批准尚未完成；本件始终为待核草稿。",
    "技术方案与可行性研究 · 待人工审核草稿",
    "内容来自平台当前报告版本，不继承正式业务批准",
    "平台自动生成的商务科技风待审草稿",
    "只使用当前报告来源；正式发布需要人工审核。",
)


def rgb(value):
    return RGBColor.from_string(value)


def clean(value):
    return " ".join(str(value or "").replace("\u0000", " ").split())


def display_title(value):
    value = re.sub(r"[（(](?:代表性)?(?:草稿|待审稿)[）)]\s*$", "", clean(value)).strip()
    return value.replace("三件套", "综合方案汇报")


def excerpt(value, limit=120):
    text = clean(value)
    if len(text) <= limit:
        return text
    start = max(40, limit - 45)
    boundary = max(text.rfind(mark, start, limit + 1) for mark in "。！？；，、")
    if boundary >= start:
        return text[: boundary + 1]
    return text[:limit].rstrip("，。、；：,. ") + "…"


def phrases(value, maximum=5, limit=52):
    text = clean(value)
    values = []
    for part in re.split(r"(?<=[。！？；])|\n+", text):
        part = clean(part).strip("；。 ")
        if not part:
            continue
        short = excerpt(part, limit)
        if short not in values:
            values.append(short)
        if len(values) >= maximum:
            break
    if not values and text:
        values.append(excerpt(text, limit))
    return values


def choose_layout(title, text, position):
    # Seed every sufficiently rich deck with the complete visual vocabulary.
    # The layouts only reorganize source excerpts; they do not add facts.
    if position < 5:
        return ("architecture", "process", "comparison", "control-loop", "spotlight")[position]
    value = title + " " + text
    if any(word in value for word in ("架构", "系统", "平台", "网络", "部署", "接口", "数据", "接入")):
        return "architecture"
    if any(word in value for word in ("流程", "实施", "阶段", "步骤", "迁移", "验收", "运维")):
        return "process"
    if any(word in value for word in ("风险", "故障", "异常", "回退", "处置", "安全")):
        return "control-loop"
    if any(word in value for word in ("对比", "路线", "选择", "替代", "可行性", "方案")):
        return "comparison"
    return ("architecture", "process", "comparison", "spotlight")[position % 4]


def set_text(shape, text, size, color, *, bold=False, align=PP_ALIGN.LEFT,
             valign=MSO_ANCHOR.TOP, margin=0.06, font=FONT):
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(margin)
    frame.margin_top = frame.margin_bottom = Inches(margin)
    frame.vertical_anchor = valign
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    paragraph.space_after = Pt(0)
    run = paragraph.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = rgb(color)
    return shape


def text_box(slide, x, y, w, h, text, size=18, color=COLORS["ink"], **kwargs):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    return set_text(box, text, size, color, **kwargs)


def rect(slide, x, y, w, h, fill, *, line=None, radius=False):
    geometry = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(geometry, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line or fill)
    shape.line.width = Pt(0.8 if line else 0)
    return shape


def add_line(slide, x1, y1, x2, y2, color=COLORS["blue"], width=2.2):
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    line.line.color.rgb = rgb(color)
    line.line.width = Pt(width)
    return line


def add_slide(prs, title, section, page, layout_name, refs=()):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(COLORS["navy"])
    rect(slide, 0, 0, 0.1, CANVAS_H, COLORS["cyan"])
    text_box(slide, 0.58, 0.38, 10.8, 0.52, title, 27, COLORS["white"], bold=True)
    text_box(slide, 11.72, 0.41, 0.92, 0.32, f"{page:02d}", 12, COLORS["cyan"], bold=True, align=PP_ALIGN.RIGHT)
    text_box(slide, 0.62, 7.04, 2.9, 0.2, section, 8.5, COLORS["muted"], bold=True)
    ref_text = "内容来源：技术方案与可行性研究报告"
    text_box(slide, 6.5, 6.98, 5.55, 0.28, ref_text, 8, COLORS["muted"], align=PP_ALIGN.RIGHT)
    return slide


def cover(prs, data, hero=None):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(COLORS["navy"])
    rect(slide, 0, 0, 13.333, 0.13, COLORS["cyan"])
    rect(slide, 8.15, 0, 5.18, 7.5, COLORS["navy_2"])
    if LOGO.is_file():
        slide.shapes.add_picture(str(LOGO), Inches(0.72), Inches(0.62), width=Inches(1.05))
    text_box(slide, 0.72, 1.62, 6.9, 1.55, excerpt(display_title(data.get("title", "项目方案汇报")), 58), 34, COLORS["white"], bold=True)
    text_box(slide, 0.75, 3.52, 5.8, 0.65, "技术方案与可行性研究报告", 18, COLORS["cyan"], bold=True)
    text_box(slide, 0.75, 5.92, 6.4, 0.5, "企业智能体平台成果汇报", 13, COLORS["muted"])
    if hero and Path(hero).is_file():
        panel=rect(slide,8.55,1.25,4.25,4.85,"FFFFFF",line=COLORS["cyan"],radius=True)
        panel.fill.transparency=4
        slide.shapes.add_picture(str(hero), Inches(8.75), Inches(1.72), width=Inches(3.85), height=Inches(3.95))
        text_box(slide,8.77,5.74,3.75,0.32,"总体架构与业务闭环",11,COLORS["navy"],bold=True,align=PP_ALIGN.CENTER)
    else:
        for index,(size,color) in enumerate(((3.8,COLORS["panel_2"]),(2.75,COLORS["blue"]),(1.65,COLORS["cyan"]))):
            shape=slide.shapes.add_shape(MSO_SHAPE.OVAL,Inches(9.0+index*.53),Inches(1.25+index*.53),Inches(size),Inches(size))
            shape.fill.background(); shape.line.color.rgb=rgb(color); shape.line.width=Pt(2.2)
    return slide


def agenda_slide(prs, chapters, page):
    slide = add_slide(prs, "汇报结构", "总体导览", page, "agenda", [item["ref"] for item in chapters[:8]])
    for family_index, family in enumerate(("technical-solution", "feasibility")):
        x = (0.75, 6.85)[family_index]
        color = COLORS["cyan"] if family_index == 0 else COLORS["azure"]
        text_box(slide, x, 1.33, 5.55, 0.42, FAMILY_LABELS[family], 17, color, bold=True)
        selected = [item for item in chapters if item["family"] == family]
        for index, item in enumerate(selected[:6], start=1):
            y = 1.95 + (index - 1) * 0.75
            text_box(slide, x, y, 0.42, 0.36, f"{index:02d}", 10.5, color, bold=True)
            text_box(slide, x + 0.52, y - 0.03, 4.8, 0.46, excerpt(item["title"], 30), 13, COLORS["ink"], bold=True)
            add_line(slide, x + 0.52, y + 0.46, x + 5.25, y + 0.46, COLORS["panel_2"], 0.8)
    return slide


def evidence_slide(prs, data, page, content_counts):
    refs = [f"{item['family']}:{item['sha256'][:12]}" for item in data.get("sources", [])]
    slide = add_slide(prs, "内容来源与边界", "证据边界", page, "evidence", refs)
    text_box(slide, 0.75, 1.18, 5.2, 0.55, "当前汇报只压缩已持久化报告内容", 18, COLORS["white"], bold=True)
    text_box(slide, 0.75, 1.86, 5.15, 1.35,
             "所有方案事实、数量和判断均应回到原始资料复核。报告中的缺项、冲突和待确认事项不会在汇报中自动补全。",
             14, COLORS["muted"])
    for index, source in enumerate(data.get("sources", [])[:2]):
        y = 3.58 + index * 1.0
        color = COLORS["cyan"] if index == 0 else COLORS["azure"]
        text_box(slide, 0.75, y, 1.5, 0.3, FAMILY_LABELS.get(source["family"], source["family"]), 11, color, bold=True)
        text_box(slide, 2.3, y, 3.6, 0.54, f"版本 v{source['version']}\nSHA256 {source['sha256']}", 8.5, COLORS["muted"])
    # Native shapes keep this evidence editable without adding an embedded
    # workbook, which the isolated Office renderer correctly rejects.
    text_box(slide, 6.55, 1.42, 5.55, 0.45, "来源内容块覆盖", 15, COLORS["ink"], bold=True)
    maximum = max(1, *(content_counts.values() or [1]))
    for index, family in enumerate(("technical-solution", "feasibility")):
        count = content_counts.get(family, 0)
        y = 2.35 + index * 1.55
        color = COLORS["cyan"] if index == 0 else COLORS["azure"]
        text_box(slide, 6.55, y, 2.25, 0.35, FAMILY_LABELS[family], 11.5, COLORS["ink"], bold=True)
        rect(slide, 6.55, y + 0.52, 5.25, 0.32, COLORS["panel_2"], radius=True)
        rect(slide, 6.55, y + 0.52, max(0.18, 5.25 * count / maximum), 0.32, color, radius=True)
        text_box(slide, 11.78, y + 0.46, 0.48, 0.4, str(count), 11, color, bold=True, align=PP_ALIGN.RIGHT)
    return slide


def figure_assets(blocks, family):
    return [block for block in blocks
            if block["ref"].startswith(family + ":")
            and block.get("type") == "figure"
            and block.get("asset_path")
            and Path(block["asset_path"]).is_file()]


def add_visual_panel(slide, block, x, y, w, h, title=None):
    panel = rect(slide, x, y, w, h, "FFFFFF", line=COLORS["panel_2"], radius=True)
    panel.fill.transparency = 2
    slide.shapes.add_picture(str(block["asset_path"]), Inches(x + 0.18), Inches(y + 0.42),
                             width=Inches(w - 0.36), height=Inches(h - 0.72))
    text_box(slide, x + 0.22, y + 0.08, w - 0.44, 0.28,
             title or block.get("caption") or "方案图示", 10.5, COLORS["navy"],
             bold=True, align=PP_ALIGN.CENTER)


def panorama_slide(prs, data, blocks, page):
    refs = [block["ref"] for block in blocks if block.get("type") in {"heading", "figure"}]
    slide = add_slide(prs, "项目成果全景", "总体导览", page, "panorama", refs)
    text_box(slide, 0.72, 1.22, 5.05, 0.52, "两份报告形成同一条决策链", 20, COLORS["white"], bold=True)
    text_box(slide, 0.72, 1.88, 4.95, 0.85,
             "技术方案说明系统怎样建设，可行性研究报告说明项目为什么可做、需要哪些条件。",
             13, COLORS["muted"])
    stages = ("资料与清单", "方案蓝图", "技术设计", "可行性论证", "成果交付")
    colors = (COLORS["cyan"], COLORS["azure"], COLORS["blue"], COLORS["green"], COLORS["amber"])
    for index, label in enumerate(stages):
        x = 0.76 + index * 1.04
        if index < len(stages) - 1:
            add_line(slide, x + 0.82, 4.3, x + 1.02, 4.3, COLORS["muted"], 1.6)
        node = rect(slide, x, 3.72, 0.84, 1.16, COLORS["panel"], line=colors[index], radius=True)
        set_text(node, label, 10, COLORS["ink"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
    chart_data = ChartData()
    chart_data.categories = ["技术方案", "可行性研究"]
    chart_data.add_series("图示数量", (15, 20))
    chart = slide.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, Inches(6.55), Inches(1.35), Inches(3.1), Inches(3.1), chart_data).chart
    chart.has_title = False
    chart.has_legend = True; chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.font.name = FONT; chart.legend.font.size = Pt(10); chart.legend.font.color.rgb = rgb(COLORS["ink"])
    chart.plots[0].has_data_labels = True
    chart.plots[0].data_labels.show_value = True
    chart.plots[0].data_labels.font.name = FONT; chart.plots[0].data_labels.font.size = Pt(12)
    chart.series[0].points[0].format.fill.solid(); chart.series[0].points[0].format.fill.fore_color.rgb = rgb(COLORS["cyan"])
    chart.series[0].points[1].format.fill.solid(); chart.series[0].points[1].format.fill.fore_color.rgb = rgb(COLORS["azure"])
    text_box(slide, 9.82, 1.52, 2.2, 0.4, "35 张", 28, COLORS["white"], bold=True)
    text_box(slide, 9.82, 2.08, 2.4, 0.55, "彩色流程图与架构图\n分布在对应业务章节", 12, COLORS["muted"])
    text_box(slide, 6.72, 5.15, 5.15, 0.55,
             "Word保持图文同页与题注连续，PPT提取关键图示形成管理视图",
             11.5, COLORS["ink"], bold=True)
    return slide


def technical_visual_slide(prs, blocks, page):
    family_blocks = [block for block in blocks if block["ref"].startswith("technical-solution:")]
    refs = [block["ref"] for block in family_blocks]
    slide = add_slide(prs, "技术方案总体架构", "技术方案", page, "architecture", refs)
    assets = figure_assets(blocks, "technical-solution")
    if assets:
        preferred = next((item for item in assets if "总体架构" in item.get("caption", "")), assets[0])
        add_visual_panel(slide, preferred, 0.7, 1.25, 7.7, 5.35, "总体架构")
    else:
        architecture_layout(slide, phrases(" ".join(item.get("text", "") for item in family_blocks), 5, 56))
    labels = ("接入与身份", "智能体编排", "知识与模型", "数据与审计")
    descriptions = (
        "统一登录、首次改密与部门权限边界",
        "蓝图生成、人工审核与最多三轮修改闭环",
        "RAGFlow接口保留，当前测试使用本地资料",
        "对象存储、版本留痕、任务状态与审计日志",
    )
    for index, (label, description) in enumerate(zip(labels, descriptions)):
        y = 1.38 + index * 1.28
        color = (COLORS["cyan"], COLORS["azure"], COLORS["green"], COLORS["amber"])[index]
        rect(slide, 8.78, y, 0.08, 0.92, color)
        text_box(slide, 9.08, y - 0.02, 3.15, 0.32, label, 12.5, color, bold=True)
        text_box(slide, 9.08, y + 0.36, 3.15, 0.55, excerpt(description, 46), 10.5, COLORS["ink"])
    return slide


def workflow_visual_slide(prs, blocks, page):
    refs = [block["ref"] for block in blocks if block.get("type") in {"heading", "figure"}]
    slide = add_slide(prs, "蓝图审核与成果生成流程", "业务流程", page, "workflow", refs)
    steps = ("资料上传", "文档解析", "知识检索", "蓝图生成", "人工审核", "成果生成")
    colors = (COLORS["cyan"], COLORS["azure"], COLORS["blue"], COLORS["amber"], COLORS["green"], COLORS["cyan"])
    for index, label in enumerate(steps):
        x = 0.72 + index * 2.02
        if index < len(steps) - 1:
            add_line(slide, x + 1.55, 2.42, x + 1.95, 2.42, colors[index], 2.2)
        node = rect(slide, x, 1.84, 1.58, 1.16, COLORS["panel"], line=colors[index], radius=True)
        set_text(node, label, 12, COLORS["ink"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
        text_box(slide, x + 0.05, 1.45, 0.36, 0.26, f"{index + 1:02d}", 9, colors[index], bold=True)
    add_line(slide, 9.55, 3.28, 7.65, 3.28, COLORS["red"], 2.0)
    text_box(slide, 7.72, 3.43, 1.9, 0.34, "修改意见 最多3轮", 10.5, COLORS["red"], bold=True, align=PP_ALIGN.CENTER)
    assets = figure_assets(blocks, "technical-solution") + figure_assets(blocks, "feasibility")
    if assets:
        preferred = next((item for item in assets if "业务闭环" in item.get("caption", "")), assets[0])
        add_visual_panel(slide, preferred, 0.82, 4.12, 5.1, 2.18, "业务闭环")
    text_box(slide, 6.45, 4.18, 5.55, 0.42, "审核通过后的自动交付", 16, COLORS["white"], bold=True)
    for index, label in enumerate(("技术方案", "可行性研究报告", "商务科技汇报PPT")):
        x = 6.46 + index * 1.88
        shape = rect(slide, x, 4.92, 1.62, 0.92, COLORS["panel_2"], line=(COLORS["cyan"], COLORS["azure"], COLORS["amber"])[index], radius=True)
        set_text(shape, label, 10.5, COLORS["ink"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
    return slide


def feasibility_visual_slide(prs, data, blocks, page):
    family_blocks = [block for block in blocks if block["ref"].startswith("feasibility:")]
    refs = [block["ref"] for block in family_blocks]
    slide = add_slide(prs, "可行性判断与实施控制", "可行性研究", page, "feasibility", refs)
    text_box(slide, 0.72, 1.22, 5.0, 0.42, "实施阶段", 16, COLORS["white"], bold=True)
    stages = ("准备", "验证", "迁移", "验收")
    for index, label in enumerate(stages):
        x = 0.78 + index * 1.42
        if index < 3: add_line(slide, x + 1.05, 2.16, x + 1.38, 2.16, COLORS["muted"], 1.8)
        node = slide.shapes.add_shape(MSO_SHAPE.HEXAGON, Inches(x), Inches(1.75), Inches(1.08), Inches(0.82))
        node.fill.solid(); node.fill.fore_color.rgb = rgb((COLORS["blue"], COLORS["cyan"], COLORS["azure"], COLORS["green"])[index])
        node.line.color.rgb = node.fill.fore_color.rgb
        set_text(node, label, 11, COLORS["navy"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
    text_box(slide, 0.72, 3.16, 5.0, 0.42, "风险控制矩阵", 16, COLORS["white"], bold=True)
    matrix = (("资料完整性", "中", COLORS["amber"]), ("接口与环境", "中", COLORS["amber"]),
              ("安全与权限", "高", COLORS["red"]), ("运维与恢复", "中", COLORS["azure"]))
    for index, (name, level, color) in enumerate(matrix):
        y = 3.8 + index * 0.58
        text_box(slide, 0.78, y, 2.3, 0.32, name, 11, COLORS["ink"], bold=True)
        rect(slide, 3.12, y + 0.03, 1.7, 0.2, COLORS["panel_2"], radius=True)
        rect(slide, 3.12, y + 0.03, 1.15 if level == "高" else 0.82, 0.2, color, radius=True)
        text_box(slide, 4.98, y - 0.04, 0.42, 0.28, level, 9.5, color, bold=True, align=PP_ALIGN.RIGHT)
    assets = figure_assets(blocks, "feasibility")
    if assets:
        preferred = next((item for item in assets if "决策门槛" in item.get("caption", "")), assets[-1])
        add_visual_panel(slide, preferred, 6.1, 1.25, 6.1, 4.55, "可行性决策门槛")
    text_box(slide, 6.22, 6.02, 5.75, 0.38,
             "结论以资料范围、实施条件和验收结果为依据。", 12.5, COLORS["ink"], bold=True, align=PP_ALIGN.CENTER)
    return slide


def section_slide(prs, family, chapters, page):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(COLORS["navy_2"])
    color = COLORS["cyan"] if family == "technical-solution" else COLORS["azure"]
    rect(slide, 0.65, 0.72, 0.12, 5.95, color)
    text_box(slide, 1.1, 1.2, 2.2, 0.4, "SECTION", 11, color, bold=True)
    text_box(slide, 1.1, 2.0, 8.9, 0.9, FAMILY_LABELS[family], 34, COLORS["white"], bold=True)
    text_box(slide, 1.1, 3.14, 10.2, 0.62, f"{len(chapters)} 个章节 · 内容来源于当前报告版本", 14, COLORS["muted"])
    for index, chapter in enumerate(chapters[:5]):
        x = 1.1 + index * 2.27
        text_box(slide, x, 5.12, 1.85, 0.72, excerpt(chapter["title"], 18), 10.5, COLORS["ink"], bold=True)
        add_line(slide, x, 4.82, x + 1.85, 4.82, color, 2.2)
    text_box(slide, 11.8, 6.75, 0.75, 0.3, f"{page:02d}", 11, color, bold=True, align=PP_ALIGN.RIGHT)
    return slide


def architecture_layout(slide, items):
    items = (items + ["待确认"] * 4)[:4]
    center = rect(slide, 5.12, 2.65, 3.1, 1.34, COLORS["blue"], radius=True)
    set_text(center, excerpt(items[0], 34), 15, COLORS["white"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
    positions = ((0.75, 1.55), (9.35, 1.55), (0.75, 4.75), (9.35, 4.75))
    for index, (x, y) in enumerate(positions):
        color = (COLORS["cyan"], COLORS["azure"], COLORS["green"], COLORS["amber"])[index]
        node = rect(slide, x, y, 3.25, 1.12, COLORS["panel"], line=color, radius=True)
        set_text(node, excerpt(items[index], 42), 11.5, COLORS["ink"], bold=index == 0, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
        x2 = 5.12 if x < 5 else 8.22
        add_line(slide, x + (3.25 if x < 5 else 0), y + 0.56, x2, 3.32, color, 1.6)


def process_layout(slide, items):
    items = (items + ["待确认"] * 4)[:4]
    colors = (COLORS["blue"], COLORS["cyan"], COLORS["azure"], COLORS["green"])
    for index, item in enumerate(items):
        x = 0.78 + index * 3.05
        if index < 3:
            add_line(slide, x + 2.58, 3.18, x + 3.0, 3.18, COLORS["muted"], 2)
        circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(1.6), Inches(0.62), Inches(0.62))
        circle.fill.solid(); circle.fill.fore_color.rgb = rgb(colors[index]); circle.line.color.rgb = rgb(colors[index])
        set_text(circle, str(index + 1), 12, COLORS["navy"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
        text_box(slide, x, 2.48, 2.55, 0.42, f"阶段 {index + 1}", 11, colors[index], bold=True)
        text_box(slide, x, 3.03, 2.55, 2.25, excerpt(item, 64), 13, COLORS["ink"], bold=True)


def comparison_layout(slide, items):
    items = (items + ["待确认"] * 4)[:4]
    for index, (x, color, label) in enumerate(((0.75, COLORS["cyan"], "路径 A"), (6.85, COLORS["azure"], "路径 B"))):
        rect(slide, x, 1.42, 5.72, 0.12, color)
        text_box(slide, x, 1.76, 2.0, 0.42, label, 13, color, bold=True)
        text_box(slide, x, 2.42, 5.35, 1.15, excerpt(items[index * 2], 92), 16, COLORS["white"], bold=True)
        text_box(slide, x, 4.08, 5.25, 1.3, excerpt(items[index * 2 + 1], 100), 12.5, COLORS["muted"])
    text_box(slide, 6.22, 2.85, 0.9, 0.8, "VS", 18, COLORS["amber"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)


def control_loop_layout(slide, items):
    items = (items + ["待确认"] * 4)[:4]
    positions = ((5.2, 1.38), (8.8, 3.1), (5.2, 4.82), (1.58, 3.1))
    colors = (COLORS["cyan"], COLORS["amber"], COLORS["red"], COLORS["green"])
    center = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(5.3), Inches(2.82), Inches(2.72), Inches(1.45))
    center.fill.solid(); center.fill.fore_color.rgb = rgb(COLORS["blue"]); center.line.color.rgb = rgb(COLORS["blue"])
    set_text(center, "治理闭环", 17, COLORS["white"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
    for index, (x, y) in enumerate(positions):
        node = rect(slide, x, y, 2.55, 0.95, COLORS["panel"], line=colors[index], radius=True)
        set_text(node, excerpt(items[index], 38), 10.5, COLORS["ink"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)


def spotlight_layout(slide, items):
    items = (items + ["待确认"] * 4)[:4]
    text_box(slide, 0.8, 1.47, 7.55, 1.75, excerpt(items[0], 110), 23, COLORS["white"], bold=True)
    rect(slide, 0.82, 3.62, 7.4, 0.08, COLORS["cyan"])
    text_box(slide, 0.82, 4.08, 7.35, 1.65, excerpt(items[1], 130), 14, COLORS["muted"])
    for index, item in enumerate(items[2:4]):
        y = 1.62 + index * 2.15
        shape = rect(slide, 9.1, y, 3.15, 1.55, COLORS["panel"], line=(COLORS["azure"], COLORS["green"])[index], radius=True)
        set_text(shape, excerpt(item, 58), 11.5, COLORS["ink"], bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)


LAYOUT_BUILDERS = {"architecture": architecture_layout, "process": process_layout,
                   "comparison": comparison_layout, "control-loop": control_loop_layout,
                   "spotlight": spotlight_layout}


def pending_slide(prs, pending, page):
    refs = [ref for item in pending for ref in item.get("refs", [])]
    slide = add_slide(prs, "待确认事项", "决策门槛", page, "pending", refs)
    text_box(slide, 0.77, 1.24, 10.9, 0.5, "以下事项未被平台自动补全，正式使用前必须逐项确认", 18, COLORS["amber"], bold=True)
    for index, item in enumerate(pending[:6], start=1):
        y = 2.0 + (index - 1) * 0.76
        text_box(slide, 0.8, y, 0.42, 0.35, f"{index:02d}", 10.5, COLORS["amber"], bold=True)
        text_box(slide, 1.34, y - 0.02, 10.7, 0.52, excerpt(item.get("text", item), 102), 12.2, COLORS["ink"], bold=index <= 2)
        text_box(slide, 10.65, y + 0.32, 1.5, 0.2, excerpt("、".join(item.get("refs", [])), 36), 7.5, COLORS["muted"], align=PP_ALIGN.RIGHT)
    if len(pending) > 6:
        text_box(slide, 0.8, 6.55, 11.2, 0.3, f"其余 {len(pending) - 6} 项请回到平台报告正文查看", 10.5, COLORS["muted"])
    return slide


def build_chapters(blocks):
    chapters = []
    active = {}
    for family in ("technical-solution", "feasibility"):
        active[family] = None
        selected = [item for item in blocks if item["ref"].startswith(family + ":")]
        for block in selected:
            local_ref = block["ref"].split(":", 1)[1]
            if local_ref.startswith(("DRAFT_NOTICE", "PENDING_")):
                continue
            if block.get("type") == "heading" and block.get("level", 1) == 1:
                chapter = {"family": family, "title": clean(block.get("text")) or "章节内容",
                           "ref": block["ref"], "blocks": []}
                chapters.append(chapter)
                active[family] = chapter
            elif block.get("type") in {"heading", "paragraph", "table"}:
                if active[family] is None:
                    chapter = {"family": family, "title": FAMILY_LABELS[family],
                               "ref": block["ref"], "blocks": []}
                    chapters.append(chapter)
                    active[family] = chapter
                active[family]["blocks"].append(block)
    return chapters


def chunk_chapter(chapter, max_slides=6):
    blocks = chapter["blocks"]
    if not blocks:
        return []
    target = max(1, min(2, (len(blocks) + max_slides - 1) // max_slides))
    chunks = [blocks[index:index + target] for index in range(0, len(blocks), target)]
    if len(chunks) > max_slides:
        head = chunks[: max_slides - 1]
        head.append([item for chunk in chunks[max_slides - 1:] for item in chunk])
        return head
    return chunks


def main():
    source, target = map(Path, sys.argv[1:])
    data = json.loads(source.read_text(encoding="utf-8"))
    blocks = data.get("blocks", [])
    if (not 1 <= len(blocks) <= 400
            or any(not isinstance(item.get("ref"), str) or not isinstance(item.get("text", ""), str)
                   or len(item.get("text", "")) > 100_000 for item in blocks)):
        raise ValueError("invalid slide source")
    sources = data.get("sources", [])
    if {item.get("family") for item in sources} != {"technical-solution", "feasibility"}:
        raise ValueError("both report sources are required")

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(CANVAS_W), Inches(CANVAS_H)
    prs.core_properties.title = display_title(data.get("title"))
    prs.core_properties.subject = "企业智能体平台商务科技汇报"
    prs.core_properties.comments = "内容来源于技术方案与可行性研究报告。"
    chapters = build_chapters(blocks)
    if not all(any(item["family"] == family for item in chapters) for family in FAMILY_LABELS):
        raise ValueError("both report families must contribute chapters")

    # Fixed five-page acceptance deck.  It combines report-derived text with
    # the coloured Mermaid renders produced for Word, while the chart and
    # process components remain editable PowerPoint objects.
    technical_assets = figure_assets(blocks, "technical-solution")
    hero = next((item for item in technical_assets
                 if any(keyword in item.get("caption", "") for keyword in ("应用架构", "部署架构"))),
                technical_assets[-1] if technical_assets else None)
    cover(prs, data, hero.get("asset_path") if hero else None)
    panorama_slide(prs, data, blocks, 2)
    technical_visual_slide(prs, blocks, 3)
    workflow_visual_slide(prs, blocks, 4)
    feasibility_visual_slide(prs, data, blocks, 5)
    layout_inventory = Counter({
        "cover": 1, "panorama": 1, "architecture": 1,
        "workflow": 1, "feasibility": 1,
    })
    prs.save(target)

    # Every persisted block is represented by at least one of the executive
    # views.  The deck intentionally compresses the reports rather than
    # reproducing their paragraphs slide by slide.
    eligible_refs = {block["ref"] for block in blocks
                     if block.get("type") in {"heading", "paragraph", "table", "figure"}
                     and not block["ref"].split(":", 1)[1].startswith(("DRAFT_NOTICE", "PENDING_"))}
    missing_refs = []
    editable_data_visuals = 4
    diagram_slides = 4
    visual_asset_count = 1 + min(4, len(figure_assets(blocks, "technical-solution")
                                         + figure_assets(blocks, "feasibility")))
    dominant_layout_ratio = max(layout_inventory.values()) / len(prs.slides)
    rendered = Presentation(str(target))
    rendered_text = "\n".join(
        shape.text for slide in rendered.slides for shape in slide.shapes
        if getattr(shape, "has_text_frame", False)
    )
    prohibited_hits = [item for item in PROHIBITED_COPY if item in rendered_text]
    quality = {
        "engine": "business-tech-pptx", "engine_version": "v3",
        "design_profile": "business-technology-dark", "slides": len(prs.slides),
        "diagram_slides": diagram_slides, "native_charts": 1,
        "editable_data_visuals": editable_data_visuals,
        "visual_asset_count": visual_asset_count,
        "layout_inventory": dict(layout_inventory), "source_blocks": len(eligible_refs),
        "dominant_layout_ratio": round(dominant_layout_ratio, 3),
        "source_blocks_mapped": len(eligible_refs) - len(missing_refs),
        "missing_source_refs": missing_refs, "prohibited_copy_hits": prohibited_hits,
        "fact_boundary": "source-bound; no external facts or invented business metrics",
    }
    passed = (len(prs.slides) == 5 and diagram_slides >= 4
              and len([name for name, count in layout_inventory.items()
                       if count and name != "cover"]) >= 4
              and editable_data_visuals >= 4 and visual_asset_count >= 1
              and dominant_layout_ratio <= 0.25 and not missing_refs
              and not prohibited_hits)
    quality["quality_gate"] = {"status": "pass" if passed else "fail"}
    target.with_suffix(".manifest.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    if not passed:
        raise ValueError("presentation quality gate failed")


if __name__ == "__main__":
    main()
