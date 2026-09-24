from pathlib import Path
import json
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parent
pdf = pdfium.PdfDocument(root / '企业平台总体规划_SDD_V0.2.pdf')
render = root / 'render'
render.mkdir(exist_ok=True)
pages = []
for index in range(len(pdf)):
    page = pdf[index]
    image = page.render(scale=1.5).to_pil().convert('RGB')
    image.save(render / f'page-{index + 1}.png')
    text = page.get_textpage().get_text_range()
    pages.append({'page': index + 1, 'characters': len(text), 'text': text})
for offset in range(0, len(pdf), 6):
    sheet = Image.new('RGB', (1500, 1470), '#d9d9d9')
    draw = ImageDraw.Draw(sheet)
    for position, index in enumerate(range(offset, min(offset + 6, len(pdf)))):
        image = Image.open(render / f'page-{index + 1}.png')
        image.thumbnail((485, 690))
        left = (position % 3) * 500 + 7
        top = (position // 3) * 735 + 30
        sheet.paste(image, (left, top))
        draw.text((left, top - 23), f'PAGE {index + 1}', fill='black')
    sheet.save(render / f'contact-{offset // 6 + 1}.png')
(root / 'page-text.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'pages': len(pdf), 'page_characters': [item['characters'] for item in pages]}))
