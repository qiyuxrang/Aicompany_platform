import base64
import copy
import unittest
from unittest.mock import patch

from model_gateway.tests import test_app

PNG = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nsynthetic').decode()


class VisionTests(unittest.TestCase):
    setUp = test_app.GatewayAppTests.setUp
    @patch('model_gateway.app.chat_completion', return_value={'content': '识别文本', 'prompt_tokens': 1, 'completion_tokens': 2})
    def test_vision_route_and_remote_url_boundary(self, call):
        body = copy.deepcopy(self.payload)
        body['messages'] = [{'role': 'user', 'content': [
            {'type': 'text', 'text': '请识别'}, {'type': 'image_url', 'image_url': {'url': PNG}},
        ]}]
        url = '/v1/generate-vision'
        self.assertEqual(self.client.post(url, json=body).status_code, 401)
        result = self.client.post(url, json=body, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(call.call_args.args[2], body['messages'])
        body['messages'][0]['content'][1]['image_url']['url'] = 'http://127.0.0.1/private'
        call.reset_mock()
        denied = self.client.post(url, json=body, headers=self.headers)
        self.assertIn(denied.status_code, (400, 422))
        call.assert_not_called()

    @patch('model_gateway.app.chat_completion')
    def test_oversized_image_and_system_image_are_rejected(self, call):
        body = copy.deepcopy(self.payload)
        body['messages'] = [{'role': 'system', 'content': [{'type': 'image_url', 'image_url': {'url': PNG}}]}]
        self.assertIn(self.client.post('/v1/generate-vision', json=body, headers=self.headers).status_code, (400, 422))
        call.assert_not_called()

    @patch('model_gateway.app.chat_completion')
    def test_oversized_and_malformed_inline_images_never_call_provider(self, call):
        for url in ('data:image/png;base64,not-base64!',
                    'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'x' * 1048576).decode()):
            body = copy.deepcopy(self.payload)
            body['messages'] = [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': url}}]}]
            result = self.client.post('/v1/generate-vision', json=body, headers=self.headers)
            self.assertEqual(result.status_code, 400)
        call.assert_not_called()

    def test_transport_serializes_image_blocks_without_fetching_them(self):
        from model_gateway.transport import _request
        body = copy.deepcopy(self.payload)
        body['messages'] = [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': PNG}}]}]
        raw, timeout = _request(body['provider'], body['model'], body['messages'], None)
        import json
        self.assertEqual(json.loads(raw)['messages'], body['messages'])
        self.assertEqual(timeout, 10)


if __name__ == '__main__':
    unittest.main()
