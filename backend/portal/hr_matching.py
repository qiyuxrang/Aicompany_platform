"""Evidence-based HR matching; model verdicts never decide employment."""
import json
import re

PROFILE_FIELDS = ('name', 'contact', 'education', 'work_experience', 'projects', 'skills',
                  'certificates', 'industry_experience', 'management_experience', 'languages', 'other_verifiable')
RULE_VERSION = 'rule-v1-no-age-20260926'
VALUES = {'MATCH': 1, 'PARTIAL': .5, 'NOT_MATCH': 0, 'UNKNOWN': None}


def _object(text):
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError('invalid_model_output') from None
    if not isinstance(value, dict):
        raise ValueError('invalid_model_output')
    return value


def is_age_requirement(text):
    # Human age notes are retained in source/JD only, never scored or excluded.
    return bool(re.search(r'年龄|周岁|[零〇一二三四五六七八九十百\d]+\s*岁|\bage\b|years?\s*old|year[- ]olds?|[0-9]{2}后|出生|birth', text, re.I))


def requirements_for(data):
    result = []
    for field in ('education_requirement', 'experience_requirement', 'skill_requirements',
                  'required_requirements', 'work_location', 'preferred_requirements'):
        value = data.get(field, []) if field == 'skill_requirements' else data.get(field, '').splitlines()
        for index, text in enumerate(value):
            if text.strip() and not is_age_requirement(text):
                result.append({'id': f'{field}#{index}', 'text': text.strip(),
                               'category': 'bonus' if field == 'preferred_requirements' else 'hard'})
    return result


def _evidence(entries, text):
    if not isinstance(entries, list):
        return []
    result = []
    for item in entries[:20]:
        if not isinstance(item, dict):
            continue
        quote = item.get('quote')
        if isinstance(quote, str) and quote.strip() and len(quote) <= 2000 and quote in text:
            offset = text.index(quote)
            result.append({'quote': quote, 'locator': f'chars:{offset}-{offset + len(quote)}'})
    return result


def parse_profile(output, text):
    data = _object(output)
    result = {}
    for field in PROFILE_FIELDS:
        entry = data.get(field)
        if not isinstance(entry, dict):
            entry = {}
        evidence = _evidence([entry.get('source_ref')], text)
        value = entry.get('value')
        valid = isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value))
        if entry.get('status') == 'extracted' and valid and evidence:
            result[field] = {'value': value, 'status': 'extracted', 'source_ref': evidence[0]}
        else:
            result[field] = {'value': None, 'status': 'unknown', 'source_ref': None}
    return result


def match_matrix(output, required, text):
    entries = _object(output).get('requirements')
    if not isinstance(entries, list) or len(entries) > 200:
        raise ValueError('invalid_model_output')
    by_id = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('requirement_id'), str):
            raise ValueError('invalid_model_output')
        key = entry['requirement_id']
        if key in by_id:
            raise ValueError('invalid_model_output')
        by_id[key] = entry
    result = []
    for requirement in required:
        if is_age_requirement(requirement['text']):
            continue
        entry = by_id.get(requirement['id'], {})
        evidence = _evidence(entry.get('evidence'), text)
        verdict = entry.get('verdict')
        if not isinstance(verdict, str) or verdict not in VALUES or not evidence:
            verdict, evidence = 'UNKNOWN', []
        result.append({**requirement, 'verdict': verdict, 'evidence': evidence})
    return result


def score_matrix(matrix):
    total = judged = full = 0
    unknown, gaps = 0, []
    for row in matrix:
        if is_age_requirement(row.get('text', '')):
            continue
        weight = .5 if row['category'] == 'bonus' else 1
        full += weight
        value = VALUES[row['verdict']]
        if value is None:
            unknown += 1
        else:
            total += value * weight
            judged += weight
            if row['category'] == 'hard' and row['verdict'] == 'NOT_MATCH':
                gaps.append(row['id'])
    return {'total': total, 'judged_max': judged, 'full_max': full, 'unknown_count': unknown,
            'hard_gap': gaps, 'rule_version': RULE_VERSION}
