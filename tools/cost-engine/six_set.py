"""六件套输出格式（DEMO）。

对齐公司实际「1.成本测算六件套.xlsx」的结构：
    ①设备清单 ②劳务清单 ③成本测算（库+网检索） ④成本测算（纯网检索）
    ⑤劳务报价（库+网检索） ⑥劳务报价（纯网检索）

取费链（与真实输出一致）：
    设备材料直接费 → 采购管理费 5% → 税金 3%（简易计税）→ 总计（含税）

⚠ 所有金额均为演示数据，由内置公式生成，未接入真实信息价/定额/供应商报价。
"""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

DEMO_MARK = "演示数据（非真实价格）"
MANAGEMENT_RATE = 0.05      # 采购管理费 5%
TAX_RATE = 0.03             # 简易计税 3%
PERCENT_FMT = "0.00%"
MONEY_FMT = "#,##0.00"

THIN = Side(style="thin", color="B7C2D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="2F5597")
WARN_FILL = PatternFill("solid", fgColor="FFF3CD")
SUB_FILL = PatternFill("solid", fgColor="EAF1FB")
NOTE_FILL = PatternFill("solid", fgColor="F4F7FB")

FONT_TITLE = Font(name="微软雅黑", size=13, bold=True)
FONT_SUB = Font(name="微软雅黑", size=9, color="5A6B82")
FONT_WARN = Font(name="微软雅黑", size=9, bold=True, color="B00020")
FONT_HEAD = Font(name="微软雅黑", size=9, bold=True, color="FFFFFF")
FONT_BODY = Font(name="微软雅黑", size=9)
FONT_BOLD = Font(name="微软雅黑", size=9, bold=True)

# 施工分类（用于②劳务清单）
TRADE_KEYWORDS = (
    ("电气与接地", ("电缆", "电线", "桥架", "开关柜", "配电箱", "照明", "接地", "母线", "变压器", "ups", "电源")),
    ("智能化", ("摄像机", "监控", "交换", "网络", "光纤", "网线", "大屏", "服务器", "plc", "传感器", "门禁")),
    ("管道与暖通", ("管道", "阀门", "水泵", "空调", "风机", "暖通", "给排水", "镀锌")),
    ("土建与结构", ("混凝土", "钢筋", "挖方", "填方", "基础", "砌筑", "模板", "土方")),
    ("安装辅材", ("辅材", "杂项", "螺栓", "支架", "紧固", "胶", "耗材")),
)


def classify(name):
    lowered = name.lower()
    for trade, keywords in TRADE_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return trade
    return "其他"


def build_six_set(path, category, region, records, unit_prices, input_sha256, online=False):
    """生成六件套。unit_prices: [(库内价, 网价)] 与 records 等长。

    所有金额均为演示数据。online=True 时④⑥使用纯网价口径。
    """
    workbook = Workbook()
    workbook.remove(workbook.active)

    subtotal = sum(round(price * item["quantity"], 2)
                   for item, (price, _) in zip(records, unit_prices))
    management = round(subtotal * MANAGEMENT_RATE, 2)
    tax = round((subtotal + management) * TAX_RATE, 2)
    total = round(subtotal + management + tax, 2)

    online_subtotal = sum(round(net * item["quantity"], 2)
                          for item, (_, net) in zip(records, unit_prices))
    online_management = round(online_subtotal * MANAGEMENT_RATE, 2)
    online_tax = round((online_subtotal + online_management) * TAX_RATE, 2)
    online_total = round(online_subtotal + online_management + online_tax, 2)

    _sheet_equipment(workbook, category, records, unit_prices, subtotal)
    _sheet_labour(workbook, category, records, unit_prices, subtotal)
    _sheet_cost(workbook, "③成本测算（库+网检索）", category, records, unit_prices,
                subtotal, management, tax, total, online=False)
    _sheet_cost(workbook, "④成本测算（纯网检索）", category, records, unit_prices,
                online_subtotal, online_management, online_tax, online_total, online=True)
    _sheet_quote(workbook, "⑤劳务报价（库+网检索）", category, records, unit_prices,
                 subtotal, management, tax, total)
    _sheet_quote(workbook, "⑥劳务报价（纯网检索）", category, records, unit_prices,
                 online_subtotal, online_management, online_tax, online_total)

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()
    return {
        "subtotal": subtotal, "management": management, "tax": tax, "total": total,
        "online_subtotal": online_subtotal, "online_total": online_total,
    }


def _title(sheet, text, span, subtitle=None, warn=None):
    sheet.cell(row=1, column=1, value=text).font = FONT_TITLE
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    row = 2
    if subtitle:
        cell = sheet.cell(row=row, column=1, value=subtitle)
        cell.font = FONT_SUB
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
        row += 1
    if warn:
        cell = sheet.cell(row=row, column=1, value=warn)
        cell.font = FONT_WARN
        cell.fill = WARN_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
        sheet.row_dimensions[row].height = 28
        row += 1
    return row


def _header(sheet, row, headers):
    for column, title in enumerate(headers, start=1):
        cell = sheet.cell(row=row, column=column, value=title)
        cell.font = FONT_HEAD
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    sheet.row_dimensions[row].height = 26
    return row + 1


def _widths(sheet, widths):
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _row(sheet, row, values, money=(), center=()):
    for column, value in enumerate(values, start=1):
        cell = sheet.cell(row=row, column=column, value=value)
        cell.font = FONT_BODY
        cell.border = BORDER
        if column in money:
            cell.number_format = MONEY_FMT
        if column in center:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        else:
            cell.alignment = Alignment(vertical="center", wrap_text=False)
    return row + 1


def _subtotal_row(sheet, row, label, value, columns, note="", value_column=None):
    """合计行：标签跨列 + 金额 + 备注。"""
    sheet.cell(row=row, column=1, value=label)
    if columns > 1:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=columns)
    value_column = value_column or (columns + 1)
    cell = sheet.cell(row=row, column=value_column, value=value)
    cell.number_format = MONEY_FMT
    if note:
        note_cell = sheet.cell(row=row, column=value_column + 1, value=note)
        note_cell.font = FONT_SUB
    for column in range(1, value_column + 2):
        item = sheet.cell(row=row, column=column)
        item.font = FONT_BOLD
        item.fill = SUB_FILL
        item.border = BORDER
    return row + 1


def _footer(sheet, row, span, lines):
    row += 1
    for text in lines:
        cell = sheet.cell(row=row, column=1, value=text)
        cell.font = FONT_BODY
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
        sheet.row_dimensions[row].height = max(16, 14 * (len(text) // 68 + 1))
        row += 1
    return row


def _common_footer(category, records, unit_prices, health_note):
    library_hits = sum(1 for price, _ in unit_prices if price is not None)
    return [
        f"【取价健康度】可疑｜命中率 100%（{len(records)} 行里 0 行无价）",
        f"　来源分布：{DEMO_MARK}——演示引擎内置公式，未接入企业价格库 / 历史报价库 / 互联网检索。",
        f"　⚠ {health_note}",
        f"【本表口径】本清单为**设备材料采购清单**，单价为采购价（含税）；不含安装。不涉及甲供/C30/劳务分包口径。",
        f"【取费链】设备材料直接费 → 采购管理费 5% → 税金 3%（简易计税）。对外报价即设备材料到场价 ＋ 采购管理费 ＋ 税金。",
        "【计量与支付】月计量 80%，完工验收后付至 95%，余 5% 为质保金；质保期按合同约定，质保金到期无息退还。",
        "【有效期】本报价自出具之日起 30 天有效；未列项按现场签证另计。",
        "【采购说明】价格为设备材料到场价（含税），含运输、装卸与保险；不含安装、调试与土建配合。规格变更或数量增减按合同约定调整。",
        f"【数据来源】{DEMO_MARK}——由演示引擎按条目名称与地区系数确定性生成；偏差控制目标 ±10% 不适用。",
        f"【演示声明】本文件由演示引擎生成，全部金额为虚构示范值，禁止用于投标、报价、结算或任何对外用途。输入清单 SHA256 见下方。",
    ]


def _sheet_equipment(workbook, category, records, unit_prices, subtotal):
    sheet = workbook.create_sheet("①设备清单")
    _widths(sheet, [6, 30, 26, 10, 8, 11, 22, 13, 15, 40, 26, 22, 12])
    row = _title(sheet, f"{category}　设备清单",
                 span=13,
                 subtitle="本清单为设备采购清单：单价为采购价（含税，不含安装）。金额为演示数据。",
                 warn="⚠ 全部金额为演示数据，未接入真实价格库/行情，禁止用于对外报价。")
    row = _header(sheet, row, ["序号", "设备 / 材料名称", "技术参数 / 规格", "数量", "单位",
                               "采购方式", "报价口径", "单价（元）", "合价（元）",
                               "备注（归属依据 / 取价）", "数据来源", "核验链接", "可信度"])
    for index, (item, (price, _)) in enumerate(zip(records, unit_prices), start=1):
        amount = round(price * item["quantity"], 2)
        row = _row(sheet, row,
                   [index, item["name"], item.get("spec") or item["name"], item["quantity"],
                    item["unit"], "我方采购", "设备采购价（不含安装）", price, amount,
                    "演示归属依据：非甲供、非辅材 → 按我方采购处理",
                    DEMO_MARK, "—（演示口径，无外部链接）", "低（演示数据）"],
                   money=(8, 9), center=(1, 4, 5, 6, 8, 13))
    _subtotal_row(sheet, row, f"合计（{len(records)} 项）", subtotal, columns=8,
                  note="全部为我方采购设备；如采购合同含安装，安装费见⑤⑥劳务报价。",
                  value_column=9)


def _sheet_labour(workbook, category, records, unit_prices, subtotal):
    sheet = workbook.create_sheet("②劳务清单")
    _widths(sheet, [6, 14, 30, 26, 10, 8, 11, 15, 15, 34, 24, 12])
    row = _title(sheet, f"{category}　劳务清单（按施工分类）", span=12,
                 subtitle="只计人工与机械；甲供材料不计价；综合单价已含工日含量。金额为演示数据。",
                 warn="⚠ 全部金额为演示数据，未接入真实定额/含量库，禁止用于对外报价。")
    row = _header(sheet, row, ["序号", "分类", "项目名称", "工作内容 / 工序", "单位", "数量",
                               "综合单价（元）", "合价（元）", "备注（含量 / 依据）",
                               "数据来源", "核验链接", "可信度"])
    for index, (item, (price, _)) in enumerate(zip(records, unit_prices), start=1):
        amount = round(price * item["quantity"], 2)
        row = _row(sheet, row,
                   [index, classify(item["name"]), item["name"],
                    item.get("spec") or item["name"], item["unit"], item["quantity"],
                    price, amount,
                    "演示含量：综合单价已含工日，未接入企业定额库",
                    DEMO_MARK, "—（演示口径，无外部链接）", "低（演示数据）"],
                   money=(7, 8), center=(1, 2, 5, 6, 12))
    _subtotal_row(sheet, row, "合　计", subtotal, columns=7, value_column=8)


def _sheet_cost(workbook, name, category, records, unit_prices, subtotal,
                management, tax, total, online):
    sheet = workbook.create_sheet(name)
    _widths(sheet, [6, 30, 26, 10, 8, 13, 15, 26, 40, 26, 22, 12])
    measure = "纯网检索口径" if online else "库+网检索口径"
    subtitle = (f"本清单为设备材料采购清单，单价＝采购价（含税）；{measure}。金额为演示数据。")
    row = _title(sheet, name, span=12, subtitle=subtitle,
                 warn="⚠ 全部金额为演示数据，未接入真实价格库/行情，禁止用于投标、报价、结算或对外交付。")
    row = _header(sheet, row, ["序号", "项目名称", "规格 / 工作内容", "单位", "数量",
                               "单价（元）", "合价（元）", "价格来源", "备注（取价依据 / 风险）",
                               "数据来源", "核验链接", "可信度"])
    for index, (item, (price, net)) in enumerate(zip(records, unit_prices), start=1):
        applied = net if online else price
        amount = round(applied * item["quantity"], 2)
        source = f"{DEMO_MARK}｜{'网价口径' if online else '库内价口径'}"
        row = _row(sheet, row,
                   [index, item["name"], item.get("spec") or item["name"], item["unit"],
                    item["quantity"], applied, amount, source,
                    "演示取价：未命中真实价格库，按内置公式估算",
                    DEMO_MARK, "—（演示口径，无外部链接）", "低（演示数据）"],
                   money=(6, 7), center=(1, 4, 5, 12))
    row = _subtotal_row(sheet, row, f"设备材料合计（{len(records)} 项）", subtotal,
                        columns=6, value_column=7,
                        note="甲供项材料费不计价，其安装人工已计入劳务报价。")
    fee_rows = [
        ("直接费合计", "设备材料 + 劳务 + 交通安措", subtotal, "演示取费口径"),
        ("管理费", f"(直接费) × {MANAGEMENT_RATE:.0%}", management, "采购组织/项目管理/资料归档"),
        ("税金", f"简易计税 {TAX_RATE:.0%}", tax, "演示取费口径"),
        (f"成本测算总计（{'施工取费口径' if online else '采购总包口径'}·含税）",
         "上列逐级累加", total, "含税总价；可选项按现场签证另计"),
    ]
    for label, basis, value, note in fee_rows:
        row = _row(sheet, row, ["", label, basis, "", "", "", value, "", note,
                                "取费口径｜演示参数", "—（演示口径）", "低（演示数据）"],
                   money=(7,), center=(12,))
    _footer(sheet, row, 12, _common_footer(category, records, unit_prices,
            "本表单价为演示引擎按「条目名称 × 地区系数」生成的确定性估算，非市场挂牌价。"))


def _sheet_quote(workbook, name, category, records, unit_prices, subtotal,
                 management, tax, total):
    sheet = workbook.create_sheet(name)
    _widths(sheet, [6, 30, 26, 10, 8, 13, 15, 26, 40, 26, 22, 12])
    row = _title(sheet, name, span=12,
                 subtitle="设备材料采购总包报价：明细同成本测算表，取费＝采购管理费 5% ＋ 税金 3%。金额为演示数据。",
                 warn="⚠ 全部金额为演示数据，不代表对外报价承诺，禁止用于投标、报价、结算或对外交付。")
    row = _header(sheet, row, ["序号", "项目名称", "规格 / 工作内容", "单位", "数量",
                               "单价（元）", "合价（元）", "价格来源", "备注（取价依据 / 风险）",
                               "数据来源", "核验链接", "可信度"])
    for index, (item, (price, net)) in enumerate(zip(records, unit_prices), start=1):
        amount = round(price * item["quantity"], 2)
        row = _row(sheet, row,
                   [index, item["name"], item.get("spec") or item["name"], item["unit"],
                    item["quantity"], price, amount,
                    f"{DEMO_MARK}｜采购价口径",
                    "演示取价：未命中真实价格库，按内置公式估算",
                    DEMO_MARK, "—（演示口径，无外部链接）", "低（演示数据）"],
                   money=(6, 7), center=(1, 4, 5, 12))
    rows = [
        ("一、设备材料直接费", "上列逐项 单价 × 数量", subtotal, "演示取价结果"),
        ("二、采购管理费", f"直接费 × {MANAGEMENT_RATE:.0%}", management,
         "采购组织、比价询价、到货验收"),
        ("三、税金", f"简易计税 {TAX_RATE:.0%}", tax, "演示取费口径"),
        ("四、对外报价总额（总包·含税）", "一~三 逐级累加", total, "含税总价；可选项按现场签证另计"),
    ]
    for label, basis, value, note in rows:
        row = _row(sheet, row, ["", label, basis, "", "", "", value, "", note,
                                "取费口径｜演示参数", "—（演示口径）", "低（演示数据）"],
                   money=(7,), center=(12,))
    _footer(sheet, row, 12, _common_footer(category, records, unit_prices,
            "对外报价总额为演示数值，不得作为报价依据。"))