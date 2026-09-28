import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from portal.tender_classification import classify_notice
from portal.tender_extraction import (ExtractionError, MAX_ATTACHMENT_BYTES, apply_extraction,
                                      enrich_notice, extract_attachment_text, extract_public_fields)
from portal.tender_normalize import FieldResult, normalize_notice, notice_content, parse_amount


def docx_bytes(text):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>' + ''.join(f'<w:p><w:r><w:t>{line}</w:t></w:r></w:p>' for line in text.splitlines())
            + '</w:body></w:document>'))
    return output.getvalue()


def pdf_bytes(*, encrypted=False, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 10 200 Td (Public procurement scope) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('private-password')
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class EnrichmentTests(SimpleTestCase):
    def test_real_qinyuan_docx_generic_download_mime_is_extracted_without_inventing_budget(self):
        url = ('https://qyzb.shccmg.com/ebidding/api/base/file/withoutPermission/download'
               '?fileId=2100056705315205121&fileName=投标承诺书.docx')
        body = (Path(__file__).parent / 'fixtures' / 'qinyuan_20260929' / 'public-commitment.docx').read_bytes()
        normalized = normalize_notice(f'<a href="{url}">投标承诺书.docx</a>', source_code='qinyuan',
                                      original_url='https://qyzb.shccmg.com/cms/default/webfile/1ywgg/20260924/1287816057720930304.html')
        client = SimpleNamespace(policy=SimpleNamespace(allowed_origins={'https://qyzb.shccmg.com'}, max_bytes=2_000_000),
                                 fetch=Mock(return_value=SimpleNamespace(body=body, content_type='application/x-msdownload')))
        result = enrich_notice(normalized, raw='', attachment_client=client)
        self.assertEqual(result.evidence[0]['status'], 'extracted')
        self.assertIn('承诺', result.text)
        self.assertIsNone(normalized.value_of('budget'))
        # Same MIME on a different endpoint or an executable is not accepted.
        for bad_url, bad_body in ((url.replace('/withoutPermission/download', '/other/download'), body),
                                  (url, b'MZ executable bytes')):
            normalized.attachments[0]['url'] = bad_url
            client.fetch.return_value = SimpleNamespace(body=bad_body, content_type='application/x-msdownload')
            rejected = enrich_notice(normalized, raw='', attachment_client=client)
            self.assertEqual(rejected.evidence[0]['code'], 'unsupported_type')

    def normalized(self, body=''):
        return normalize_notice(body, source_code='test', original_url='https://public.example/notice/1')

    def test_primary_digital_and_incidental_subscope_are_distinguished(self):
        core = classify_notice(title='医院信息系统采购公告', raw='<p>采购需求：建设医院信息系统。</p>')
        related = classify_notice(title='医院病房楼装修采购公告',
                                  raw='<p>建设内容：装饰装修及给排水施工，安装视频监控系统。</p>')
        self.assertEqual(core['classification_evidence']['relevance_tier'], 'core')
        self.assertEqual(related['classification_evidence']['relevance_tier'], 'related')
        missing = classify_notice(title='医院信息系统采购公告', raw='内容详见附件')
        self.assertEqual(missing['classification_evidence']['relevance_tier'], '')

    def test_real_yuneng_smart_speed_control_is_core_but_procedural_mentions_are_not(self):
        raw = (Path(__file__).parent / 'fixtures' / 'yuneng_20260929' / 'detail-52241.json').read_bytes()
        title = '榆林市榆神煤炭榆树湾煤矿有限公司主运系统煤流智能调速系统改造招标公告'
        result = classify_notice(title=title, raw=raw, source_code='yuneng')
        self.assertEqual(result['classification_status'], 'matched')
        self.assertEqual(result['industry_code'], 'coal')
        self.assertEqual(result['classification_evidence']['relevance_tier'], 'core')
        self.assertIn('智能化建设', result['digital_tags'])
        self.assertTrue(any('智能调速' in item for item in result['classification_evidence']['snippets']))
        for body in ('具体内容详见附件。', '采购需求：不采购智能调速系统。',
                     '供应商登录智能调速系统上传投标文件。',
                     '<p>投标人资格要求</p><p>须提供智能调速系统建设服务业绩证明。</p>'):
            with self.subTest(body=body):
                negative = classify_notice(title=title, raw=body)
                self.assertNotEqual(negative['classification_status'], 'matched')
                self.assertEqual(negative['classification_evidence']['relevance_tier'], '')

    def test_public_docx_enriches_fields_and_has_reproducible_provenance(self):
        url = 'https://public.example/requirements.docx?download=1&v=2'
        body = ('采购需求：建设医院信息系统。\n预算金额（万元）：123.5\n'
                '响应文件递交截止时间：2026年10月12日09时30分\n开标时间：2026-10-12 09:30')
        original = self.normalized(f'<a href="{url.replace("&", "&amp;")}">采购需求.docx</a>')
        client = SimpleNamespace(policy=SimpleNamespace(allowed_origins={'https://public.example'}, max_bytes=2_000_000),
                                 fetch=Mock(return_value=SimpleNamespace(body=docx_bytes(body))))
        result = enrich_notice(original, raw='', attachment_client=client)
        self.assertEqual(original.value_of('bid_deadline'), '2026-10-12T09:30:00+08:00')
        self.assertEqual(original.fields['budget'].extra['amount_yuan'], '1235000')
        self.assertIn('医院信息系统', original.value_of('procurement_scope'))
        self.assertEqual(result.evidence[0]['status'], 'extracted')
        self.assertEqual(len(result.evidence[0]['sha256']), 64)
        self.assertEqual(original.fields['budget'].extra['source_url'], url)
        client.fetch.assert_called_once_with(url)
        target = apply_extraction(self.normalized(), result.to_dict())
        self.assertEqual(target.value_of('bid_deadline'), original.value_of('bid_deadline'))

    def test_date_only_unknown_clears_legacy_midnight_and_file_deadline_is_not_bid_deadline(self):
        normalized = self.normalized('<p>投标截止时间：2026-10-12</p>')
        self.assertEqual(normalized.status_of('bid_deadline'), 'UNKNOWN')
        normalized.fields['bid_deadline'] = FieldResult('bid_deadline', '2026-10-12T00:00:00+08:00', status='OK')
        enrich_notice(normalized, raw='投标截止时间：2026-10-12')
        self.assertIsNone(normalized.value_of('bid_deadline'))
        self.assertEqual(normalized.fields['bid_deadline'].extra['verification_status'], '待核实')
        self.assertNotIn('bid_deadline', extract_public_fields('文件获取截止时间：2026-10-12 09:30'))
        self.assertIsNone(self.normalized('文件获取截止时间：2026-10-12 09:30').value_of('bid_deadline'))

    def test_conflicting_deadlines_and_package_budgets_remain_unverified(self):
        fields = extract_public_fields('投标截止时间：2026-10-12 09:30\n投标截止时间：2026-10-13 09:30\n'
                                       '预算金额：100万元\n预算金额：200万元')
        for name in ('bid_deadline', 'budget'):
            self.assertEqual(fields[name].status, 'UNKNOWN')
            self.assertTrue(fields[name].extra['conflict'])
        normalized = self.normalized('投标截止时间：2026-10-12 09:30')
        attachment = {'fields': {'bid_deadline': extract_public_fields(
            '投标截止时间：2026-10-13 09:30')['bid_deadline'].to_dict()}}
        apply_extraction(normalized, attachment)
        self.assertEqual(normalized.status_of('bid_deadline'), 'UNKNOWN')

    def test_scope_and_amount_do_not_invent_missing_values(self):
        result = extract_public_fields('采购需求：详见附件\n预算金额：按实结算\n投标截止时间：详见原件')
        self.assertEqual(result, {})
        fields = extract_public_fields('<table><tr><td>预算金额（万元）</td><td>123.50</td></tr></table>')
        self.assertEqual(fields['budget'].extra['amount_yuan'], '1235000')

    def test_domain_type_and_count_limits_prevent_download(self):
        client = SimpleNamespace(policy=SimpleNamespace(allowed_origins={'https://public.example'}, max_bytes=1000), fetch=Mock())
        normalized = self.normalized()
        normalized.attachments = [{'url': 'https://evil.example/a.pdf'}, {'url': 'file:///a.pdf'},
                                  {'url': 'https://public.example/a.exe'}, {'url': 'https://public.example/b.pdf'}]
        result = enrich_notice(normalized, attachment_client=client)
        client.fetch.assert_not_called()
        self.assertEqual([item['code'] for item in result.evidence],
                         ['not_allowed', 'not_allowed', 'unsupported_type', 'count_limit'])

    def test_real_pdf_text_extraction_encryption_and_page_limit(self):
        self.assertIn('Public procurement scope', extract_attachment_text(pdf_bytes(), kind='pdf'))
        for content, code in ((pdf_bytes(encrypted=True), 'encrypted'), (pdf_bytes(pages=51), 'page_limit'),
                              (b'<html>login required</html>', 'parse_failed')):
            with self.subTest(code=code), self.assertRaisesRegex(ExtractionError, code):
                extract_attachment_text(content, kind='pdf')

    def test_archive_expansion_and_process_timeout_are_bounded(self):
        with self.assertRaisesRegex(ExtractionError, 'size_limit'):
            extract_attachment_text(b'x' * (MAX_ATTACHMENT_BYTES + 1), kind='pdf')
        bomb = docx_bytes('x' * (9 * 1024 * 1024))
        with self.assertRaisesRegex(ExtractionError, 'size_limit'):
            extract_attachment_text(bomb, kind='docx')
        import subprocess
        with patch('portal.tender_extraction.subprocess.run', side_effect=subprocess.TimeoutExpired('parser', 15)):
            with self.assertRaisesRegex(ExtractionError, 'parse_timeout'):
                extract_attachment_text(b'%PDF-', kind='pdf')

    def test_docx_actual_xml_read_is_bounded_even_if_metadata_understates_size(self):
        from portal.tender_extraction import _parse_document

        body = docx_bytes('public procurement ' * 100)
        claimed = SimpleNamespace(file_size=1, flag_bits=0, filename='word/document.xml')
        with patch('portal.tender_extraction.MAX_EXPANDED_BYTES', 64), \
                patch('portal.tender_extraction.zipfile.ZipFile.infolist', return_value=[claimed]):
            with self.assertRaisesRegex(ExtractionError, 'size_limit'):
                _parse_document(body, 'docx')

    def test_apply_extraction_cannot_override_verified_publication_or_identity(self):
        normalized = self.normalized('发布时间：2026-09-29\n项目名称：测试')
        apply_extraction(normalized, {'fields': {'publish_at': {'value': '2000-01-01'},
                                               'project_name': {'value': 'overwrite'}}})
        self.assertEqual(normalized.value_of('publish_at'), '2026-09-29')
        self.assertEqual(normalized.value_of('project_name'), '测试')

    def test_untrusted_huge_numeric_runs_never_overflow_amount_parser(self):
        for raw in ('9' * 150 + '万元', '999999999999999亿元', '9' * 80 + '.12元'):
            self.assertIsNone(parse_amount(raw))
            self.assertNotIn('budget', extract_public_fields('预算金额：' + raw))
        self.assertEqual(parse_amount('2.5亿元')['amount_yuan'], '250000000')

    def test_yuneng_json_envelope_is_decoded_for_fields_classification_and_attachments(self):
        content = ('<p>采购需求：建设煤矿智能体系统。</p><p>预算金额：123万元</p>'
                   '<p>投标截止时间：2026-10-12 09:30</p><a href="/public/a.pdf">采购需求.pdf</a>')
        raw = json.dumps({'success': True, 'code': 'success', 'content': {'content': content}}).encode()
        normalized = normalize_notice(raw, source_code='yuneng', original_url='https://public.example/a')
        self.assertEqual(normalized.value_of('bid_deadline'), '2026-10-12T09:30:00+08:00')
        self.assertEqual(normalized.attachments[0]['url'], 'https://public.example/public/a.pdf')
        enriched = enrich_notice(normalized, raw=raw)
        self.assertEqual(enriched.normalized.fields['budget'].extra['amount_yuan'], '1230000')
        result = classify_notice(title='煤矿智能体系统采购公告', raw=raw, source_code='yuneng')
        self.assertEqual(result['classification_evidence']['relevance_tier'], 'core')
        self.assertEqual(notice_content(b'{broken', source_code='yuneng'), '')
        self.assertEqual(notice_content(json.dumps({'success': False, 'content': {'content': content}}), source_code='yuneng'), '')

    def test_two_real_yuneng_notices_have_evidenced_deadlines_and_acquisition_ranges(self):
        cases = (
            ('52241', '2026-10-13T09:30:00+08:00', '2026-09-21T18:00:00+08:00', '2026-09-29T18:00:00+08:00'),
            ('51643', '2026-09-29T08:30:00+08:00', '2026-09-15T17:00:00+08:00', '2026-09-21T18:00:00+08:00'),
        )
        for notice_id, deadline, start, end in cases:
            with self.subTest(notice=notice_id):
                raw = (Path(__file__).parent / 'fixtures' / 'yuneng_20260929' / f'detail-{notice_id}.json').read_bytes()
                normalized = normalize_notice(raw, source_code='yuneng', original_url='https://dzsw.sxylny.com/')
                result = enrich_notice(normalized, raw=raw).normalized
                self.assertEqual(result.value_of('bid_deadline'), deadline)
                self.assertEqual(result.fields['signup_time'].extra['start_at'], start)
                self.assertEqual(result.fields['signup_time'].extra['end_at'], end)
                self.assertIn('获取时间', result.fields['signup_time'].raw)
                self.assertEqual(result.status_of('budget'), 'UNKNOWN')
                self.assertIn('改造', result.value_of('procurement_scope'))
                self.assertNotIn('资格要求', result.value_of('procurement_scope'))
                if notice_id == '51643':
                    self.assertEqual(result.value_of('bid_open_at'), deadline)
                    self.assertEqual(result.value_of('project_code'), 'YSMD-2026-01020246')

    def test_acquisition_context_and_date_labels_do_not_borrow_unrelated_dates(self):
        for content in (
            '获取时间\n2026年9月15日17时00分',
            '资格要求：自2023年1月1日至投标截止时间有类似业绩\n2026-10-01 12:30',
            '5.招标文件的获取\n6.投标文件的递交\n获取时间\n2026-10-01 12:30',
        ):
            result = extract_public_fields(content)
            self.assertNotIn('signup_time', result)
            self.assertNotIn('bid_deadline', result)
        result = extract_public_fields('报名时间：2026-10-12 09:00至2026-10-11 18:00')
        self.assertEqual(result['signup_time'].status, 'UNKNOWN')

    def test_project_code_removes_unmatched_wrappers_but_retains_balanced_code_parts(self):
        self.assertEqual(self.normalized('（项目编号：YSMD-2026-01020246）').value_of('project_code'), 'YSMD-2026-01020246')
        self.assertEqual(self.normalized('项目编号：YSMD(2026)-010').value_of('project_code'), 'YSMD(2026)-010')

    def test_actual_scope_is_preserved_before_an_attachment_reference(self):
        fields = extract_public_fields('2.5 本项目 采购 内容和 范围：建设智能调速系统，具体要求详见招标文件。\n3.投标人资格要求')
        self.assertEqual(fields['procurement_scope'].value, '2.5本项目采购内容和范围：建设智能调速系统')
