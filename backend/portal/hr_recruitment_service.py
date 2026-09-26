"""Recruitment field contract adapted from HR Recruitment V1."""
REQUEST_FIELDS = {
    'position_name', 'headcount', 'responsibilities', 'required_requirements',
    'preferred_requirements', 'education_requirement', 'experience_requirement',
    'skill_requirements', 'work_location', 'notes', 'salary', 'benefits', 'social_insurance',
}
SHORT_FIELDS = {'position_name', 'education_requirement', 'experience_requirement', 'work_location'}
MISSING_LABELS = {
    'position_name': '岗位名称未填写', 'headcount': '招聘人数未明确',
    'responsibilities': '岗位职责未填写', 'required_requirements': '必备任职要求未填写',
    'education_requirement': '学历要求未明确', 'experience_requirement': '工作经验要求未明确',
    'skill_requirements': '必备技能未填写', 'work_location': '工作地点未填写',
}


class RecruitmentValidationError(ValueError):
    pass


def clean_request_data(data):
    if not isinstance(data, dict) or not set(data) <= REQUEST_FIELDS:
        raise RecruitmentValidationError('请求字段无效。')
    cleaned = {}
    for field, value in data.items():
        if field == 'headcount':
            if value is not None and (type(value) is not int or not 1 <= value <= 2147483647):
                raise RecruitmentValidationError('招聘人数必须为正整数。')
        elif field == 'skill_requirements':
            if (not isinstance(value, list) or len(value) > 30
                    or any(not isinstance(item, str) or not item.strip()
                           or len(item) > 100 or '\x00' in item for item in value)):
                raise RecruitmentValidationError('技能要求格式无效。')
            value = [item.strip() for item in value]
        else:
            limit = 200 if field in SHORT_FIELDS else 12000
            if not isinstance(value, str) or '\x00' in value or len(value) > limit:
                raise RecruitmentValidationError(f'{field}格式无效。')
            value = value.strip()
        cleaned[field] = value
    return cleaned


def missing_items(row):
    return [{'field': field, 'reason': reason} for field, reason in MISSING_LABELS.items()
            if not getattr(row, field)]


# Intake is deliberately local and conservative: unmatched facts remain verbatim
# in original_text/JD body, never guessed into screening requirements.
FIELD_LABELS = {
    'position_name': '岗位名称', 'headcount': '招聘人数', 'responsibilities': '岗位职责',
    'required_requirements': '必备任职要求', 'preferred_requirements': '加分项',
    'education_requirement': '学历要求', 'experience_requirement': '经验要求',
    'skill_requirements': '技能要求', 'work_location': '工作地点',
    'salary': '薪资', 'benefits': '福利', 'social_insurance': '社保', 'notes': '备注',
}
ALIASES = {
    'position_name': ('岗位名称', '招聘岗位', '职位名称', '职位', '岗位'),
    'responsibilities': ('岗位职责', '工作职责', '职责'),
    'required_requirements': ('必备任职要求', '任职要求', '必备要求', '硬性要求'),
    'preferred_requirements': ('加分项', '优先条件', '优先要求'),
    'education_requirement': ('学历要求', '学历'),
    'experience_requirement': ('经验要求', '工作经验'),
    'skill_requirements': ('技能要求', '必备技能', '技能'),
    'work_location': ('工作地点', '办公地点', '地点'),
    'salary': ('薪资待遇', '薪资', '工资', '月薪', '年薪'),
    'benefits': ('福利待遇', '福利'),
    'social_insurance': ('社会保险', '社保'),
    'notes': ('备注',),
}


def intake_text(value, *, maximum=40000):
    if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value) > maximum:
        raise RecruitmentValidationError(f'招聘说明须为 1～{maximum} 字的文本，不进行截断。')
    return value.strip()


def extract_requirements(text):
    """Extract only literal facts, retaining every original clause outside this snapshot."""
    import re
    from .hr_matching import is_age_requirement
    text = intake_text(text, maximum=50000)
    data = {field: [] if field == 'skill_requirements' else None if field == 'headcount' else ''
            for field in REQUEST_FIELDS}
    clauses = re.split(r'([\n，,；;。]+)', text)
    age_notes = []
    active_field = None

    def add(field, value):
        if field == 'skill_requirements':
            data[field].extend(item.strip() for item in value.split('、') if item.strip())
        elif field in SHORT_FIELDS:
            data[field] = value
        else:
            data[field] = '\n'.join(filter(None, [data[field], value]))

    for clause in clauses:
        if re.fullmatch(r'[\n，,；;。]+', clause):
            if any(char in clause for char in '\n；;。'):
                active_field = None
            continue
        clause = clause.strip(' •-')
        if not clause:
            continue
        # Age is recorded for human review, not copied into a screening field.
        if is_age_requirement(clause):
            age_notes.append(clause)
            continue
        labelled = False
        for field, labels in ALIASES.items():
            match = re.match(r'^(?:' + '|'.join(labels) + r')\s*[:：]\s*(.+)$', clause)
            if match:
                value = match.group(1).strip()
                if value not in {'待补充', '待确认', '未知', '未明确'}:
                    add(field, value)
                active_field = field if field not in SHORT_FIELDS else None
                labelled = True
                break
        if labelled:
            continue
        before = {key: list(value) if isinstance(value, list) else value for key, value in data.items()}
        preferred = bool(re.search(r'优先|加分|优选', clause))
        negated = bool(re.search(r'不要求|无需|不需要|不强制|不必', clause))
        if not data['position_name']:
            match = re.search(r'(?:招聘|想招|需要招|招)(?:一[位名个]|[位名个]|[0-9]+[位名个])?\s*(.*?(?:工程师|经理|专员|助理|设计师|开发|销售|客服|运营|会计|主管|程序员))', clause)
            if match:
                data['position_name'] = match.group(1).strip()
        match = re.search(r'(?:招聘人数\s*[:：]?\s*|招聘|招)([0-9]+)\s*[人名位]?', clause)
        if match:
            data['headcount'] = int(match.group(1))
        if not preferred and not negated and not data['education_requirement']:
            match = re.search(r'(?:博士|硕士|研究生|本科|大专|专科|高中|中专)(?:及以上|以上)?|学历不限|不限学历', clause)
            if match:
                data['education_requirement'] = match.group()
        if not preferred and not negated and not data['experience_requirement']:
            match = re.search(r'[0-9一二三四五六七八九十]+(?:[-～至到][0-9一二三四五六七八九十]+)?年(?:以上)?(?:相关)?(?:工作|开发|行业)?经验|经验不限|不限经验|应届生', clause)
            if match:
                data['experience_requirement'] = match.group()
        if re.search(r'薪|工资|[0-9]+\s*[kKwW万千元]|面议', clause):
            data['salary'] = '\n'.join(filter(None, [data['salary'], clause]))
        if re.search(r'五险|六险|社保|社会保险|公积金|一金', clause):
            data['social_insurance'] = '\n'.join(filter(None, [data['social_insurance'], clause]))
        if re.search(r'双休|单休|带薪|年假|包吃|包住|餐补|交通补|奖金|福利|补贴|年终', clause):
            data['benefits'] = '\n'.join(filter(None, [data['benefits'], clause]))
        if re.match(r'负责|承担|工作内容', clause):
            data['responsibilities'] = '\n'.join(filter(None, [data['responsibilities'], clause]))
        if not preferred and not negated and re.match(r'熟悉|掌握|熟练|精通|会用', clause):
            data['skill_requirements'].append(clause)
        if preferred:
            data['preferred_requirements'] = '\n'.join(filter(None, [data['preferred_requirements'], clause]))
        if active_field and before == data and not preferred and not negated:
            add(active_field, clause)
    if age_notes:
        data['notes'] = '\n'.join(filter(None, [data['notes'], *age_notes, '年龄信息仅作备注，不参与筛选。']))
    return clean_request_data(data)


def grounded_requirements(body, supplied=None):
    """Manual requirement corrections must still be backed by this version's text."""
    from .hr_matching import is_age_requirement
    # The original-input appendix is provenance, not a second source of
    # executable requirements after HR edits the visible JD fields.
    source = body
    for marker in ('\n\n原始招聘说明（未识别事实完整保留，非新增筛选条件）：',
                   '\n原始招聘说明（未识别项请人工核对）：'):
        source = source.split(marker, 1)[0]
    result = extract_requirements(source)
    if supplied is None:
        return result
    cleaned = clean_request_data(supplied)
    for field, value in cleaned.items():
        if field in {'notes', 'headcount'}:
            continue
        values = value if isinstance(value, list) else value.splitlines()
        if any(item and item not in source for item in values):
            raise RecruitmentValidationError('人工要求须在 JD 正文中有对应原文，请同时修改正文。')
        if field in {'required_requirements', 'preferred_requirements', 'skill_requirements',
                     'education_requirement', 'experience_requirement', 'work_location'}:
            if any(is_age_requirement(item) for item in values):
                raise RecruitmentValidationError('年龄只能放在备注，不得作为筛选条件。')
    result.update(cleaned)
    return result


def record_message(row, role, content, jd=None):
    from .hr_recruitment_models import RecruitmentMessage
    return RecruitmentMessage.objects.create(request=row, role=role, content=content,
                                             input_version=row.input_version, jd_version=jd)


def pending_fields(requirements):
    return [{'field': field, 'reason': reason} for field, reason in MISSING_LABELS.items()
            if not requirements.get(field)]


def readable_fields(requirements):
    """Human-readable labels for JD facts and structured-form conversation turns."""
    lines = []
    for field, label in FIELD_LABELS.items():
        if field not in requirements:
            continue
        value = requirements[field]
        if isinstance(value, list):
            value = '、'.join(value)
        lines.append(f'{label}：{value if value else "待确认"}')
    return '\n'.join(lines)


def intake_draft(text, requirements):
    return ('通用 JD 草稿\n来源：本地规则提取与整理（未调用 AI）；请人工核对。\n\n'
            + readable_fields(requirements)
            + '\n\n原始招聘说明（未识别事实完整保留，非新增筛选条件）：\n' + text
            + '\n\n请人工核对正文和提取要求后确认。年龄信息仅作备注，不参与筛选。')


def fact_appendix(row):
    """Keep supplied facts even if the model's prose omits a field."""
    text = '\n\n已提供事实与待确认项：\n' + readable_fields(row.structured_payload())
    if row.original_text:
        text += '\n原始招聘说明（未识别项请人工核对）：\n' + row.original_text
    return text
