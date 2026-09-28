"""Portable public notices only; never deserialize arbitrary models or database PKs."""
import gzip
import json
import os
import tempfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .tender_models import TenderNotice, TenderNoticeVersion, TenderOpportunity, TenderSource
from .tender_sources import registered_adapters

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
DEFAULT_BUNDLE = Path(__file__).resolve().parents[2] / 'data/demo/tender-public.json.gz'
SOURCE_FIELDS = 'code name base_url adapter_code'.split()
NOTICE_FIELDS = ('source_notice_id canonical_key title notice_type original_url publish_at publish_date '
                 'publish_precision first_seen_at last_seen_at current_version created_at updated_at').split()
VERSION_FIELDS = 'version content_hash normalized attachments change_summary created_at'.split()
OPPORTUNITY_FIELDS = ('opportunity_key project_group_key project_name project_code purchaser agency region '
    'notice_type procurement_method industry_code digital_tags classification_status notice_category '
    'classification_evidence extraction_evidence classification_version budget_amount_yuan budget_cap_yuan '
    'budget_raw publish_at publish_date publish_precision bid_deadline bid_open_at signup_time_text '
    'contact_person contact_phone attachment_count attachment_urls unknown_fields possible_match_keys '
    'status current_version first_seen_at created_at updated_at').split()
TOP_FIELDS = {'schema_version', 'exported_at', 'sources', 'notices', 'versions', 'opportunities'}


def _values(row, fields):
    return {name: getattr(row, name) for name in fields}


def _public_url(source_code, row):
    # Reuse the exact official detail URL boundary used by the board itself.
    from .tender_api import _official_notice_url
    source = TenderSource(pk=1, code=source_code)
    notice = TenderNotice(source=source, source_notice_id=row['source_notice_id'], original_url=row['original_url'])
    return bool(_official_notice_url(notice))


@transaction.atomic
def _export_rows():
    adapters = registered_adapters()
    notices = [row for row in TenderNotice.objects.filter(source__code__in=adapters).select_related('source').order_by('id')
               if _public_url(row.source.code, _values(row, NOTICE_FIELDS))]
    notice_refs = {row.pk: [row.source.code, row.source_notice_id] for row in notices}
    versions = list(TenderNoticeVersion.objects.filter(notice_id__in=notice_refs).order_by('notice_id', 'version'))
    version_refs = {row.pk: [*notice_refs[row.notice_id], row.version] for row in versions}
    opportunities = list(TenderOpportunity.objects.filter(primary_notice_id__in=notice_refs).select_related('source').order_by('id'))
    codes = sorted({row.source.code for row in notices})
    return {
        'schema_version': SCHEMA_VERSION, 'exported_at': timezone.now(),
        'sources': [{'code': code, 'name': adapters[code].name, 'base_url': adapters[code].entry_url,
                     'adapter_code': code} for code in codes],
        'notices': [{**_values(row, NOTICE_FIELDS), 'source_code': row.source.code} for row in notices],
        'versions': [{**_values(row, VERSION_FIELDS), 'source_code': notice_refs[row.notice_id][0],
                      'source_notice_id': notice_refs[row.notice_id][1]} for row in versions],
        'opportunities': [{**_values(row, OPPORTUNITY_FIELDS),
            'source_code': notice_refs[row.primary_notice_id][0],
            'primary_notice_ref': notice_refs[row.primary_notice_id],
            'classification_notice_version_ref': version_refs.get(row.classification_notice_version_id)} for row in opportunities],
    }


def export_bundle():
    # Convert dates and Decimals after releasing the consistent database snapshot.
    data = json.loads(json.dumps(_export_rows(), cls=DjangoJSONEncoder, ensure_ascii=False, allow_nan=False))
    return validate_bundle(data)


def _shape(row, keys, label):
    if not isinstance(row, dict) or set(row) != set(keys):
        raise ValueError(f'{label} 字段不符合公开演示包格式')


def _fields(row, model, names):
    for name in names:
        field = model._meta.get_field(name)
        value = row[name]
        kind = field.get_internal_type()
        if value is None:
            if not field.null:
                raise ValueError(f'{model.__name__}.{name} 不能为空')
            continue
        if kind in ('CharField', 'SlugField', 'URLField', 'DateTimeField', 'DateField', 'DecimalField') and not isinstance(value, str):
            raise ValueError(f'{name} 格式无效')
        if 'IntegerField' in kind and (type(value) is not int or not 0 <= value <= 2147483647):
            raise ValueError(f'{name} 必须为有界整数')
        if kind == 'JSONField':
            expected = list if name in ('attachments', 'change_summary', 'digital_tags', 'attachment_urls', 'unknown_fields', 'possible_match_keys') else dict
            if not isinstance(value, expected):
                raise ValueError(f'{name} JSON 类型无效')
        try:
            field.clean(value, None)
        except (ValidationError, ValueError, TypeError, OverflowError):
            raise ValueError(f'{model.__name__}.{name} 格式无效') from None
        if kind == 'DateTimeField' and timezone.is_naive(parse_datetime(value)):
            raise ValueError(f'{name} 必须包含时区')


def _ref(value, length, label):
    if (not isinstance(value, list) or len(value) != length
            or any(not isinstance(part, str) or not part for part in value[:2])
            or (length == 3 and type(value[2]) is not int)):
        raise ValueError(f'{label} 引用无效')
    return tuple(value)


def validate_bundle(data):
    _shape(data, TOP_FIELDS, '演示包')
    if type(data['schema_version']) is not int or data['schema_version'] != SCHEMA_VERSION:
        raise ValueError('不支持的演示包版本')
    try:
        stamp = parse_datetime(data['exported_at'])
        if stamp is None or timezone.is_naive(stamp):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('演示包导出时间无效') from None
    for name in ('sources', 'notices', 'versions', 'opportunities'):
        if not isinstance(data[name], list) or len(data[name]) > 50000:
            raise ValueError(f'{name} 数量或格式无效')
    codes, notices, versions, hashes, opportunity_keys = set(), {}, {}, set(), set()
    adapters = registered_adapters()
    for row in data['sources']:
        _shape(row, SOURCE_FIELDS, '来源')
        _fields(row, TenderSource, SOURCE_FIELDS)
        code = row['code']
        if code in codes or code not in adapters or row['adapter_code'] != code or row['base_url'] != adapters[code].entry_url:
            raise ValueError('来源重复或不是已支持的官方来源')
        codes.add(code)
    for row in data['notices']:
        _shape(row, [*NOTICE_FIELDS, 'source_code'], '公告')
        _fields(row, TenderNotice, NOTICE_FIELDS)
        key = _ref([row['source_code'], row['source_notice_id']], 2, '公告')
        if key in notices or row['source_code'] not in codes or not _public_url(row['source_code'], row):
            raise ValueError('公告重复、来源缺失或原文不是官方链接')
        notices[key] = row
    for row in data['versions']:
        _shape(row, [*VERSION_FIELDS, 'source_code', 'source_notice_id'], '版本')
        _fields(row, TenderNoticeVersion, VERSION_FIELDS)
        key = _ref([row['source_code'], row['source_notice_id'], row['version']], 3, '版本')
        hash_key = (*key[:2], row['content_hash'])
        if key in versions or hash_key in hashes or key[:2] not in notices or row['version'] < 1:
            raise ValueError('版本重复或引用公告缺失')
        versions[key] = row
        hashes.add(hash_key)
    for key, row in notices.items():
        if row['current_version'] and (*key, row['current_version']) not in versions:
            raise ValueError('公告当前版本缺失')
    for row in data['opportunities']:
        _shape(row, [*OPPORTUNITY_FIELDS, 'source_code', 'primary_notice_ref', 'classification_notice_version_ref'], '商机')
        _fields(row, TenderOpportunity, OPPORTUNITY_FIELDS)
        key = row['opportunity_key']
        notice_key = _ref(row['primary_notice_ref'], 2, '商机公告')
        if (key in opportunity_keys or notice_key not in notices or row['source_code'] != notice_key[0]
                or key != notices[notice_key]['canonical_key']):
            raise ValueError('商机重复或公告关联无效')
        classification = row['classification_notice_version_ref']
        if classification is not None:
            version_key = _ref(classification, 3, '分类版本')
            if (version_key not in versions
                    or notices[version_key[:2]]['canonical_key'] != key):
                raise ValueError('分类版本缺失或属于其他项目')
        opportunity_keys.add(key)
    return data


def _create(model, values, **relations):
    dates = {name: values[name] for name in ('created_at', 'updated_at') if name in values}
    instance = model.objects.create(**values, **relations)
    if dates:
        model.objects.filter(pk=instance.pk).update(**dates)
    return instance


def import_bundle(data, *, check_only=False):
    validate_bundle(data)
    counts = {name: 0 for name in ('sources', 'notices', 'versions', 'opportunities')}
    if check_only:
        return counts
    # Never import while this database's collection is mutating notice history.
    from .tender_models import TenderFetchRun, TenderManualRefresh
    with transaction.atomic():
        if (TenderFetchRun.objects.filter(state='RUNNING').exists()
                or TenderManualRefresh.objects.filter(state__in=['QUEUED', 'RUNNING']).exists()):
            raise ValueError('请先停止目标电脑采集并确认无活动批次，再导入演示包')
        sources, notices, versions, new_notices = {}, {}, {}, set()
        for row in data['sources']:
            code = row['code']
            source, created = TenderSource.objects.get_or_create(code=code, defaults={
                **{name: row[name] for name in SOURCE_FIELDS if name != 'code'}, 'enabled': False,
                'health_state': 'unknown', 'health_detail': f'已导入公开公告演示快照（{data["exported_at"]}），尚未验证本机来源连通性。'})
            sources[code] = source
            counts['sources'] += created
        for row in data['notices']:
            key = (row['source_code'], row['source_notice_id'])
            notice = TenderNotice.objects.filter(source=sources[key[0]], source_notice_id=key[1]).first()
            if notice is None:
                notice = _create(TenderNotice, {name: row[name] for name in NOTICE_FIELDS}, source=sources[key[0]])
                new_notices.add(key)
                counts['notices'] += 1
            notices[key] = notice
        previous = {}
        for row in sorted(data['versions'], key=lambda item: (item['source_code'], item['source_notice_id'], item['version'])):
            key = (row['source_code'], row['source_notice_id'])
            version_key = (*key, row['version'])
            if key in new_notices:
                version = _create(TenderNoticeVersion, {name: row[name] for name in VERSION_FIELDS},
                                  notice=notices[key], supersedes=previous.get(key))
                previous[key] = version
                counts['versions'] += 1
            else:
                version = notices[key].versions.filter(version=row['version'], content_hash=row['content_hash']).first()
            versions[version_key] = version
        for row in data['opportunities']:
            notice = notices[tuple(row['primary_notice_ref'])]
            if (TenderOpportunity.objects.filter(opportunity_key=row['opportunity_key']).exists()
                    or TenderOpportunity.objects.filter(primary_notice=notice).exists()):
                continue
            classification = row['classification_notice_version_ref']
            _create(TenderOpportunity, {name: row[name] for name in OPPORTUNITY_FIELDS},
                    source=sources[row['source_code']], primary_notice=notice,
                    classification_notice_version=versions.get(tuple(classification)) if classification else None)
            counts['opportunities'] += 1
    return counts


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 存在重复字段')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('演示包包含非标准 JSON 数值')


def load_bundle(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('演示包文件超过 16 MiB')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('演示包解压后超过 64 MiB')
    try:
        return validate_bundle(json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_keys,
                                          parse_constant=_invalid_constant))
    except (RecursionError, UnicodeError):
        raise ValueError('演示包编码或嵌套结构无效') from None


def write_bundle(data, path):
    validate_bundle(data)
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('演示包超过 64 MiB')
    path = Path(path)
    content = gzip.compress(raw, mtime=0) if path.suffix == '.gz' else raw
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('演示包文件超过 16 MiB，请使用 .json.gz 压缩包')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
