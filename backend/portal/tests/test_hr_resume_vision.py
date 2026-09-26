import base64
import hashlib
from unittest.mock import patch

from django.test import SimpleTestCase

from portal.hr_resume_vision import recognize_pages
from portal.product_storage import StorageError


class ResumeVisionTests(SimpleTestCase):
    def pages(self):
        image = b'\xff\xd8\xffsynthetic'
        return [
            {'page': 1, 'text': '第一页原文', 'needs_vision': False},
            {'page': 2, 'text': '页脚残留', 'needs_vision': True,
             'image': base64.b64encode(image).decode(), 'image_sha256': hashlib.sha256(image).hexdigest()},
        ]

    @patch('portal.hr_resume_vision.generate_for_use')
    def test_mixed_pdf_replaces_incomplete_page_and_preserves_provenance(self, call):
        call.return_value = {'content': '{"text":"第二页完整文字","readable":true}'}
        result = recognize_pages(object(), self.pages(), 'a' * 64)
        self.assertEqual([p['text'] for p in result['pages']], ['第一页原文', '第二页完整文字'])
        self.assertEqual(result['pages'][1]['method'], 'vision')
        self.assertEqual(result['source_sha256'], 'a' * 64)
        self.assertEqual(call.call_args.args[1], 'hr_resume_extract')
        self.assertNotIn('image', result['pages'][1])

    @patch('portal.hr_resume_vision.generate_for_use')
    def test_unreadable_invalid_and_empty_outputs_are_not_success(self, call):
        for output in ('not-json', '{"text":"猜测","readable":false}',
                       '{"text":"","readable":true}'):
            call.return_value = {'content': output}
            with self.subTest(output=output), self.assertRaises(StorageError):
                recognize_pages(object(), self.pages(), 'a' * 64)

    @patch('portal.hr_resume_vision.generate_for_use')
    def test_tampered_page_is_rejected_before_outbound(self, call):
        pages = self.pages()
        pages[1]['image_sha256'] = '0' * 64
        with self.assertRaises(StorageError):
            recognize_pages(object(), pages, 'a' * 64)
        call.assert_not_called()

    @patch('portal.hr_resume_vision.generate_for_use')
    def test_text_only_pages_do_not_call_model(self, call):
        result = recognize_pages(object(), self.pages()[:1], 'a' * 64)
        self.assertEqual(result['pages'][0]['method'], 'text_layer')
        call.assert_not_called()
