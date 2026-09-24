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
    source_by_family = {item["family"]: item for item in data.get("sources", [])}
    provenance = "\n".join(
        f"{item['family']} v{item['version']} | SHA256={item['sha256']}"
        for item in data.get("sources", [])
    )
    slides = [(data["title"], "待人工审核草稿\n简版内容草稿引擎；非 PPT Master",
               f"Pair SHA256={data.get('sha256', '')}\n{provenance}")]
    for family, title in (("technical-solution", "技术方案"), ("feasibility", "可行性研究")):
        selected = [item for item in blocks if item["ref"].startswith(family + ":") and item["type"] == "paragraph"
                    and not item["ref"].split(":", 1)[1].startswith(("DRAFT_NOTICE", "PENDING_TEXT"))]
        for index in range(0, len(selected), 3):
            chunk = selected[index:index + 3]
            text = "\n\n".join(item["text"][:300] for item in chunk)
            refs = "内容块：" + "、".join(item["ref"] for item in chunk)
            source = source_by_family[family]
            provenance = f"{family} v{source['version']} | SHA256={source['sha256']}"
            slides.append((f"{title} · {index // 3 + 1}", text, refs + "\n来源版本：" + provenance))
    if len(slides) < 3:
        raise ValueError("both report families must contribute slides")
    if data.get("pending"):
        pending = data["pending"][:8]
        body = "\n".join("• " + str(value.get("text", value))[:120] for value in pending)
        refs = [ref for value in pending for ref in value.get("refs", [])]
        slides.append(("待确认事项", body, "待确认块：" + "、".join(refs)))
    for slide_number, (heading, body, citation) in enumerate(slides, start=1):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        citation = f"{citation}\n第 {slide_number} 页"
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = RGBColor(244, 247, 251)
        for text, y, size, color in ((heading, .6, 32, RGBColor(23, 54, 93)),
                                     (body, 1.7, 21, RGBColor(23, 43, 77)),
                                     (citation, 6.1, 10, RGBColor(70, 80, 95))):
            height = 4.35 if y == 1.7 else (1.15 if y == 6.1 else .9)
            box = slide.shapes.add_textbox(Inches(.8), Inches(y), Inches(11.7), Inches(height))
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
