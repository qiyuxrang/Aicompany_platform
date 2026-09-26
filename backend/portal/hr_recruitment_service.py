"""Recruitment field contract adapted from HR Recruitment V1."""
REQUEST_FIELDS = {
    'position_name', 'headcount', 'responsibilities', 'required_requirements',
    'preferred_requirements', 'education_requirement', 'experience_requirement',
    'skill_requirements', 'work_location', 'notes',
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
            if type(value) is not int or not 1 <= value <= 2147483647:
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
