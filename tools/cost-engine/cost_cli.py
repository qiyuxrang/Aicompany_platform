#!/usr/bin/env python3
"""演示用工程成本测算 CLI（DEMO ONLY）。

严格实现平台 backend/portal/engineering_worker.py 约定的外部程序协议：

    cost_cli.py inspect <清单.xlsx> [...]
    cost_cli.py run <清单.xlsx> [...] --region <地区> --output-dir <目录> [--allow-online]
    cost_cli.py quota-candidates --name <名称> --unit <单位>

用途：仅供本地联调，验证平台的「上传 → 预检 → 内部成本草稿 → 下载」链路是否通。

重要声明
--------
本程序输出的**全部金额均为演示数据**，由内置的确定性公式按条目名称哈希生成，
未接入任何真实信息价、定额库、供应商报价或市场行情。
不得用于投标报价、结算、申报或任何对外用途。
平台自身也将其标记为 internal_draft / formal_pricing=false。
"""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from six_set import build_six_set  # noqa: E402  （同目录模块，需先加入 sys.path）

DEMO_MARK = "演示数据（非真实价格）"
NAME_HEADERS = ("项目名称", "清单名称", "名称", "材料名称", "设备名称", "子目名称")

# 演示用「基准单价」种子表：按名称中的关键词给一个量级，使输出看起来有区分度。
# 这些数字是构造出来的，不对应任何真实市场价。
DEMO_SEED_PRICES = (
    ("配电箱", 1850.0), ("电缆", 46.0), ("桥架", 128.0), ("开关", 62.0),
    ("插座", 38.0), ("灯具", 210.0), ("灯", 210.0), ("变压器", 68000.0),
    ("钢管", 58.0), ("挖方", 32.0), ("填方", 26.0), ("混凝土", 520.0),
    ("钢筋", 4800.0), ("管道", 96.0), ("阀门", 430.0), ("水泵", 5600.0),
    ("监控", 3200.0), ("摄像机", 1450.0), ("交换机", 4200.0), ("机柜", 2600.0),
    ("桥墩", 12500.0), ("护栏", 340.0), ("标志", 780.0), ("标线", 24.0),
)

# 地区调整系数（演示）。用于体现 --region 参数确实参与了计算。
DEMO_REGION_FACTORS = {
    "陕西": 1.00, "榆林": 1.06, "西安": 1.03,
    "山西": 0.99, "内蒙古": 1.01, "甘肃": 0.97, "宁夏": 0.98,
}

# 费用构成比例（演示）
DEMO_SPLIT = {"人工费": 0.28, "材料费": 0.52, "机械费": 0.08}


def fail(message, code=1):
    """Worker 只解析 stdout 的 JSON；诊断信息一律走 stderr。"""
    print(message, file=sys.stderr)
    return code


def emit(payload, code=0):
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.flush()
    return code


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_hash(values):
    """与 engineering_worker._combined_hash 完全一致：拼接十六进制串后再哈希。"""
    return hashlib.sha256("".join(values).encode("ascii")).hexdigest()


def demo_unit_price(name, region):
    """确定性演示单价：同名条目任何时候都得到同一结果，便于复核链路。"""
    base = None
    for keyword, price in DEMO_SEED_PRICES:
        if keyword in name:
            base = price
            break
    if base is None:
        # 未命中种子表时，用名称哈希生成一个稳定的量级（200~1200 元）。
        digest = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
        base = 200.0 + (digest % 1001)
    factor = DEMO_REGION_FACTORS.get(region, 1.00)
    # 名称哈希带来的微小浮动，避免同价条目完全一致。
    jitter = 0.94 + (int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:4], 16) % 121) / 1000.0
    return round(base * factor * jitter, 2)


def demo_online_price(name, region):
    """④⑥纯网检索口径的演示单价。

    真实输出里网价与库内价不同（网价含税、口径各异）。此处用一个稳定的偏移量模拟两套
    价格并存，使④⑥表在结构上可区分、可核对。仍为演示数据。
    """
    base = demo_unit_price(name, region)
    # 网价相对库内价的偏移：-8% ~ +12%，由名称哈希决定，保证可复现。
    digest = int(hashlib.sha256(("net:" + name).encode("utf-8")).hexdigest()[:6], 16)
    ratio = 0.92 + (digest % 201) / 1000.0
    return round(base * ratio, 2)


def read_sheet(path):
    """读取清单，返回 (表头行号, 表头列表, 数据行列表)。"""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(["" if cell is None else str(cell).strip() for cell in row])
        if not rows:
            return 0, [], []
        header_index = 0
        for index, row in enumerate(rows[:10]):
            joined = "".join(row)
            if any(key in joined for key in NAME_HEADERS) or "数量" in joined:
                header_index = index
                break
        header = rows[header_index]
        data = [row for row in rows[header_index + 1:] if any(cell for cell in row)]
        return header_index, header, data
    finally:
        workbook.close()


def locate_columns(header):
    """定位关键列。返回 dict，缺失的列为 None。"""
    found = {"name": None, "unit": None, "quantity": None, "spec": None, "remark": None}
    for index, title in enumerate(header):
        if found["name"] is None and any(key in title for key in NAME_HEADERS):
            found["name"] = index
        elif found["spec"] is None and any(key in title for key in ("规格", "技术参数", "型号", "工作内容")):
            found["spec"] = index
        elif found["unit"] is None and "单位" in title:
            found["unit"] = index
        elif found["quantity"] is None and ("数量" in title or "工程量" in title):
            found["quantity"] = index
        elif found["remark"] is None and "备注" in title:
            found["remark"] = index
    return found


def inspect_one(path, name):
    """预检单份清单。返回 (passed, issues, data_rows, records)。"""
    issues = []
    records = []
    try:
        _, header, data = read_sheet(path)
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        return False, [f"无法读取工作簿：{error}"], 0, []
    if not header:
        return False, ["工作簿为空，未找到表头。"], 0, []
    columns = locate_columns(header)
    if columns["name"] is None:
        issues.append("缺少「项目名称」列，无法识别清单条目。")
    if columns["unit"] is None:
        issues.append("缺少「单位」列。")
    if columns["quantity"] is None:
        issues.append("缺少「数量」列。")
    if issues:
        return False, issues, 0, []
    for row in data:
        label = row[columns["name"]] if columns["name"] < len(row) else ""
        if not label:
            continue
        raw_quantity = row[columns["quantity"]] if columns["quantity"] < len(row) else ""
        unit = row[columns["unit"]] if columns["unit"] < len(row) else ""
        spec = row[columns["spec"]] if columns["spec"] is not None and columns["spec"] < len(row) else ""
        remark = row[columns["remark"]] if columns["remark"] is not None and columns["remark"] < len(row) else ""
        try:
            quantity = float(str(raw_quantity).replace(",", ""))
        except (TypeError, ValueError):
            issues.append(f"条目「{label}」的数量无法解析：{raw_quantity!r}")
            continue
        if quantity <= 0:
            issues.append(f"条目「{label}」的数量必须大于 0。")
            continue
        records.append({"name": label, "unit": unit, "quantity": quantity,
                        "spec": spec, "remark": remark})
    if not records:
        issues.append("未解析到任何有效清单条目。")
    return (not issues), issues, len(records), records
def write_draft(path, source_name, region, records, input_sha256):
    """生成六件套内部成本草稿（对齐公司实际输出格式）。

    ⚠ 全部金额为演示数据，由演示引擎按确定性公式生成，未接入真实价格库/定额/行情。
    """
    unit_prices = [(demo_unit_price(item["name"], region),
                    demo_online_price(item["name"], region)) for item in records]
    summary = build_six_set(path, Path(source_name).stem, region, records, unit_prices,
                            input_sha256)
    return summary


def command_inspect(paths):
    files = []
    all_passed = True
    for path in paths:
        name = Path(path).name
        try:
            digest = sha256_file(path)
            size = Path(path).stat().st_size
        except OSError as error:
            files.append({"input": str(path), "sha256": "", "size_bytes": 0,
                          "passed": False, "data_rows": 0, "issues": [f"文件不可读取：{error}"]})
            all_passed = False
            continue
        passed, issues, data_rows, _ = inspect_one(path, name)
        all_passed = all_passed and passed
        files.append({"input": str(path), "sha256": digest, "size_bytes": size,
                      "passed": passed, "data_rows": data_rows, "issues": issues})
    code = 0 if all_passed else 2
    return emit({"ok": all_passed, "command": "inspect", "files": files,
                 "engine": "demo-cost-engine", "demo": True}, code)


def command_run(paths, region, output_root, allow_online):
    # 复现平台要求的预检阶段：任一清单不合格即停在 preflight。
    prepared = []
    preflight_files = []
    for path in paths:
        name = Path(path).name
        try:
            digest = sha256_file(path)
        except OSError as error:
            prepared.append(None)
            preflight_files.append({"input": str(path), "name": name, "passed": False,
                                    "issues": [f"文件不可读取：{error}"]})
            continue
        passed, issues, data_rows, records = inspect_one(path, name)
        prepared.append({"path": Path(path), "name": name, "sha256": digest,
                         "records": records} if passed else None)
        preflight_files.append({"input": str(path), "name": name, "passed": passed,
                                "issues": issues, "data_rows": data_rows})
    if any(item is None for item in prepared):
        return emit({"ok": False, "command": "run", "stage": "preflight",
                     "engine": "demo-cost-engine", "demo": True,
                     "files": preflight_files}, 2)

    output_root = Path(output_root)
    generated = output_root / "generated"
    file_records = []
    output_hashes = []
    input_hashes = []
    validation_issues = []
    source_health_files = []
    pending_files = []

    for item in prepared:
        input_hashes.append(item["sha256"])
        output_path = generated / f"成本测算内部草稿-{item['path'].stem}.xlsx"
        write_draft(output_path, item["name"], region, item["records"], item["sha256"])
        output_hash = sha256_file(output_path)
        output_hashes.append(output_hash)
        file_records.append({
            "input": str(item["path"]),
            "input_sha256": item["sha256"],
            "preflight_issues": [],
            "status": "completed",
            "output_dir": str(generated),
            "output_file": str(output_path),
            "output_sha256": output_hash,
            "validation_passed": True,
            "validation_issues": [],
            "source_health": {
                "结论": f"{DEMO_MARK}：由演示引擎生成，未接入真实信息价。",
                "警告": [],
                "零价项": 0,
                "来源分布": "演示定额库（内置种子表）",
            },
            "pending_confirmations": [f"条目「{item['records'][0]['name']}」等 {len(item['records'])} 项的单价为演示数据，须人工重新组价。"],
            "internal_draft": True,
        })
        source_health_files.append({"input": str(item["path"]), "name": item["name"],
                                    "health": {"结论": DEMO_MARK, "警告": [], "零价项": 0}})
        pending_files.append({"input": str(item["path"]), "name": item["name"],
                              "item": f"共 {len(item['records'])} 项单价待人工复核"})

    return emit({
        "ok": True,
        "command": "run",
        "engine": "demo-cost-engine",
        "demo": True,
        "online_allowed": bool(allow_online),
        "input_hash": combined_hash(input_hashes),
        "output_hash": combined_hash(output_hashes),
        "validation_issues": validation_issues,
        "source_health": {"files": source_health_files, "结论": DEMO_MARK},
        "pending_confirmations": pending_files,
        "result_type": "internal_draft",
        "region": region,
        "files": file_records,
    }, 0)


def load_quota_library():
    """加载陕西省基价表提取结果（29316 条）。

    数据由 build_quota_library.py 从省住建厅公开的《陕西省建设工程基价表（2025）》
    Word 转换稿提取，仅含编号/项目名称/单位/专业，**不含价格**。
    """
    path = Path(__file__).resolve().parent / "quota-library.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = payload.get("entries")
    return entries if isinstance(entries, list) else []


def _scorable_chars(text):
    """参与模糊匹配的字符：中文与字母。

    数字是「巧合命中」的重灾区 —— 型号、尺寸、管径、容量都含数字，
    若参与计分，「YJV-4×25+1×16」会误配到「箱体1200×650」这类条目。
    故只保留中文与字母。
    """
    return {ch for ch in text if "\u4e00" <= ch <= "\u9fff" or ch.isalpha()}


def normalize_query(name):
    """剥离规格型号与尺寸，提取中文主干词。

    工程清单条目通常带型号（如「电力电缆 YJV-4×25+1×16」），而定额条目
    是工序名（如「铜芯电力电缆敷设 电缆截面(mm²) ≤35」）。不做归一化时，
    型号会把字命中率稀释到 0.1 上下，使结果不可用；归一化后「电力电缆」
    可直接作为连续子串命中目标条目。
    """
    kept = []
    for token in re.split(r"[\s\u3000]+", name):
        # 去掉「字母+数字」混排的型号、以及尺寸/纯数字片段
        token = re.sub(r"[A-Za-z]*\d+[A-Za-z0-9\-\u2013\u2014\u00d7xX*.\u002b/]*", "", token)
        token = token.strip(" -\u2013\u2014_/\uff08\uff09()[]\u3010\u3011")
        # 仅保留含中文、且长度 ≥2 的片段：单字多为剥离型号后的碎片
        # （如「400万」→「万」、「24口」→「口」），留存会污染字命中率
        if len(token) >= 2 and any("\u4e00" <= ch <= "\u9fff" for ch in token):
            kept.append(token)
    return " ".join(kept).strip() or name


def normalize_unit(unit):
    """归一化计量单位，用于判断清单单位与定额单位是否实质兼容。

    定额惯用「扩大单位」计价（10m / 100m² / 1000m³），而清单多用基本单位
    （m / m² / m³）。二者实质兼容，但字符串不同 —— 若不归一化，
    「单位一致」会全判为否，既误导用户，也浪费了单位维度的加权信息。
    另外 m³ 存在 m3 / m³ / ㎡ / 立方米 等多种写法，一并统一。
    """
    if not unit:
        return ""
    value = unit.strip()
    value = re.sub(r"^\d+(?:\.\d+)?\s*", "", value)      # 去掉计量基数倍数
    value = (value.replace("m3", "m³").replace("M3", "m³")
                  .replace("m2", "m²").replace("M2", "m²")
                  .replace("㎡", "m²").replace("m^2", "m²").replace("m^3", "m³"))
    value = (value.replace("立方米", "m³").replace("平方米", "m²")
                  .replace("米", "m").replace("吨", "t"))
    return value.strip()


def units_compatible(query_unit, entry_unit):
    """判断两个单位是否兼容（含定额扩大基数的差异）。"""
    left, right = normalize_unit(query_unit), normalize_unit(entry_unit)
    return bool(left) and left == right


def score_entry(entry, name, unit):
    """给定额条目打分（兼容平台 0~2 的 score 区间）。

    规则：
      - 名称完全一致            +1.00
      - 查询词为名称连续子串      +0.90
      - 字命中率（只计中文与字母）  +0.60 × 命中率
      - 单位一致                +0.10

    查询词同时以「原词」与「剥离规格后的主干词」评估并取较高分：
    清单条目带型号、定额条目是工序名，主干词往往才是可用信号。
    """
    entry_name = (entry.get("name") or "").replace("\n", " ")
    if not name or not entry_name:
        return 0.0

    queries = [name]
    stem = normalize_query(name)
    if stem and stem != name:
        queries.append(stem)

    best = 0.0
    for query in queries:
        if entry_name == query:
            value = 1.00
        elif query in entry_name:
            value = 0.90
        else:
            chars = _scorable_chars(query)
            if not chars:
                continue
            hits = sum(1 for ch in chars if ch in entry_name)
            value = 0.60 * (hits / len(chars))
        best = max(best, value)

    if units_compatible(unit, entry.get("unit")):
        best += 0.10
    return best


MIN_RELEVANT = 0.30


def rank_candidates(name, unit, limit=5, library=None, per_section=2):
    """检索并排序候选定额条目，返回 [(score, entry), ...]。

    排序键（依次）：
      1. 分数降序
      2. 查询词在名称中的出现次数降序 —— 出现越多说明该条目越"专指"此事。
         例：查「机柜」时，「综合布线…机柜、机架 安装机柜、机架」（2 次）
         应优先于「电源设备安装 恒电位仪 一体机柜」（1 次）。
      3. 编号升序（册内主线子目靠前）

    再做**章节多样性**筛选：同一工序章节最多保留 per_section 条。
    否则查「电力电缆」会返回 5 条同章节的「≤10/≤16/≤25/≤35/≤50」，
    看似有结果实则只覆盖一个工序，用户看不到候选全貌。
    若去重后不足 limit，按分数从剩余条目补齐。

    低于 MIN_RELEVANT 的结果不返回 —— 宁可少给，也不给明显无关的条目。
    """
    entries = library if library is not None else load_quota_library()
    stem = normalize_query(name)
    scored = []
    for entry in entries:
        value = score_entry(entry, name, unit)
        if value <= 0:
            continue
        entry_name = (entry.get("name") or "")
        occurrences = entry_name.count(name)
        if stem and stem != name:
            occurrences = max(occurrences, entry_name.count(stem))
        scored.append((value, occurrences, entry))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2].get("code", "")))

    picked, seen_section, overflow = [], {}, []
    for value, _, entry in scored:
        if value < MIN_RELEVANT:
            break
        pair = (value, entry)
        key = entry.get("section") or entry.get("major")
        if seen_section.get(key, 0) >= per_section:
            overflow.append(pair)
            continue
        seen_section[key] = seen_section.get(key, 0) + 1
        picked.append(pair)
        if len(picked) >= limit:
            return picked
    # 去重后不足 limit，用被压下的高分条目补齐
    for pair in overflow:
        if len(picked) >= limit:
            break
        picked.append(pair)
    return picked


def command_quota_candidates(name, unit):
    """从陕西省基价表检索候选定额条目。

    注意：**绝不输出 price 字段** —— 平台契约规定配额候选不得携带价格，
    且 D-05（定额数据授权、价格基准日、费用税费口径）尚未签认，
    价格不应由本工具给出。因此只返回编号/名称/单位/专业供人工核对。
    """
    library = load_quota_library()
    if not library:
        return fail("未找到定额库文件 quota-library.json，请先运行 build_quota_library.py。")

    ranked = rank_candidates(name, unit, limit=5, library=library)
    candidates = []
    for value, entry in ranked:
        candidates.append({
            "code": entry["code"],
            "major": entry.get("major", "未分类"),
            "name": entry["name"],
            "unit": entry.get("unit", ""),
            "score": round(min(value, 2.0), 2),
            "original_source": entry.get("source", "陕西省建设工程基价表（2025）"),
            "unit_compatible": units_compatible(unit, entry.get("unit")),
        })
    return emit({"ok": True, "command": "quota-candidates", "demo": False,
                 "library_count": len(library), "candidates": candidates}, 0)


def main(argv):
    if len(argv) < 2:
        return fail("用法：cost_cli.py <inspect|run|quota-candidates> ...")
    command = argv[1]
    if command == "inspect":
        paths = argv[2:]
        if not paths:
            return fail("inspect 需要至少一个清单路径。")
        return command_inspect(paths)
    if command == "run":
        paths = []
        region = ""
        output_root = ""
        allow_online = False
        index = 2
        while index < len(argv):
            token = argv[index]
            if token == "--region":
                index += 1
                region = argv[index] if index < len(argv) else ""
            elif token == "--output-dir":
                index += 1
                output_root = argv[index] if index < len(argv) else ""
            elif token == "--allow-online":
                allow_online = True
            else:
                paths.append(token)
            index += 1
        if not paths or not output_root:
            return fail("run 需要清单路径与 --output-dir。")
        return command_run(paths, region or "陕西", output_root, allow_online)
    if command == "quota-candidates":
        name = ""
        unit = ""
        index = 2
        while index < len(argv):
            token = argv[index]
            if token == "--name":
                index += 1
                name = argv[index] if index < len(argv) else ""
            elif token == "--unit":
                index += 1
                unit = argv[index] if index < len(argv) else ""
            index += 1
        return command_quota_candidates(name, unit)
    return fail(f"未知命令：{command}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    sys.exit(main(sys.argv))