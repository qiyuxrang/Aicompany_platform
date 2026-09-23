"""Isolated document-runtime entry: persisted pair JSON -> editable draft slides."""

import json
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt


def main():
    source, target = map(Path, sys.argv[1:])
    data = json.loads(source.read_text(encoding="utf-8"))
    blocks = data["blocks"]
    if len(blocks) > 200 or any(not isinstance(item.get("ref"), str) or not isinstance(item.get("text"), str)
                                or len(item["text"]) > 3000 for item in blocks):
        raise ValueError("invalid slide source")
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(13.333), Inches(7.5)
    slides = [(data["title"] + " · 待人工审核草稿", "简版内容草稿引擎；非 PPT Master", "两份报告的精确内容版本")]
    for family, title in (("technical-solution", "技术方案"), ("feasibility", "可行性研究")):
        selected = [item for item in blocks if item["ref"].startswith(family + ":") and item["type"] == "paragraph"]
        for index in range(0, len(selected), 3):
            chunk = selected[index:index + 3]
            text = "\n\n".join(item["text"][:300] for item in chunk)
            refs = "来源：" + "、".join(item["ref"] for item in chunk)
            slides.append((f"{title} · {index // 3 + 1}", text, refs))
    if len(slides) < 3:
        raise ValueError("both report families must contribute slides")
    if data.get("pending"):
        slides.append(("待确认事项", "\n".join(str(value.get("text", value))[:120] for value in data["pending"][:8]), "来源：双报告 pending"))
    for heading, body, citation in slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = RGBColor(244, 247, 251)
        for text, y, size, color in ((heading, .6, 32, RGBColor(23, 54, 93)),
                                     (body, 1.7, 21, RGBColor(23, 43, 77)),
                                     (citation, 6.7, 12, RGBColor(70, 80, 95))):
            box = slide.shapes.add_textbox(Inches(.8), Inches(y), Inches(11.7), Inches(4.7 if y == 1.7 else .65))
            frame = box.text_frame
            frame.word_wrap = True
            frame.text = text
            for paragraph in frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(size)
                    run.font.color.rgb = color
    presentation.save(target)


if __name__ == "__main__":
    main()
