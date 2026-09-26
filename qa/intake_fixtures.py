"""Generate local synthetic files for real parser/browser acceptance. No user files."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from portal.tests.intake_fixtures import docx, xlsx, pdf
from PIL import Image, ImageDraw, ImageFont


def generate():
    target = ROOT / '.runtime' / 'intake-fixtures'
    target.mkdir(parents=True, exist_ok=True)
    (target / '项目设计说明.docx').write_bytes(docx())
    (target / '设备清单.xlsx').write_bytes(xlsx(formula=True, merged=True))
    (target / '项目背景.pdf').write_bytes(pdf())
    fontpath = Path('C:/Windows/Fonts/msyh.ttc')
    if not fontpath.is_file():
        raise RuntimeError('Chinese OCR acceptance requires an installed CJK font; no font is bundled or downloaded.')
    font = ImageFont.truetype(str(fontpath), 40)
    image = Image.new('RGB', (1300, 400), 'white')
    draw = ImageDraw.Draw(image)
    for index, text in enumerate(['供配电项目现场记录', '设备名称：配电柜', '数量：2 台', '仅用于软件合成验收']):
        draw.text((50, 30 + index * 80), text, font=font, fill='black')
    image.save(target / '现场记录.png')
    image.save(target / '扫描记录.pdf', 'PDF', resolution=120)
    Image.new('RGB', (300, 200), 'white').save(target / '空白图.png')
    print(target)


if __name__ == '__main__': generate()
