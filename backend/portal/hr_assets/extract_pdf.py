"""Executed only inside the existing isolated document runtime."""
import json
import sys
from pathlib import Path

import fitz


def main():
    source, output = map(Path, sys.argv[1:])
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
            pages.append(text)
    output.write_text(json.dumps({'text': '\n'.join(pages)}, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
