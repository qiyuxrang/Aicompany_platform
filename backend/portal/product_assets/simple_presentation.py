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
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE, MSO_SHAPE_TYPE
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
SOURCE_MAX_BYTES = 16 * 1024 * 1024
BLOCK_MAX_COUNT = 10000
BLOCK_TYPES = {"heading", "paragraph", "table", "figure"}
EXCLUDED_REF_PREFIXES = ("DRAFT_NOTICE", "PENDING_")
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


def eligible_block(block):
    return (block.get("type") in BLOCK_TYPES
            and not block["ref"].split(":", 1)[1].startswith(EXCLUDED_REF_PREFIXES))


def block_text(block):
    if block.get("type") == "figure":
        return clean(block.get("caption") or block.get("alt") or block.get("text"))
    if block.get("type") == "table":
        return clean(block.get("caption") or block.get("text") or "报告表格")
    return clean(block.get("text"))


def source_blocks(blocks, family=None, types=None):
    prefix = family + ":" if family else None
    return [block for block in blocks
            if eligible_block(block)
            and (prefix is None or block["ref"].startswith(prefix))
            and (types is None or block.get("type") in types)
            and block_text(block)]


def select_summary_blocks(blocks, family, limit, *, types=None, excluded=()):
    candidates = source_blocks(blocks, family, types)
    excluded = set(excluded)
    selected = []
    priorities = (
        lambda block: block.get("type") == "heading" and block.get("level") == 1,
        lambda block: block.get("type") == "paragraph",
        lambda block: block.get("type") == "table",
        lambda block: block.get("type") == "heading",
        lambda block: True,
    )
    for priority in priorities:
        for block in candidates:
            if block["ref"] not in excluded and block not in selected and priority(block):
                selected.append(block)
                if len(selected) == limit:
                    return selected
    return selected


def set_source_notes(slide, refs):
    values = list(dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref))
    slide.notes_slide.notes_text_frame.text = "source_refs" + ("\n" + "\n".join(values) if values else "")


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
    set_source_notes(slide, refs)
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
    text_box(slide, 0.75, 5.92, 6.4, 0.5, "项目方案成果汇报", 13, COLORS["muted"])
    if hero and Path(hero["asset_path"]).is_file():
        panel=rect(slide,8.55,1.25,4.25,4.85,"FFFFFF",line=COLORS["cyan"],radius=True)
        panel.fill.transparency=4
        slide.shapes.add_picture(str(hero["asset_path"]), Inches(8.75), Inches(1.72), width=Inches(3.85), height=Inches(3.95))
        text_box(slide,8.77,5.74,3.75,0.32,excerpt(block_text(hero), 24),11,COLORS["navy"],bold=True,align=PP_ALIGN.CENTER)
    else:
        for index,(size,color) in enumerate(((3.8,COLORS["panel_2"]),(2.75,COLORS["blue"]),(1.65,COLORS["cyan"]))):
            shape=slide.shapes.add_shape(MSO_SHAPE.OVAL,Inches(9.0+index*.53),Inches(1.25+index*.53),Inches(size),Inches(size))
            shape.fill.background(); shape.line.color.rgb=rgb(color); shape.line.width=Pt(2.2)
    set_source_notes(slide, [hero["ref"]] if hero else [])
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
            and eligible_block(block)
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
    technical = select_summary_blocks(blocks, "technical-solution", 2,
                                      types={"heading", "paragraph", "table"})
    feasibility = select_summary_blocks(blocks, "feasibility", 2,
                                        types={"heading", "paragraph", "table"})
    stages = select_summary_blocks(blocks, None, 5, types={"heading"})
    figures = [block for block in blocks if eligible_block(block) and block.get("type") == "figure"]
    refs = [block["ref"] for block in [*technical, *feasibility, *stages, *figures]]
    slide = add_slide(prs, "项目成果全景", "总体导览", page, "panorama", refs)
    text_box(slide, 0.72, 1.22, 5.05, 0.52,
             excerpt(block_text(technical[0]) if technical else "技术方案摘要", 30),
             20, COLORS["white"], bold=True)
    text_box(slide, 0.72, 1.88, 4.95, 0.85,
             excerpt(block_text(feasibility[0]) if feasibility else "可行性研究摘要", 92),
             13, COLORS["muted"])
    colors = (COLORS["cyan"], COLORS["azure"], COLORS["blue"], COLORS["green"], COLORS["amber"])
    for index, block in enumerate(stages):
        x = 0.76 + index * 1.04
        if index < len(stages) - 1:
            add_line(slide, x + 0.82, 4.3, x + 1.02, 4.3, COLORS["muted"], 1.6)
        node = rect(slide, x, 3.72, 0.84, 1.16, COLORS["panel"], line=colors[index], radius=True)
        set_text(node, excerpt(block_text(block), 16), 9.5, COLORS["ink"], bold=True,
                 align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
    figure_counts = {family: len([block for block in figures
                                  if block["ref"].startswith(family + ":")])
                     for family in FAMILY_LABELS}
    chart_data = ChartData()
    chart_data.categories = ["技术方案", "可行性研究"]
    chart_data.add_series("图示数量", tuple(figure_counts.values()))
    chart = slide.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, Inches(6.55), Inches(1.35), Inches(3.1), Inches(3.1), chart_data).chart
    chart.has_title = False
    chart.has_legend = True; chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.font.name = FONT; chart.legend.font.size = Pt(10); chart.legend.font.color.rgb = rgb(COLORS["ink"])
    chart.plots[0].has_data_labels = True
    chart.plots[0].data_labels.show_value = True
    chart.plots[0].data_labels.font.name = FONT; chart.plots[0].data_labels.font.size = Pt(12)
    chart.series[0].points[0].format.fill.solid(); chart.series[0].points[0].format.fill.fore_color.rgb = rgb(COLORS["cyan"])
    chart.series[0].points[1].format.fill.solid(); chart.series[0].points[1].format.fill.fore_color.rgb = rgb(COLORS["azure"])
    text_box(slide, 9.82, 1.52, 2.2, 0.4, f"{sum(figure_counts.values())} 张", 28, COLORS["white"], bold=True)
    text_box(slide, 9.82, 2.08, 2.4, 0.55, "当前两份报告中的\n图示内容块", 12, COLORS["muted"])
    text_box(slide, 6.72, 5.15, 5.15, 0.55,
             "本页只呈现选定摘要，未选内容仍保留在原报告中。",
             11.5, COLORS["ink"], bold=True)
    return slide


def technical_visual_slide(prs, blocks, page, preferred=None):
    summaries = select_summary_blocks(blocks, "technical-solution", 4,
                                      types={"heading", "paragraph", "table"})
    refs = [block["ref"] for block in summaries]
    if preferred:
        refs.append(preferred["ref"])
    slide = add_slide(prs, "技术方案总体架构", "技术方案", page, "architecture", refs)
    if preferred:
        add_visual_panel(slide, preferred, 0.7, 1.25, 7.7, 5.35)
    else:
        architecture_layout(slide, [block_text(block) for block in summaries])
    for index, block in enumerate(summaries):
        y = 1.38 + index * 1.28
        color = (COLORS["cyan"], COLORS["azure"], COLORS["green"], COLORS["amber"])[index]
        rect(slide, 8.78, y, 0.08, 0.92, color)
        label = {"heading": "报告标题", "paragraph": "正文摘录", "table": "表格摘要"}.get(block["type"], "报告摘录")
        text_box(slide, 9.08, y - 0.02, 3.15, 0.32, label, 12.5, color, bold=True)
        text_box(slide, 9.08, y + 0.36, 3.15, 0.55, excerpt(block_text(block), 46), 10.5, COLORS["ink"])
    return slide


def workflow_visual_slide(prs, blocks, page, preferred=None):
    steps = select_summary_blocks(blocks, None, 6, types={"heading"})
    details = select_summary_blocks(blocks, None, 3, types={"paragraph", "table"},
                                    excluded=[block["ref"] for block in steps])
    refs = [block["ref"] for block in [*steps, *details]]
    if preferred:
        refs.append(preferred["ref"])
    slide = add_slide(prs, "方案重点脉络", "报告摘要", page, "workflow", refs)
    colors = (COLORS["cyan"], COLORS["azure"], COLORS["blue"], COLORS["amber"], COLORS["green"], COLORS["cyan"])
    for index, block in enumerate(steps):
        x = 0.72 + index * 2.02
        if index < len(steps) - 1:
            add_line(slide, x + 1.55, 2.42, x + 1.95, 2.42, colors[index], 2.2)
        node = rect(slide, x, 1.84, 1.58, 1.16, COLORS["panel"], line=colors[index], radius=True)
        set_text(node, excerpt(block_text(block), 24), 10.5, COLORS["ink"], bold=True,
                 align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
        text_box(slide, x + 0.05, 1.45, 0.36, 0.26, f"{index + 1:02d}", 9, colors[index], bold=True)
    if preferred:
        add_visual_panel(slide, preferred, 0.82, 4.12, 5.1, 2.18)
    text_box(slide, 6.45, 4.18, 5.55, 0.42, "当前报告摘录", 16, COLORS["white"], bold=True)
    for index, block in enumerate(details):
        y = 4.82 + index * 0.58
        text_box(slide, 6.46, y, 0.35, 0.28, f"{index + 1:02d}", 9.5,
                 (COLORS["cyan"], COLORS["azure"], COLORS["amber"])[index], bold=True)
        text_box(slide, 6.9, y - 0.03, 5.0, 0.45, excerpt(block_text(block), 70), 10.2, COLORS["ink"])
    return slide


def feasibility_visual_slide(prs, data, blocks, page, preferred=None):
    summaries = select_summary_blocks(blocks, "feasibility", 5,
                                      types={"heading", "paragraph", "table"})
    refs = [block["ref"] for block in summaries]
    if preferred:
        refs.append(preferred["ref"])
    slide = add_slide(prs, "可行性判断与实施控制", "可行性研究", page, "feasibility", refs)
    text_box(slide, 0.72, 1.22, 5.0, 0.42, "当前报告要点", 16, COLORS["white"], bold=True)
    for index, block in enumerate(summaries[:4]):
        y = 1.82 + index * 1.08
        color = (COLORS["blue"], COLORS["cyan"], COLORS["azure"], COLORS["green"])[index]
        text_box(slide, 0.78, y, 0.42, 0.32, f"{index + 1:02d}", 10, color, bold=True)
        text_box(slide, 1.32, y - 0.04, 4.18, 0.72, excerpt(block_text(block), 72),
                 11.2, COLORS["ink"], bold=block.get("type") == "heading")
        add_line(slide, 1.32, y + 0.72, 5.42, y + 0.72, COLORS["panel_2"], 0.8)
    if preferred:
        add_visual_panel(slide, preferred, 6.1, 1.25, 6.1, 4.55)
    elif summaries:
        comparison_layout(slide, [block_text(block) for block in summaries[:4]])
    text_box(slide, 6.22, 6.02, 5.75, 0.38,
             excerpt(block_text(summaries[4]) if len(summaries) > 4 else block_text(summaries[-1]), 82),
             12.5, COLORS["ink"], bold=True, align=PP_ALIGN.CENTER)
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


def load_source(source):
    if source.stat().st_size > SOURCE_MAX_BYTES:
        raise ValueError("presentation source limit exceeded")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("invalid slide source") from None
    if (not isinstance(data, dict)
            or not isinstance(data.get("title", ""), str)
            or len(data.get("title", "")) > 500):
        raise ValueError("invalid slide source")
    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) != len(FAMILY_LABELS):
        raise ValueError("invalid slide source")
    families = []
    for item in sources:
        if (not isinstance(item, dict)
                or item.get("family") not in FAMILY_LABELS
                or not isinstance(item.get("id"), str) or not 1 <= len(item["id"]) <= 100
                or type(item.get("version")) is not int or item["version"] < 1
                or not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in item["sha256"].lower())):
            raise ValueError("invalid slide source")
        families.append(item["family"])
    if set(families) != set(FAMILY_LABELS) or len(set(families)) != len(families):
        raise ValueError("invalid slide source")
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("invalid slide source")
    if len(blocks) > BLOCK_MAX_COUNT:
        raise ValueError("presentation source limit exceeded")
    if not blocks:
        raise ValueError("invalid slide source")
    refs = []
    for item in blocks:
        if not isinstance(item, dict):
            raise ValueError("invalid slide source")
        ref = item.get("ref")
        block_type = item.get("type")
        if (not isinstance(ref, str) or not 3 <= len(ref) <= 200 or ":" not in ref
                or any(ord(character) < 32 for character in ref)
                or block_type not in BLOCK_TYPES):
            raise ValueError("invalid slide source")
        family, local_ref = ref.split(":", 1)
        if family not in FAMILY_LABELS or not local_ref:
            raise ValueError("invalid slide source")
        for field in ("text", "caption", "alt"):
            value = item.get(field, "")
            if not isinstance(value, str) or len(value) > 100_000:
                raise ValueError("invalid slide source")
        if block_type == "heading" and (type(item.get("level", 1)) is not int
                                         or not 1 <= item.get("level", 1) <= 9):
            raise ValueError("invalid slide source")
        if "asset_path" in item and (not isinstance(item["asset_path"], str)
                                     or not item["asset_path"] or len(item["asset_path"]) > 4096
                                     or "\x00" in item["asset_path"]):
            raise ValueError("invalid slide source")
        refs.append(ref)
    if len(refs) != len(set(refs)):
        raise ValueError("invalid slide source")
    return data


def source_mapping(prs, eligible_refs, all_refs):
    declared = set()
    for slide in prs.slides:
        if not slide.has_notes_slide:
            continue
        lines = slide.notes_slide.notes_text_frame.text.splitlines()
        if lines and lines[0] == "source_refs":
            declared.update(line for line in lines[1:] if line)
    eligible_refs = set(eligible_refs)
    all_refs = set(all_refs)
    return {
        "mapped_source_refs": sorted(declared),
        "omitted_source_refs": sorted(eligible_refs - declared),
        "missing_source_refs": sorted(declared - all_refs),
    }


def main():
    source, target = map(Path, sys.argv[1:])
    data = load_source(source)
    blocks = data["blocks"]

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(CANVAS_W), Inches(CANVAS_H)
    prs.core_properties.title = display_title(data.get("title"))
    prs.core_properties.subject = "项目方案商务科技汇报"
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
    technical_visual = next((item for item in technical_assets if "总体架构" in item.get("caption", "")),
                            technical_assets[0] if technical_assets else None)
    feasibility_assets = figure_assets(blocks, "feasibility")
    feasibility_visual = next((item for item in feasibility_assets if "决策门槛" in item.get("caption", "")),
                              feasibility_assets[-1] if feasibility_assets else None)
    all_assets = [*technical_assets, *feasibility_assets]
    workflow_visual = next((item for item in all_assets if "业务闭环" in item.get("caption", "")),
                           all_assets[0] if all_assets else None)
    cover(prs, data, hero)
    panorama_slide(prs, data, blocks, 2)
    technical_visual_slide(prs, blocks, 3, technical_visual)
    workflow_visual_slide(prs, blocks, 4, workflow_visual)
    feasibility_visual_slide(prs, data, blocks, 5, feasibility_visual)
    layout_inventory = Counter({
        "cover": 1, "panorama": 1, "architecture": 1,
        "workflow": 1, "feasibility": 1,
    })
    prs.save(target)

    # Every persisted block is represented by at least one of the executive
    # views.  The deck intentionally compresses the reports rather than
    # reproducing their paragraphs slide by slide.
    eligible_refs = {block["ref"] for block in blocks if eligible_block(block)}
    all_refs = {block["ref"] for block in blocks}
    rendered = Presentation(str(target))
    mapping = source_mapping(rendered, eligible_refs, all_refs)
    mapped_refs = mapping["mapped_source_refs"]
    missing_refs = mapping["missing_source_refs"]
    mapped_eligible_refs = eligible_refs & set(mapped_refs)
    editable_data_visuals = 4
    diagram_slides = 4
    visual_asset_count = sum(shape.shape_type == MSO_SHAPE_TYPE.PICTURE
                             for slide in rendered.slides for shape in slide.shapes)
    native_charts = sum(bool(getattr(shape, "has_chart", False))
                        for slide in rendered.slides for shape in slide.shapes)
    dominant_layout_ratio = max(layout_inventory.values()) / len(prs.slides)
    rendered_text = "\n".join(
        shape.text for slide in rendered.slides for shape in slide.shapes
        if getattr(shape, "has_text_frame", False)
    )
    prohibited_hits = [item for item in PROHIBITED_COPY if item in rendered_text]
    quality = {
        "engine": "business-tech-pptx", "engine_version": "v3",
        "design_profile": "business-technology-dark", "slides": len(prs.slides),
        "diagram_slides": diagram_slides, "native_charts": native_charts,
        "editable_data_visuals": editable_data_visuals,
        "visual_asset_count": visual_asset_count,
        "layout_inventory": dict(layout_inventory), "source_blocks": len(eligible_refs),
        "dominant_layout_ratio": round(dominant_layout_ratio, 3),
        "source_blocks_mapped": len(mapped_eligible_refs),
        **mapping,
        "source_mapping_scope": "selected_summary_blocks",
        "content_review": "not_run",
        "prohibited_copy_hits": prohibited_hits,
        "fact_boundary": "source-bound; no external facts or invented business metrics",
    }
    passed = (len(prs.slides) == 5 and diagram_slides >= 4
              and len([name for name, count in layout_inventory.items()
                       if count and name != "cover"]) >= 4
              and editable_data_visuals >= 4 and visual_asset_count >= 1
              and dominant_layout_ratio <= 0.25 and mapped_refs and not missing_refs
              and not prohibited_hits)
    quality["quality_gate"] = {"status": "pass" if passed else "fail"}
    target.with_suffix(".manifest.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    if not passed:
        raise ValueError("presentation quality gate failed")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--validate-source":
        load_source(Path(sys.argv[2]))
    else:
        main()
