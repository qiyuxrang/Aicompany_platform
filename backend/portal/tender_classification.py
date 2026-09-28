"""Conservative, offline classification backed by notice construction evidence."""

import re

from .tender_normalize import html_to_lines, notice_content

CLASSIFICATION_VERSION = 'digital-construction-v5'
INDUSTRIES = {
    'coal': '煤炭', 'water': '水利水电', 'power': '电力与新能源',
    'medical': '医疗卫生', 'transport': '交通运输', 'petrochemical': '石油与化工',
    'municipal': '市政与公共设施', 'education': '教育与科研', 'manufacturing': '工业制造',
}
NOTICE_CATEGORIES = {'procurement': '招标采购', 'change': '变更澄清', 'result': '结果公告'}
PROVINCES = (
    ('北京', '北京市'), ('天津', '天津市'), ('河北', '河北省'), ('山西', '山西省'), ('内蒙古', '内蒙古自治区'),
    ('辽宁', '辽宁省'), ('吉林', '吉林省'), ('黑龙江', '黑龙江省'), ('上海', '上海市'), ('江苏', '江苏省'),
    ('浙江', '浙江省'), ('安徽', '安徽省'), ('福建', '福建省'), ('江西', '江西省'), ('山东', '山东省'),
    ('河南', '河南省'), ('湖北', '湖北省'), ('湖南', '湖南省'), ('广东', '广东省'), ('广西', '广西壮族自治区'),
    ('海南', '海南省'), ('重庆', '重庆市'), ('四川', '四川省'), ('贵州', '贵州省'), ('云南', '云南省'),
    ('西藏', '西藏自治区'), ('陕西', '陕西省'), ('甘肃', '甘肃省'), ('青海', '青海省'),
    ('宁夏', '宁夏回族自治区'), ('新疆', '新疆维吾尔自治区'), ('香港', '香港特别行政区'),
    ('澳门', '澳门特别行政区'), ('台湾', '台湾省'),
)
INDUSTRY_TERMS = {
    'coal': ('煤矿', '煤炭', '矿井', '煤业', '矿业'),
    'water': ('水利', '水电', '水库', '水务', '灌区', '河道', '防汛'),
    'power': ('电力', '电网', '供电', '新能源', '光伏', '风电', '储能', '发电'),
    'medical': ('医院', '医疗', '卫生', '疾控', '药品', '医药'),
    'transport': ('交通', '公路', '高速', '铁路', '机场', '港口', '轨道', '运输', '交控'),
    'petrochemical': ('石油', '化工', '石化', '炼油', '油田', '天然气'),
    'municipal': ('市政', '城管', '城市', '环卫', '公共设施', '园林', '政务', '公安', '消防'),
    'education': ('教育', '学校', '大学', '学院', '科研', '研究院', '实验室'),
    'manufacturing': ('制造', '工厂', '工业', '生产线', '车间', '机械', '钢铁'),
}
DIGITAL_TERMS = {
    '信息化建设': ('信息化', '信息系统', '管理系统', '业务系统', '软件开发', '软件系统', 'ERP系统'),
    '数字化建设': ('数字化', '数字孪生', '数字矿山', '数字园区', '数字平台'),
    '智能化建设': ('智能化', '智能矿山', '智慧矿山', '智慧水利', '智慧水务', '智慧医院',
                  '智慧校园', '智慧交通', '智慧城市', '智能控制', '智能调速', '智慧工厂'),
    '数据平台': ('数据平台', '数据中心', '数据治理', '数据中台', '大数据', '数据库'),
    '物联网与监测': ('物联网', '监测系统', '监控系统', '安防监控', '视频监控', '监控平台',
                   '人员定位', '远程监测', '感知系统', '自动化控制', '工业控制', '在线监测设备', '数采仪'),
    '网络与安全': ('网络安全', '网络建设', '网络改造', '网络设备', '弱电系统', '弱电智能化',
                 '综合布线', '通信系统', '服务器', '机房建设'),
    '人工智能': ('人工智能', '机器视觉', '智能识别', '大模型', '智能体', '预测性维护', '设备预测维护'),
    '算力基础设施': ('计算集群', '算力平台', '算力中心', '算力服务', '计算节点', '超算系统'),
}
_SCOPE = re.compile(r'采购需求|建设内容|项目概况|招标范围|采购内容|技术要求|服务内容|项目内容|标的名称|主要内容|项目简介')
_STOP = re.compile(r'获取.{0,8}(?:招标|采购|磋商)文件|投标人资格|申请人.{0,5}资格|提交.{0,6}文件|投标截止|联系方式|代理机构|其他补充事宜')
_PROCEDURE = re.compile(
    r'投标文件|招标文件下载|在线下载|网上下载|在线报名|网上报名|(?:登录|登陆).{0,80}(?:平台|系统)'
    r'|(?:获取|下载|领取|购买)(?:竞争性)?(?:招标|采购|磋商|谈判|询价)文件|文件获取|(?:注册|申请)(?:账号|账户)'
    r'|CA证书|数字证书|电子签章|远程开标|电子交易平台|电子招投标平台|电子化交易'
    r'|(?:供应商|投标人).{0,12}(?:登录|注册|下载|上传|提交|递交|报名)'
    r'|(?:提交|递交).{0,10}(?:报价|响应|文件)|版权所有|技术支持|主办单位|承办单位|网站建设')
_POLICY_CITATION = re.compile(
    r'(?:财库|财办库|国办发)\s*[〔\[（(]|(?:财政部|工业和信息化部|国务院).*《')
_QUALIFICATION_SECTION = re.compile(
    r'(?:供应商|投标人|申请人).{0,5}(?:资格|资质)(?:要求|条件)|(?:资格|资质|业绩)要求\s*[:：]')
_QUALIFICATION_EVIDENCE = re.compile(
    r'(?:登记注册|备案证明|查询截图|提供截图)|(?:基本信息|资质信息).{0,100}(?:可查询|查询)'
    r'|(?:须|必须|应).{0,30}(?:具有|具备|提供).{0,50}(?:资质|许可证|业绩证明)')
_BACKGROUND_ONLY = re.compile(r'瓶颈|(?:智能化|数字化|信息化)服务体验|(?:智能化|数字化|信息化)发展趋势')
_ACTION = re.compile(r'建设|构建|开发|升级|改造|部署|采购|安装|实施|集成|运维|维护|服务|接入|改建|搭建|配置')
_UNRELATED = re.compile(r'煤炭采购|原煤|燃煤|药品|药剂|家具|办公桌|课桌|办公椅|土建|土石方|道路维修|道路施工|房屋施工|装修|食材|保洁')


def notice_category(title, notice_type=''):
    if re.search(r'采购意向|招标意向|拟建项目|投资计划', f'{notice_type} {title}'):
        return 'unknown'
    # The notice's category and announcement suffix outweigh words inside a system name.
    text = f'{notice_type} {title[-16:]}'
    if re.search(r'更正|变更|澄清|补充公告|延期公告', text):
        return 'change'
    if (notice_type.strip() in ('中标', '成交', '结果') or
            re.search(r'中标(?:公告|公示|结果|候选人)|成交(?:公告|公示|结果)|废标|流标|终止公告|结果公告|候选人公示', text)):
        return 'result'
    if re.search(r'招标|采购公告|磋商|谈判|询价|单一来源|资格预审', text):
        return 'procurement'
    return 'unknown'


def _industry_term_in(term, text):
    # “建筑智能化工程” is a digital scope, not evidence of the chemical industry.
    return bool(re.search(r'化工(?!程)', text)) if term == '化工' else term in text


def classify_notice(*, title, raw=b'', purchaser='', notice_type='', content_type='', source_code=''):
    """Require body evidence; headings and procurement portal boilerplate never suffice."""
    encoding = re.search(r'charset\s*=\s*[\"\']?([\w-]+)', content_type, re.I)
    codec = encoding.group(1) if encoding else 'utf-8'
    if isinstance(raw, bytes):
        # Old snapshots may declare their encoding in a meta element only.
        meta = re.search(rb'charset\s*=\s*[\"\']?([\w-]+)', raw[:4096], re.I)
        if not encoding and meta:
            codec = meta.group(1).decode('ascii')
        try:
            raw = raw.decode(codec, errors='replace')
        except LookupError:
            raw = raw.decode('utf-8', errors='replace')
    raw = notice_content(raw, source_code=source_code)
    raw = re.sub(r'<(title|header|footer|nav|aside)\b[^>]*>.*?</\1>', '', raw, flags=re.I | re.S)
    lines = html_to_lines(raw)
    evidence = []
    tags = set()
    in_scope = False
    qualification_section = False
    for line in lines:
        if re.match(r'^(?:采购人|采购单位|招标人|招标单位|代理机构|联系人|联系电话|地址|技术支持|主办单位|承办单位)\s*[:：]', line):
            in_scope = False
            continue
        if _STOP.search(line):
            in_scope = False
        if _QUALIFICATION_SECTION.search(line):
            in_scope = False
            qualification_section = True
            continue
        scope = bool(_SCOPE.search(line))
        if scope:
            in_scope = True
            qualification_section = False
        if qualification_section:
            continue
        if line.strip() == title.strip() or re.match(r'^(?:采购)?项目名称\s*[:：]', line):
            continue
        # Split prose so adjacent application instructions cannot supply evidence.
        for sentence in re.split(r'[。；;，,\n]', line):
            if (_PROCEDURE.search(sentence) or _POLICY_CITATION.search(sentence)
                    or _QUALIFICATION_EVIDENCE.search(sentence) or _BACKGROUND_ONLY.search(sentence)):
                continue
            if re.search(r'不(?:含|包含|涉及|采购|建设)|无(?:信息化|数字化|智能化)', sentence):
                continue
            if re.search(r'(?:公司|单位|机构)\s*[:：]', sentence) or re.search(r'(?:信息化|数字化|智能化)服务公司', sentence):
                continue
            matched = [tag for tag, terms in DIGITAL_TERMS.items() if any(term in sentence for term in terms)]
            if matched and (in_scope or _ACTION.search(sentence)):
                tags.update(matched)
                evidence.append(sentence.strip()[:400])
    context = f'{title} {purchaser}'
    scores = {code: sum(3 for term in terms if _industry_term_in(term, context))
              + sum(1 for term in terms if _industry_term_in(term, ' '.join(evidence)))
              for code, terms in INDUSTRY_TERMS.items()}
    maximum = max(scores.values())
    winners = [code for code, score in scores.items() if score == maximum]
    industry = winners[0] if maximum and len(winners) == 1 else ''
    if re.search(r'采购意向|招标意向|拟建项目|投资计划', f'{notice_type} {title}'):
        status = 'excluded'
        reason = '意向或拟建计划尚不是本看板范围内的公开招标采购公告'
    elif evidence:
        status = 'matched'
        reason = '公告建设或采购内容明确包含数字建设'
    elif _UNRELATED.search(title):
        status = 'excluded'
        reason = '采购对象为普通物资或土建，未发现明确数字建设内容'
    else:
        status = 'review'
        reason = '缺少明确建设内容证据，需核实公告正文或附件'
    # A supported digital sub-item is useful, but it is not the primary object
    # of a general building/renovation purchase. Never infer a tier from a title alone.
    title_digital = any(term in title for terms in DIGITAL_TERMS.values() for term in terms)
    primary_scope = any(re.search(
        r'(?:采购需求|采购内容|建设内容|标的名称)\s*[:：]\s*(?:本项目)?(?:拟|将|主要)?'
        r'(?:建设|采购|部署|开发|升级|改造|提供)?\s*[^，。；;]{0,12}'
        + re.escape(term), snippet)
        for snippet in evidence for terms in DIGITAL_TERMS.values() for term in terms)
    tier = ('core' if (title_digital or primary_scope) and not _UNRELATED.search(title)
            else 'related') if status == 'matched' else ''
    tier_reason = ('数字建设为明确主采购标的，且正文有实际建设证据' if tier == 'core' else
                   '采购范围包含数字建设子项，整体项目主旨仍需结合原文判断' if tier else
                   '缺少足够实际采购证据，暂不分层')
    return {
        'industry_code': industry, 'digital_tags': [tag for tag in DIGITAL_TERMS if tag in tags],
        'classification_status': status, 'notice_category': notice_category(title, notice_type),
        'classification_evidence': {'reason': reason, 'snippets': list(dict.fromkeys(evidence))[:12],
                                    'relevance_tier': tier, 'relevance_reason': tier_reason},
        'classification_version': CLASSIFICATION_VERSION,
    }
