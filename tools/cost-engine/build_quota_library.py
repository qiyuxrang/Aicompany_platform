"""从陕西省基价表 Word 文档提取定额条目，构建候选库（只读源文件）。

重要：仅提取「编号 / 项目名称 / 单位」三项，**不提取任何价格字段**。
平台契约规定 quota-candidates 输出中不得出现 price，否则判定能力不可用；
且 D-05（定额数据授权、价格基准日等）尚未签认，价格不应由本工具输出。

## 关键点：工序名在表格之外

基价表的表格里「项目名称」列只写规格（如「电缆截面(mm²) ≤10」），
真正的工序名（如「直埋式电力电缆敷设」）是表格**前面紧邻的段落标题**：

    四、电力电缆敷设              ← 章
    1.直埋式电力电缆敷设           ← 节＝工序名
    ┌──────────────────────────┐
    │ 4-9-140 | 电缆截面(mm²) ≤10 | 10m | ... │
    └──────────────────────────┘
    2.电缆沟(隧道)内电力电缆敷设     ← 下一个节

因此必须按文档顺序遍历段落与表格，跟踪最近的章节标题，
将其并入条目名称。否则近 20% 的条目只剩规格、无法被工序词检索到
（搜「电力电缆敷设」会一无所获）。

章节标题需与正文条款区分：说明部分的「2.材料费包括：…」也符合 `数字.`
形态，故以「长度上限 + 不含句读标点」过滤。

产出：.runtime/demo-cost-engine/quota-library.json
"""
import json
import os
import re
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

# 定额源文档目录：优先取环境变量，缺省为本仓库 tools/ 下的约定位置。
# 不硬编码个人机器路径 —— 换机器、换用户都可运行。
BASE = Path(os.environ.get("QUOTA_SOURCE_DIR", Path(__file__).resolve().parent / "source"))
PRICE_DIR = BASE / "P020250321392712718605" / "各专业工程基价表"
OUT = Path(__file__).resolve().parent / "quota-library.json"

# 专业名映射：从文件名推断业务专业，便于按专业检索与展示
MAJOR_BY_FILE = {
    "房屋建筑与装饰": "房屋建筑与装饰工程",
    "通用安装": "通用安装工程",
    "市政": "市政工程",
    "园林绿化": "园林绿化工程",
    "城市地下综合管廊": "城市地下综合管廊工程",
    "绿色建筑": "绿色建筑工程",
}

CODE_PATTERN = re.compile(r"^\d+(-\d+)*$")
# 标题形态：
#   章「一、xxx」或「第六章 xxx」／节「1.xxx」／子节「(1) xxx」
CHAPTER = re.compile(r"^(?:第[一二三四五六七八九十百\d]+章[\s　]*)?[一二三四五六七八九十]+、\S{2,30}$|^第[一二三四五六七八九十百\d]+章[\s　]*\S{2,30}$")
SECTION = re.compile(r"^\d+[.、]\S{1,30}$")
SUBSECTION = re.compile(r"^[（(]\d+[)）]\S{1,25}$")
# 标题起始位置（用于拆分被粘在一起的多个标题）
TITLE_AT = re.compile(r"第[一二三四五六七八九十百\d]+[章节]|[一二三四五六七八九十]{1,3}、|\d+[.、](?=\D)|[（(]\d+[)）]")
# 序号前缀：「一、」「1.」「1、」「(1)」「第六章 」
ORDINAL = re.compile(r"^(第[一二三四五六七八九十百\d]+[章节][\s　]*|[一二三四五六七八九十]+、|\d+[.、]|[（(]\d+[)）])\s*")
# 标题正文中不允许出现的句读标点（用于排除说明部分的正文条款）。
#
# 两个坑：
#  1) 顿号「、」与句点「.」既是序号分隔符、又是标题内的并列连接词
#     （如「九、交换机设备安装、调试」），故**不纳入**正文标点集，
#     否则这类标题会被整条否掉，章节状态退回到更早的陈旧值。
#  2) 判定前必须先剥离序号，否则「四、电力电缆敷设」会被自身的顿号误判。
# 说明部分的条款多含「。／，／：」，仍能被可靠排除。
PUNCTUATION = set("。，：；！？,;:!")


def major_of(path):
    for key, value in MAJOR_BY_FILE.items():
        if key in str(path):
            return value
    return "其他"


def iter_blocks(document):
    """按文档真实顺序产出 ('p', 段落文本) / ('t', 表格对象)。"""
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield "p", (Paragraph(child, document).text or "").strip()
        elif child.tag.endswith("}tbl"):
            yield "t", Table(child, document)


def looks_like_title(text, pattern):
    """判断段落是否为章节标题。

    规则：符合形态 → 剥掉序号后正文不含句读标点 → 长度受控。
    「2.材料费包括：材料原价（或供应价格）」这类正文条款带冒号/括号，
    剥掉序号后仍会命中标点，故被排除。
    """
    if not text or len(text) > 34 or not pattern.match(text):
        return False
    body = ORDINAL.sub("", text).strip()
    if not body or len(body) > 30:
        return False
    return not any(ch in PUNCTUATION for ch in body)


def split_titles(text):
    """拆分被粘成一个段落的「章 + 节」标题。

    部分文档（如城市地下综合管廊册）会把章与节合并成一个段落：
        「第六章 自动化控制装置及仪表安装工程一、计算机及网络系统工程」
    若不拆分，该段落会被当成单个标题，其下条目的工序名要么为空、
    要么沿用上一章的陈旧值（实测曾导致该册大量条目错标）。

    做法：检测到 ≥2 个标题起始标记、且首段为「第…章」时，
    在最后一个标记处切开，返回 (章片段, 节片段)。

    注意：该册章下用中文序号分节（「一、计算机及网络系统工程」），
    与上册「数字. 工序名」的层级相反，故此处直接返回「章＋节」语义，
    由调用方赋值，不再走形态判定。
    """
    if not text:
        return (text, "")
    marks = [(m.start(), m.group()) for m in TITLE_AT.finditer(text)]
    if len(marks) < 2:
        return (text, "")
    first_at, last_at = marks[0][0], marks[-1][0]
    if first_at != 0 or last_at == 0:
        return (text, "")
    head, tail = text[:last_at].strip(), text[last_at:].strip()
    # 首段必须确为章（含「章」字），否则不拆，避免误伤普通标题
    if "章" in head and tail and len(tail) <= 30:
        return (head, tail)
    return (text, "")


def clean_title(text):
    """去掉标题前的序号，保留工序词。例：1.直埋式电力电缆敷设 → 直埋式电力电缆敷设"""
    return ORDINAL.sub("", text).strip()


def merge_name(section, name):
    """把工序名并入条目名称；若名称已含工序词则不重复。"""
    if not section:
        return name
    if section in name:
        return name
    # 名称已覆盖工序词大部分字符时不重复（例如名称本身就是完整工序名）
    if len(section) >= 3:
        hit = sum(1 for ch in set(section) if ch in name)
        if hit / len(set(section)) >= 0.7:
            return name
    return f"{section} {name}"


def extract(path):
    """返回 (条目列表, 表格统计)。只取编号/名称/单位。"""
    document = Document(path)
    entries = []
    stat = {"tables": 0, "data_tables": 0, "skipped_header": 0,
            "with_section": 0, "merged_titles": 0}

    chapter = ""
    section = ""
    subsection = ""

    for kind, payload in iter_blocks(document):
        if kind == "p":
            text = payload
            if not text:
                continue
            # 段落可能把章与节粘在一起（如管廊册），先拆分再逐段判定
            head, tail = split_titles(text)
            if tail:
                stat["merged_titles"] += 1
                # 拆分结果语义明确：head=章、tail=节（该册章下用中文序号分节）
                chapter, section, subsection = clean_title(head), clean_title(tail), ""
            elif looks_like_title(head, CHAPTER):
                chapter, section, subsection = clean_title(head), "", ""
            elif looks_like_title(head, SECTION):
                section, subsection = clean_title(head), ""
            elif looks_like_title(head, SUBSECTION):
                subsection = clean_title(head)
            # 页码与空段落不重置 section —— 跨页续表仍归属同一工序。
            continue

        table = payload
        stat["tables"] += 1
        if not table.rows:
            continue
        header = [cell.text.strip() for cell in table.rows[0].cells]
        joined = "".join(header)
        if "编号" not in joined or "项目名称" not in joined:
            continue
        stat["data_tables"] += 1

        # 定位列索引：以表头文字为准，避免列序变化导致错位
        def column_of(*names):
            for index, title in enumerate(header):
                if any(name in title for name in names):
                    return index
            return None

        code_at = column_of("编号")
        name_at = column_of("项目名称", "名称")
        unit_at = column_of("单位")
        if code_at is None or name_at is None or unit_at is None:
            stat["skipped_header"] += 1
            continue

        # 工序名优先取「节」，无节时退到「子节」，再退到「章」
        label = section or subsection or chapter
        # 章的粒度过粗（如「四、电力电缆敷设」下辖多个工序），仅在前两者都缺时使用
        for row in table.rows[1:]:
            cells = [cell.text.strip() for cell in row.cells]
            if code_at >= len(cells) or name_at >= len(cells) or unit_at >= len(cells):
                continue
            code, name, unit = cells[code_at], cells[name_at], cells[unit_at]
            if not code or not name or not unit:
                continue
            if not CODE_PATTERN.match(code) or name in {"项目名称", "名称"}:
                continue
            # 原始表格中「项目名称」可能因换行被拆开，统一为空格
            name = re.sub(r"\s+", " ", name).strip()
            merged = merge_name(label, name)
            if merged != name:
                stat["with_section"] += 1
            entries.append({"code": code, "name": merged,
                            "section": label, "unit": unit})
    return entries, stat


def main():
    targets = []
    if PRICE_DIR.is_dir():
        for item in sorted(PRICE_DIR.iterdir()):
            if item.is_dir():
                targets.extend(sorted(item.glob("*.docx")))
            elif item.suffix == ".docx":
                targets.append(item)

    # 源目录缺失或没找到文档时必须中止：否则会写出空库，
    # 把已生成的 quota-library.json（29316 条）覆盖成空文件，造成静默数据丢失。
    if not targets:
        print(f"✗ 未找到基价表文档。")
        print(f"  期望目录：{PRICE_DIR}")
        print(f"  请设置环境变量 QUOTA_SOURCE_DIR 指向定额 Word 文档的根目录，")
        print(f"  或把文档放到 {BASE} 下。本次未修改 quota-library.json。")
        return 1

    print(f"基价表文档：{len(targets)} 个（源目录 {BASE}）\n")

    library = []
    for path in targets:
        try:
            entries, stat = extract(path)
        except Exception as error:  # noqa: BLE001
            print(f"✗ {path.name}: {error}")
            continue
        major = major_of(path)
        for entry in entries:
            entry["major"] = major
            entry["source"] = path.name
        library.extend(entries)
        print(f"  {path.name[:38]:<40} 表格 {stat['tables']:>4}　数据表 {stat['data_tables']:>3}"
              f"　条目 {len(entries):>6}　补工序名 {stat['with_section']:>6}"
              f"　粘合标题 {stat['merged_titles']:>4}　专业 {major}")

    # 去重（同一编号可能因跨页重复出现）
    seen = {}
    for entry in library:
        seen.setdefault((entry["major"], entry["code"]), entry)
    unique = list(seen.values())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "陕西省建设工程基价表（2025）— 省住建厅公开文件转换稿",
        "note": "仅含编号/项目名称/单位/专业与所属工序（section），不含价格。"
                "价格字段依 D-05 隔离要求不输出。"
                "名称已并入表格外的工序标题，便于按工序词检索。",
        "count": len(unique),
        "entries": unique,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"\n提取条目合计：{len(library)}　去重后：{len(unique)}")
    print(f"已写入：{OUT}")
    by_major = {}
    for entry in unique:
        by_major[entry["major"]] = by_major.get(entry["major"], 0) + 1
    print("\n按专业分布：")
    for major, count in sorted(by_major.items(), key=lambda kv: -kv[1]):
        print(f"  {major:<24} {count:>6} 条")


if __name__ == "__main__":
    raise SystemExit(main())