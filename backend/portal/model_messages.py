"""Bounded inline images only; never fetch user-supplied image URLs."""
import base64
import binascii
import json

VISION_REQUEST_LIMIT = 6 * 1024 * 1024
MAX_IMAGE_BYTES = 1024 * 1024


def validate_messages(messages, *, vision=False):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise ValueError('invalid messages')
    images = 0
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                or message['role'] not in ('system', 'user', 'assistant')):
            raise ValueError('invalid message')
        content = message['content']
        if isinstance(content, str):
            if not content.strip() or len(content) > 16000:
                raise ValueError('invalid text')
            continue
        if not vision or message['role'] != 'user' or not isinstance(content, list) or not 1 <= len(content) <= 8:
            raise ValueError('invalid content')
        for block in content:
            if not isinstance(block, dict):
                raise ValueError('invalid block')
            if set(block) == {'type', 'text'} and block['type'] == 'text':
                if not isinstance(block['text'], str) or not block['text'].strip() or len(block['text']) > 16000:
                    raise ValueError('invalid text block')
            elif set(block) == {'type', 'image_url'} and block['type'] == 'image_url':
                validate_image(block['image_url'])
                images += 1
                if images > 4:
                    raise ValueError('too many images')
            else:
                raise ValueError('invalid block')
    limit = VISION_REQUEST_LIMIT if vision else 60000
    if len(json.dumps(messages, ensure_ascii=False).encode()) > limit:
        raise ValueError('message size limit')
    return images


def validate_image(image):
    if not isinstance(image, dict) or set(image) != {'url'} or not isinstance(image['url'], str):
        raise ValueError('invalid image')
    url = image['url']
    prefix, separator, encoded = url.partition(',')
    signatures = {'data:image/png;base64': b'\x89PNG\r\n\x1a\n', 'data:image/jpeg;base64': b'\xff\xd8\xff'}
    if prefix not in signatures or not separator or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError('invalid inline image')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('invalid base64') from None
    if len(raw) > MAX_IMAGE_BYTES or not raw.startswith(signatures[prefix]):
        raise ValueError('invalid image bytes')
