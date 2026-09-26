"""Executed only inside the existing isolated document runtime."""
import json
import base64
import hashlib
import sys
from pathlib import Path

import fitz


def main():
    source, output = map(Path, sys.argv[1:3])
    inspect = len(sys.argv) == 4 and sys.argv[3] == '--pages'
    with fitz.open(source) as document:
        if document.needs_pass or document.page_count > 100:
            raise ValueError('unsupported PDF')
        pages = []
        total = 0
        for page in document:
            text = page.get_text()
            total += len(text)
            if total > 100000:
                raise ValueError('text limit')
            if not inspect:
                pages.append(text)
                continue
            rect = page.rect
            if rect.is_empty or rect.is_infinite:
                raise ValueError('invalid page bounds')
            largest_image = max((fitz.Rect(info['bbox']).get_area() / rect.get_area()
                                 for info in page.get_image_info()), default=0)
            # ponytail: heuristic coverage flag, use layout recognition if mixed-page recall proves inadequate.
            needs_vision = not text.strip() or largest_image >= 0.25
            record = {'page': page.number + 1, 'text': text, 'needs_vision': needs_vision}
            if needs_vision:
                scale = min(2.0, 2000 / max(rect.width, rect.height))
                image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False,
                                       colorspace=fitz.csRGB).tobytes('jpeg', jpg_quality=85)
                if len(image) > 1024 * 1024:
                    raise ValueError('page image limit')
                record.update(image=base64.b64encode(image).decode(), image_sha256=hashlib.sha256(image).hexdigest())
            pages.append(record)
    result = {'pages': pages} if inspect else {'text': '\n'.join(pages)}
    encoded = json.dumps(result, ensure_ascii=False).encode('utf-8')
    if len(encoded) > (16 * 1024 * 1024 if inspect else 1024 * 1024):
        raise ValueError('output limit')
    output.write_bytes(encoded)


if __name__ == '__main__':
    main()
