import io
from datetime import timedelta
from unittest.mock import patch
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from portal.hr_recruitment_models import RecruitmentRequest, JDVersion, RecruitmentMessage
from portal.hr_recruitment_service import extract_requirements
from portal.hr_screening_models import ResumeScreeningBatch, ResumeArtifact
from portal.hr_matching import requirements_for
from .base import PortalTestCase, json_body


class HrPrdIntakeTests(PortalTestCase):
    def setUp(self):
        self.hr = self.create_user('prd-intake-hr', 'hr')
        self.login(self.client, self.hr)
        self.root = '/api/hr/recruitment/'

    def post(self, suffix, **body):
        return self.client.post(self.root + suffix, json_body(**body), content_type='application/json')

    def intake(self, text='招一个数据工程师，本科及以上，月薪8000-12000元，双休，五险一金，年龄35岁以下，熟悉SQL'):
        response = self.post('requests/intake/', text=text)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_plain_chinese_keeps_facts_pending_and_age_note(self):
        result = self.intake()
        row, jd = result['request'], result['jd']
        self.assertEqual(row['position_name'], '数据工程师')
        self.assertEqual(row['education_requirement'], '本科及以上')
        self.assertIn('8000-12000', row['salary'])
        self.assertIn('双休', row['benefits'])
        self.assertIn('五险一金', row['social_insurance'])
        self.assertIn('35岁', row['notes'])
        self.assertIsNone(row['headcount'])
        self.assertTrue(jd['missing_items'])
        self.assertIn(row['original_text'], jd['body'])
        self.assertIn('通用 JD 草稿', jd['body'])
        self.assertIn('本地规则提取与整理（未调用 AI）', jd['body'])
        self.assertIn('招聘人数：待确认', jd['body'])
        self.assertEqual(jd['source_label'], '本地文本提取')
        self.assertEqual(jd['state'], 'draft')
        self.assertIsNone(row['official_jd_id'])
        matrix = requirements_for(jd['requirements'])
        self.assertTrue(any('SQL' in item['text'] for item in matrix))
        self.assertFalse(any('35岁' in item['text'] for item in matrix))
        self.assertEqual(list(RecruitmentMessage.objects.values_list('role', flat=True)), ['user', 'assistant'])

    def test_arbitrary_unclassified_facts_are_not_lost_or_guessed(self):
        text = '团队每周组织读书会；远程工作安排尚在讨论；薪资待协商'
        result = self.intake(text)
        self.assertIn(text, result['jd']['body'])
        self.assertEqual(result['request']['education_requirement'], '')
        self.assertIn('education_requirement', [x['field'] for x in result['jd']['missing_items']])

    def test_txt_upload_edit_confirm_uses_version_requirements(self):
        text = '岗位：数据工程师\n学历：本科\n技能：SQL\n薪资：9000元\n福利：双休\n社保：五险一金'
        response = self.client.post(self.root + 'requests/upload-jd/', {
            'file': SimpleUploadedFile('JD.txt', text.encode('utf-8'))})
        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        row, jd = result['request'], result['jd']
        self.assertEqual(row['intake_source'], 'upload')
        prefix = f"requests/{row['id']}/"
        blocked = self.client.post(self.root + 'batches/', json_body(jd_version_id=jd['id']),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY='draft-blocked')
        self.assertEqual(blocked.status_code, 409, blocked.content)
        body = '岗位：数据工程师\n学历：硕士\n技能：Python\n薪资：9000元\n福利：双休\n社保：五险一金'
        edit = self.post(prefix + 'jd-versions/', expected_version=1, base_jd_id=jd['id'], body=body,
                         requirements={'education_requirement': '硕士', 'skill_requirements': ['Python']})
        self.assertEqual(edit.status_code, 201, edit.content)
        revised = edit.json()
        self.assertEqual(revised['requirements']['skill_requirements'], ['Python'])
        self.assertEqual(revised['requirements']['education_requirement'], '硕士')
        self.assertEqual(self.post(prefix + f"jd-versions/{jd['id']}/confirm/", expected_version=1).status_code, 409)
        confirmed = self.post(prefix + f"jd-versions/{revised['id']}/confirm/", expected_version=1)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        # The immutable version carries the matching input, not the old request.
        self.assertEqual(RecruitmentRequest.objects.get(pk=row['id']).skill_requirements, ['SQL'])
        self.assertEqual(JDVersion.objects.get(pk=revised['id']).requirements['skill_requirements'], ['Python'])
        history = self.client.get(self.root + prefix + 'history/').json()
        self.assertEqual(len(history['jd_versions']), 2)
        self.assertEqual(len(history['messages']), 6)
        self.assertEqual(history['jd_versions'][-1]['state'], 'confirmed')
        batch = self.client.post(self.root + 'batches/', json_body(jd_version_id=revised['id']),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY='edited-jd')
        self.assertEqual(batch.status_code, 201, batch.content)
        saved = ResumeScreeningBatch.objects.get(pk=batch.json()['id'])
        self.assertEqual(saved.requirements['skill_requirements'], ['Python'])
        self.assertEqual(saved.requirements['education_requirement'], '硕士')

    def test_manual_requirements_must_match_body_and_age_cannot_screen(self):
        result = self.intake()
        prefix = f"requests/{result['request']['id']}/jd-versions/"
        for body, requirements in [('技能：SQL', {'skill_requirements': ['Python']}),
                                   ('任职要求：35岁以下', {'required_requirements': '35岁以下'})]:
            with self.subTest(body=body):
                response = self.post(prefix, expected_version=1, base_jd_id=result['jd']['id'],
                                     body=body, requirements=requirements)
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(JDVersion.objects.count(), 1)
        self.assertEqual(RecruitmentMessage.objects.count(), 2)

    def test_docx_uses_safe_extractor(self):
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>岗位：工程师</w:t></w:r></w:p></w:body></w:document>')
        response = self.client.post(self.root + 'requests/upload-jd/', {
            'file': SimpleUploadedFile('JD.docx', stream.getvalue())})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['request']['position_name'], '工程师')

    @patch('portal.hr_resume_extract._pdf', return_value='岗位：PDF工程师\n学历：本科')
    def test_pdf_routes_through_existing_extractor(self, pdf):
        response = self.client.post(self.root + 'requests/upload-jd/', {
            'file': SimpleUploadedFile('JD.pdf', b'%PDF-1.7 mocked parser fixture')})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['jd']['requirements']['education_requirement'], '本科')
        pdf.assert_called_once()

    def test_upload_rejects_paths_unsupported_large_and_malicious_files(self):
        response = self.post('requests/upload-jd/', file='C:/private/jd.txt')
        self.assertEqual(response.status_code, 400)
        response = self.post('requests/upload-jd/', path='C:/private/jd.txt')
        self.assertEqual(response.status_code, 400)
        for name, content in [('jd.exe', b'hello'), ('jd.txt', b'a' * (2 * 1024 * 1024 + 1)),
                              ('jd.pdf', b'invalid'), ('jd.docx', b'not a zip'), ('jd.txt', b'')]:
            with self.subTest(name=name, size=len(content)):
                response = self.client.post(self.root + 'requests/upload-jd/', {
                    'file': SimpleUploadedFile(name, content)})
                self.assertEqual(response.status_code, 400, response.content)
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('word/document.xml', '<document/>')
            archive.writestr('word/_rels/document.xml.rels', '<Relationships><Relationship TargetMode="External" Target="http://example.com"/></Relationships>')
        response = self.client.post(self.root + 'requests/upload-jd/', {
            'file': SimpleUploadedFile('jd.docx', stream.getvalue())})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(RecruitmentRequest.objects.count(), 0)

    def test_owner_isolation_and_history_results(self):
        result = self.intake()
        row = RecruitmentRequest.objects.get(pk=result['request']['id'])
        jd = row.current_jd
        batch = ResumeScreeningBatch.objects.create(created_by=self.hr, jd_version=jd,
            input_version=1, idempotency_key='history')
        ResumeArtifact.objects.create(batch=batch, uploaded_by=self.hr, file_id='hidden-file',
            filename='candidate.txt', sha256='a' * 64, size=1, processing_status='completed',
            match={'matrix': [], 'score': {'total': 1, 'unknown_count': 0, 'hard_gap': []}})
        url = self.root + f'requests/{row.pk}/history/'
        history = self.client.get(url)
        self.assertEqual(history.status_code, 200, history.content)
        self.assertEqual(history.json()['batches'][0]['results'][0]['score'], 1)
        self.assertNotIn('hidden-file', history.content.decode())
        self.login(self.client, self.create_user('other-prd-hr', 'hr'))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(self.root + 'requests/').json(), [])

    def test_expired_request_hidden_and_edit_does_not_renew(self):
        result = self.intake()
        row = RecruitmentRequest.objects.get(pk=result['request']['id'])
        created = row.created_at
        detail = self.root + f'requests/{row.pk}/'
        response = self.client.patch(detail, json_body(expected_version=1, salary='12000元'), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.created_at, created)
        RecruitmentRequest.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=15))
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(detail + 'history/').status_code, 404)
        self.assertEqual(self.client.get(self.root + 'requests/').json(), [])
        self.assertEqual(self.client.get(self.root + 'confirmed-jds/').json(), [])
        self.assertEqual(self.client.patch(detail, json_body(expected_version=2, salary='14000元'), content_type='application/json').status_code, 404)

    def test_history_filters_old_batches_even_with_active_parent(self):
        result = self.intake()
        row = RecruitmentRequest.objects.get(pk=result['request']['id'])
        batch = ResumeScreeningBatch.objects.create(created_by=self.hr, jd_version=row.current_jd,
            input_version=1, idempotency_key='expired-history')
        ResumeScreeningBatch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - timedelta(days=15))
        response = self.client.get(self.root + f'requests/{row.pk}/history/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['batches'], [])

    @patch('portal.model_gateway.generate_for_use')
    def test_gateway_retains_unparsed_original_and_known_facts(self, model):
        result = self.intake('招聘工程师，双休，五险一金，每周一次读书会')
        model.return_value = {'content': '工程师 JD 草稿'}
        response = self.post(f"requests/{result['request']['id']}/generate-jd/", expected_version=1)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn('每周一次读书会', response.json()['body'])
        self.assertIn('五险一金', response.json()['body'])
        self.assertIn('原始招聘说明', response.json()['body'])
        self.assertIn('每周一次读书会', model.call_args.args[2][-1]['content'])

    def test_invalid_intake_is_atomic(self):
        for text in ['', None, 'a' * 40001, 'bad\x00text']:
            response = self.post('requests/intake/', text=text)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(RecruitmentRequest.objects.count(), 0)
        self.assertEqual(RecruitmentMessage.objects.count(), 0)


    def test_edit_does_not_reintroduce_original_appendix_requirements(self):
        result = self.intake('岗位：工程师\n技能：SQL\n学历：本科')
        jd, row = result['jd'], result['request']
        # Change only the executable JD section; retain provenance verbatim.
        body = jd['body'].replace('技能要求：SQL', '技能要求：Python').replace('学历要求：本科', '学历要求：硕士')
        response = self.post(f"requests/{row['id']}/jd-versions/", expected_version=1,
            base_jd_id=jd['id'], body=body)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['requirements']['skill_requirements'], ['Python'])
        self.assertEqual(response.json()['requirements']['education_requirement'], '硕士')
        self.assertIn('技能：SQL', response.json()['body'])

    def test_english_age_and_repeated_labels_preserve_safe_requirements(self):
        extracted = extract_requirements('岗位：工程师\n任职要求：熟悉Python，掌握SQL\n任职要求：了解测试\n技能：Python、SQL，Git\nage: under 35\nyears old: 30')
        self.assertIn('熟悉Python', extracted['required_requirements'])
        self.assertIn('了解测试', extracted['required_requirements'])
        self.assertIn('掌握SQL', extracted['skill_requirements'])
        self.assertIn('Python', extracted['skill_requirements'])
        self.assertIn('Git', extracted['skill_requirements'])
        self.assertIn('age: under 35', extracted['notes'])
        self.assertFalse(any('age:' in item['text'] for item in requirements_for(extracted)))

    def test_structured_conversation_uses_chinese_labels(self):
        response = self.post('requests/', position_name='工程师', salary='9000元')
        self.assertEqual(response.status_code, 201)
        message = RecruitmentMessage.objects.filter(role='user').get()
        self.assertIn('岗位名称：工程师', message.content)
        self.assertIn('薪资：9000元', message.content)
        self.assertNotIn('position_name', message.content)
