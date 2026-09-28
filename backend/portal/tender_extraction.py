"""Bounded public-document enrichment. Network calls must run outside DB transactions.

Only the source adapter's existing allowlisted outbound client may fetch attachments.
PDF and DOCX parsers run as our own isolated child process, never as document programs.
Extraction is evidence, not permission to guess an absent deadline or budget.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 3
MAX_TEXT_CHARS = 120_000
MAX_PDF_PAGES = 50
MAX_EXPANDED_BYTES = 8 * 1024 * 1024
EXTRACTABLE_FIELDS = frozenset({'bid_deadline', 'bid_open_at', 'signup_time', 'budget', 'budget_cap', 'procurement_scope'})
DETAILS = {
    'unsupported_type': '附件类型暂不支持自动提取，请查看公开原件。',
    'size_limit': '附件超过安全体积限制，请查看公开原件。',
    'parse_failed': '附件格式无法可靠解析，请查看公开原件。',
    'parse_timeout': '附件解析超时，已停止处理，请查看公开原件。',
    'parser_unavailable': '附件解析组件暂不可用，请查看公开原件。',
    'encrypted': '附件已加密，无法提取公开正文。',
    'no_text': '附件未检测到可读文字，扫描件需人工核实。',
    'page_limit': '附件页数超过安全提取限制，请查看公开原件。',
    'fetch_failed': '公开附件暂时无法获取，请查看原文链接。',
    'not_allowed': '附件不在该来源已核实的公开域名范围内，未下载。',
    'no_client': '附件尚未抓取，请查看公开原件。',
    'count_limit': '本轮附件数量已达安全上限，其余附件待核实。',
}


class ExtractionError(ValueError):
    pass


@dataclass
class ExtractionResult:
    normalized: object
    text: str
    evidence: list
    warnings: list

    def to_dict(self):
        return {'fields': {name: item.to_dict() for name, item in self.normalized.fields.items()
                           if name in EXTRACTABLE_FIELDS},
                'text': self.text, 'evidence': self.evidence, 'warnings': self.warnings}


def _document_kind(url, label=''):
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix not in {'.pdf', '.docx', '.txt'}:
        suffix = Path(label).suffix.lower()
    return suffix.removeprefix('.') if suffix in {'.pdf', '.docx', '.txt'} else ''


def _parse_document(body, kind):
    """Child-only parser; bounded input/output, no link resolution or file extraction."""
    if len(body) > MAX_ATTACHMENT_BYTES:
        raise ExtractionError('size_limit')
    if kind == 'txt':
        if b'\x00' in body or body.lstrip().startswith((b'<html', b'<!DOCTYPE', b'<script')):
            raise ExtractionError('parse_failed')
        try:
            text = body.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = body.decode('gb18030')
    elif kind == 'docx':
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if (len(entries) > 2000 or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES
                    or any(item.flag_bits & 1 for item in entries)):
                raise ExtractionError('size_limit')
            if any('vbaproject' in item.filename.lower() for item in entries):
                raise ExtractionError('unsupported_type')
            with archive.open('word/document.xml') as document:
                xml = document.read(MAX_EXPANDED_BYTES + 1)
            if len(xml) > MAX_EXPANDED_BYTES:
                raise ExtractionError('size_limit')
            if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise ExtractionError('parse_failed')
            root = ElementTree.fromstring(xml)
            namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(item.text or '' for item in paragraph.iter(namespace + 't'))
                             for paragraph in root.iter(namespace + 'p'))
    elif kind == 'pdf':
        if not body.startswith(b'%PDF-'):
            raise ExtractionError('parse_failed')
        from pypdf import PdfReader, filters
        # pypdf enforces this bound during flate decompression, before allocating
        # an unbounded decoded page stream. This override lives only in the child.
        filters.ZLIB_MAX_OUTPUT_LENGTH = MAX_EXPANDED_BYTES
        reader = PdfReader(io.BytesIO(body), strict=True)
        if reader.is_encrypted:
            raise ExtractionError('encrypted')
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ExtractionError('page_limit')
        parts = []
        for page in reader.pages:
            content = page.get_contents()
            if content is not None and len(content.get_data()) > MAX_EXPANDED_BYTES:
                raise ExtractionError('size_limit')
            parts.append(page.extract_text() or '')
            if sum(map(len, parts)) > MAX_TEXT_CHARS:
                raise ExtractionError('size_limit')
        text = '\n'.join(parts)
    else:
        raise ExtractionError('unsupported_type')
    if len(text) > MAX_TEXT_CHARS:
        raise ExtractionError('size_limit')
    if not text.strip():
        raise ExtractionError('no_text')
    return text.strip()


def extract_attachment_text(body, *, kind):
    if len(body) > MAX_ATTACHMENT_BYTES:
        raise ExtractionError('size_limit')
    if kind not in {'pdf', 'docx', 'txt'}:
        raise ExtractionError('unsupported_type')
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    try:
        completed = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), kind],
                                   input=body, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=15, check=False, **kwargs)
    except subprocess.TimeoutExpired as error:
        raise ExtractionError('parse_timeout') from error
    if completed.returncode or len(completed.stdout) > MAX_TEXT_CHARS * 8:
        raise ExtractionError('parse_failed')
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, UnicodeDecodeError) as error:
        raise ExtractionError('parse_failed') from error
    if payload.get('error'):
        raise ExtractionError(payload['error'])
    return payload['text']


def extract_public_fields(raw, *, source_url='', encoding='utf-8'):
    """Return conservative supplementary fields with exact snippets and provenance."""
    from .tender_normalize import FieldResult, _DATE_RE, _parse_datetime_parts, html_to_lines, parse_amount
    original_lines = html_to_lines(raw, encoding=encoding)
    # Match labels split by editor spans while keeping original snippets as evidence.
    lines = [re.sub(r'(?<=[\u4e00-\u9fff\d])\s+(?=[\u4e00-\u9fff\d])', '', line)
             for line in original_lines]
    fields = {}
    date_patterns = {
        'bid_deadline': r'(?:提交|递交)?(?:投标文件|响应文件|报价文件|投标|报价)(?:递交|提交)?(?:的)?截止时间',
        'bid_open_at': r'开标时间|开启时间',
        'signup_time': r'报名(?:截止)?时间|获取(?:采购|招标|磋商)文件(?:的)?时间|文件获取时间',
    }
    for name, pattern in date_patterns.items():
        candidates = []
        acquisition = False
        range_ends = []
        range_invalid = False
        for index, line in enumerate(lines):
            if re.search(r'(?:招标|采购|磋商|询价)文件(?:的)?获取|获取(?:招标|采购|磋商|询价)文件', line):
                acquisition = True
            if re.match(r'^\s*\d+\s*[.、．]\s*(?:投标|响应|报价|开标|资格|联系方式)', line):
                acquisition = False
            match = re.search(pattern, line)
            if name == 'signup_time' and acquisition and not match:
                match = re.search(r'获取时间', line)
            if not match:
                continue
            snippet = original_lines[index]
            tail = line[match.end():].strip(' ：:（）()')
            if (not _parse_datetime_parts(tail) and index + 1 < len(lines)
                    and (not tail or re.fullmatch(r'(?:[（(]?同(?:投标|响应文件|开标)?截止时间[）)]?\s*)?[为是：:]*', tail))):
                tail = lines[index + 1]
                snippet += ' ' + original_lines[index + 1]
            parsed = _parse_datetime_parts(tail)
            if parsed:
                value, precision = parsed
                candidates.append((value, precision, snippet[:600]))
                if name == 'signup_time':
                    date_matches = list(_DATE_RE.finditer(re.sub(r'(?<=\d)\s+(?=\d)', '', tail)))
                    if len(date_matches) == 2:
                        end = _parse_datetime_parts(date_matches[1].group())
                        if end and end[0] >= value:
                            range_ends.append(end[0])
                        else:
                            range_invalid = True
        if not candidates:
            continue
        distinct = {(value, precision) for value, precision, _ in candidates}
        value, precision, snippet = candidates[0]
        conflict = len(distinct) > 1 or len(set(range_ends)) > 1 or range_invalid
        verified = not conflict and (precision != 'date' or name == 'signup_time')
        fields[name] = FieldResult(name, value if verified else None, snippet,
                                  'OK' if verified else 'UNKNOWN', 'public_document:explicit_date',
                                  {'source_url': source_url, 'precision': precision,
                                   'verification_status': '已提取' if verified else '待核实',
                                   'conflict': conflict,
                                   'snippets': [item[2] for item in candidates][:6]})
        if name == 'signup_time' and verified and len(set(range_ends)) == 1:
            fields[name].extra.update(start_at=value, end_at=range_ends[0])
    for name, pattern in {'budget': r'预算金额|采购预算|预算总额|项目预算',
                          'budget_cap': r'最高投标限价|最高限价|控制价|拦标价'}.items():
        candidates = []
        for index, line in enumerate(lines):
            match = re.search(pattern, line)
            if not match:
                continue
            tail = line[match.end():].strip(' ：:')
            # Tables commonly put the unit in the heading and number in the next cell.
            if not re.search(r'\d', tail) and index + 1 < len(lines):
                tail += ' ' + lines[index + 1]
            unit = re.search(r'[（(]\s*(亿元|万元|元)\s*[)）]', tail)
            amount_text = re.sub(r'[（(].*?[)）]', '', tail)
            if unit and not re.search(r'\d\s*(?:亿|万|元)', amount_text):
                amount_text = amount_text.strip() + unit.group(1)
            parsed = parse_amount(amount_text)
            if parsed and parsed.get('amount_yuan') is not None:
                candidates.append((parsed, (line + ' ' + tail)[:600]))
        if candidates:
            amounts = {item[0]['amount_yuan'] for item in candidates}
            parsed, snippet = candidates[0]
            fields[name] = FieldResult(name, parsed['raw'] if len(amounts) == 1 else None, snippet,
                                      'OK' if len(amounts) == 1 else 'UNKNOWN', 'public_document:explicit_amount',
                                      {**parsed, 'amount_yuan': parsed['amount_yuan'] if len(amounts) == 1 else None,
                                       'source_url': source_url, 'conflict': len(amounts) > 1,
                                       'verification_status': '已提取' if len(amounts) == 1 else '待核实'})
    scope = []
    active = False
    for line in lines:
        if re.search(r'采购需求|建设内容|采购内容|招标范围|服务内容|技术要求|施工范围', line):
            active = True
        if re.search(r'资格要求|资格条件|文件获取|获取.{0,8}文件|投标截止|联系方式|代理机构'
                     r'|预算金额|采购预算|最高限价|开标时间|报名时间|项目编号|采购人', line):
            active = False
        if active and not re.search(r'登录|注册|下载|上传投标文件', line):
            # A real scope followed by “具体要求详见附件” still supplies evidence;
            # a reference-only heading supplies none.
            content = re.split(r'[，；;。](?=[^，；;。]{0,25}详见)', line, maxsplit=1)[0]
            if re.search(r'详见.{0,12}(?:附件|文件)', content):
                continue
            if re.fullmatch(r'[\d.、．\s]*(?:本项目)?(?:采购需求|建设内容|采购内容(?:和范围)?|招标范围|服务内容|技术要求|施工范围)[:：]?', content):
                continue
            scope.append(content[:600])
            if len(scope) == 10:
                break
    if scope:
        snippet = '\n'.join(scope)
        fields['procurement_scope'] = FieldResult('procurement_scope', snippet, snippet, 'OK',
                                                'public_document:scope', {'source_url': source_url})
    return fields


def _merge_field(normalized, name, incoming):
    from .tender_normalize import FieldResult
    existing = normalized.fields.get(name)
    if existing and existing.extra.get('conflict'):
        return
    if incoming.extra.get('conflict'):
        normalized.fields[name] = incoming
        return
    if (name in {'bid_deadline', 'bid_open_at'} and incoming.status == 'UNKNOWN'
            and incoming.extra.get('precision') == 'date'):
        normalized.fields[name] = incoming
        return
    if not existing or existing.status != 'OK' or (name in {'budget', 'budget_cap'} and
                                                  not existing.extra.get('amount_yuan')):
        normalized.fields[name] = incoming
        return
    if incoming.status != 'OK' or name == 'procurement_scope':
        return
    before = existing.extra.get('amount_yuan') if name in {'budget', 'budget_cap'} else existing.value
    after = incoming.extra.get('amount_yuan') if name in {'budget', 'budget_cap'} else incoming.value
    if before != after:
        normalized.fields[name] = FieldResult(name, raw=existing.raw, rule='public_document:conflict',
                                             extra={'conflict': True, 'verification_status': '待核实',
                                                    'snippets': [existing.raw[:600], incoming.raw[:600]],
                                                    'source_url': incoming.extra.get('source_url', '')})


def apply_extraction(normalized, payload):
    """Apply only enrichment fields; never override publication, identity or source trust."""
    from .tender_normalize import FieldResult
    for name, item in payload.get('fields', {}).items():
        if name not in EXTRACTABLE_FIELDS or not isinstance(item, dict):
            continue
        incoming = FieldResult(name, item.get('value'), item.get('raw', ''), item.get('status', 'UNKNOWN'),
                               item.get('rule', ''), {key: value for key, value in item.items()
                                                     if key not in {'value', 'raw', 'status', 'rule'}})
        _merge_field(normalized, name, incoming)
    normalized.warnings.extend(payload.get('warnings', []))
    return normalized


def enrich_notice(normalized, *, raw='', attachment_client=None, encoding='utf-8', heartbeat=None):
    from .tender_normalize import notice_content
    raw = notice_content(raw, source_code=normalized.source_ref.get('source_code', ''), encoding=encoding)
    source_url = normalized.source_ref.get('original_url', '')
    for name, item in extract_public_fields(raw, source_url=source_url, encoding=encoding).items():
        _merge_field(normalized, name, item)
    evidence, warnings, texts, seen = [], [], [], set()
    for attachment in normalized.attachments:
        url = str(attachment.get('url') or '')
        if not url or url in seen:
            continue
        seen.add(url)
        item = {'url': url, 'label': str(attachment.get('label') or '')[:200]}
        if len(seen) > MAX_ATTACHMENTS:
            item.update(status='pending', code='count_limit', detail=DETAILS['count_limit'])
            evidence.append(item)
            break
        try:
            kind = _document_kind(url, item['label'])
            if not kind:
                raise ExtractionError('unsupported_type')
            if attachment_client is None:
                raise ExtractionError('no_client')
            policy = attachment_client.policy
            parsed_url = urlsplit(url)
            origin = f'{parsed_url.scheme}://{parsed_url.netloc}'
            if (parsed_url.scheme not in {'http', 'https'} or parsed_url.username or parsed_url.password
                    or origin not in policy.allowed_origins):
                raise ExtractionError('not_allowed')
            if policy.max_bytes > MAX_ATTACHMENT_BYTES:
                raise ExtractionError('size_limit')
            if heartbeat:
                heartbeat.guard()
            response = attachment_client.fetch(url)
            if heartbeat:
                heartbeat.guard()
            mime = str(getattr(response, 'content_type', '')).split(';', 1)[0].lower().strip()
            accepted = {'application/octet-stream', 'binary/octet-stream', ''}
            accepted.update({'pdf': {'application/pdf'}, 'txt': {'text/plain'},
                             'docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                      'application/zip'}}[kind])
            # Verified QinYuan public download returns a DOCX ZIP with this
            # generic MIME. Limit compatibility to that exact public endpoint;
            # ZIP structure, expansion limits and macro checks still run below.
            if (kind == 'docx' and normalized.source_ref.get('source_code') == 'qinyuan'
                    and origin == 'https://qyzb.shccmg.com'
                    and parsed_url.path == '/ebidding/api/base/file/withoutPermission/download'
                    and response.body.startswith(b'PK\x03\x04')):
                accepted.add('application/x-msdownload')
            if mime not in accepted:
                raise ExtractionError('unsupported_type')
            text = extract_attachment_text(response.body, kind=kind)
            texts.append(text)
            item.update(status='extracted', sha256=hashlib.sha256(response.body).hexdigest(),
                        bytes=len(response.body), chars=len(text), detail='已提取公开附件文字')
            for name, field in extract_public_fields(html.escape(text), source_url=url).items():
                _merge_field(normalized, name, field)
        except ExtractionError as error:
            code = str(error)
            item.update(status='unverified', code=code, detail=DETAILS.get(code, DETAILS['parse_failed']))
            warnings.append(item['detail'])
        except Exception as error:
            from .tender_runtime import LeaseLost
            if isinstance(error, LeaseLost):
                raise
            item.update(status='unverified', code='fetch_failed', detail=DETAILS['fetch_failed'])
            warnings.append(item['detail'])
        evidence.append(item)
    return ExtractionResult(normalized, '\n'.join(texts)[:MAX_TEXT_CHARS], evidence, list(dict.fromkeys(warnings)))


if __name__ == '__main__':
    try:
        content = sys.stdin.buffer.read(MAX_ATTACHMENT_BYTES + 1)
        result = {'text': _parse_document(content, sys.argv[1])}
    except ImportError:
        result = {'error': 'parser_unavailable'}
    except ExtractionError as error:
        result = {'error': str(error)}
    except Exception:
        result = {'error': 'parse_failed'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
