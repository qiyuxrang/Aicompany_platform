"""Native PDF text first; local OCR only where an image has no text layer."""
import io
import math
import warnings
from PIL import Image, ImageOps
from .core import LIMITS, ParseError

Image.MAX_IMAGE_PIXELS = LIMITS["pixels"]
_ENGINE = None


def read_image(content, suffix=None):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(content))
            formats = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP', '.bmp': 'BMP', '.tif': 'TIFF', '.tiff': 'TIFF'}
            if suffix and formats.get(suffix) != image.format:
                image.close()
                raise ParseError('format_mismatch', '图片内容与扩展名不一致，请重新导出图片。')
            if image.width * image.height > LIMITS["pixels"]:
                raise ParseError("image_limit", "图片像素超过 2400 万，请缩小图片后上传。")
            image.load()
            result = ImageOps.exif_transpose(image).convert("RGB")
            frames = getattr(image, "n_frames", 1)
            image.close()
            return result, frames
    except ParseError: raise
    except Exception as error: raise ParseError("invalid_image", "图片损坏、像素过大或不是受支持的位图格式。") from error


def ocr(image, result, location, native=""):
    global _ENGINE
    if result.ocr_count >= LIMITS["ocr_pages"]:
        result.warn("ocr_limit", "本文件已识别 12 个扫描页或图片，剩余扫描内容未识别；请拆分补充。", location, "partial")
        return
    result.ocr_count += 1
    if not result.meta.get("ocr_enabled", True):
        result.warn("ocr_unavailable", "本地 OCR 未启用，扫描内容尚未识别。", location, "partial")
        return
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        if _ENGINE is None:
            _ENGINE = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1, det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False)
        image.thumbnail((2200, 2200))
        output, _ = _ENGINE(np.asarray(image))
        normalized = "".join(native.split())
        recognized = 0
        for box, text, confidence in output or []:
            if normalized and "".join(text.split()) in normalized: continue
            score = float(confidence)
            if not math.isfinite(score): continue
            result.add(text, {**location, "bbox": [[round(float(x), 2), round(float(y), 2)] for x, y in box],
                             "image_width": image.width, "image_height": image.height}, "ocr", confidence=round(score, 4))
            recognized += 1
            if score < .85: result.warn("low_ocr_confidence", "部分文字识别置信度较低，请对照原图核对。", location)
        if recognized: result.warn("ocr_review_required", "此处由本地 OCR 识别，文字、数字和单位需人工核对；未自动推断表格或图形含义。", location)
        elif not native: result.warn("image_no_text", "未识别到可用文字，请检查图像清晰度或补充说明。", location, "partial")
    except (ImportError, RuntimeError, OSError) as error:
        result.warn("ocr_unavailable", "本地 OCR 引擎不可用，已保留原文件，请修复解析环境后重试。", location, "partial")


def image(content, result, location=None, embedded=False, suffix=None):
    location = location or {"image": 1}
    img, frames = read_image(content, suffix)
    try:
        if frames > 1: result.warn("image_frames_skipped", "多帧图片仅识别第一帧，请将其余帧导出为单独图片。", location, "partial")
        ocr(img, result, location)
    finally: img.close()


def pdf(content, result):
    import pypdfium2 as pdfium
    if not content.lstrip().startswith(b"%PDF-"):
        raise ParseError("format_mismatch", "文件内容不是 PDF。")
    try: document = pdfium.PdfDocument(content)
    except pdfium.PdfiumError as error:
        code = "encrypted_document" if error.err_code == 4 else "invalid_document"
        raise ParseError(code, "PDF 已加密或损坏，请解密后重新上传。") from error
    try:
        if len(document) > LIMITS["pages"]: raise ParseError("page_limit", "PDF 超过 100 页，请拆分后上传。")
        result.meta["pages"] = len(document)
        if document.count_attachments(): result.warn("pdf_attachments_ignored", "PDF 内嵌附件未解析，请单独上传附件。", severity="partial")
        for number in range(len(document)):
            page = document[number]
            textpage = None
            try:
                textpage = page.get_textpage()
                if textpage.count_chars() > LIMITS["characters"]: raise ParseError("text_limit", "单页文字量超过解析上限，请拆分 PDF。")
                text = textpage.get_text_bounded().replace("\x00", "").strip()
                location = {"page": number + 1}
                if text: result.add(text, location, "page")
                width, height = page.get_size()
                area = max(1, width * height)
                large_image = False
                for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
                    left, bottom, right, top = obj.get_bounds()
                    if abs((right-left) * (top-bottom)) / area > .15: large_image = True
                needs_ocr = (not text or text.count("\ufffd") > len(text) * .1) or large_image
                if needs_ocr:
                    if result.ocr_count >= LIMITS['ocr_pages'] or not result.meta.get('ocr_enabled', True):
                        code = 'ocr_limit' if result.ocr_count >= LIMITS['ocr_pages'] else 'ocr_unavailable'
                        result.warn(code, '本页扫描内容未识别，请拆分文件或启用本地 OCR 后重试。', location, 'partial')
                        continue
                    if width <= 0 or height <= 0 or not all(math.isfinite(v) for v in (width, height)):
                        raise ParseError("invalid_page", "PDF 页尺寸无效。")
                    scale = min(2.5, 2200 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        img = bitmap.to_pil().convert("RGB")
                        try: ocr(img, result, location, native=text)
                        finally: img.close()
                    finally: bitmap.close()
            finally:
                if textpage is not None: textpage.close()
                page.close()
    finally: document.close()


def preview(content, suffix, page_number, output):
    if suffix == ".pdf":
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(content) as document:
            if not 1 <= page_number <= min(len(document), LIMITS["pages"]): raise ParseError("invalid_page", "预览页码无效。")
            page = document[page_number - 1]
            try:
                w, h = page.get_size()
                if not all(math.isfinite(v) and v > 0 for v in (w, h)): raise ParseError("invalid_page", "PDF 页尺寸无效。")
                bitmap = page.render(scale=min(2.0, 1600 / max(w, h)))
                try:
                    img = bitmap.to_pil()
                    try: img.save(output, "PNG")
                    finally: img.close()
                finally: bitmap.close()
            finally: page.close()
    else:
        img, _ = read_image(content, suffix)
        try:
            img.thumbnail((1600, 1600))
            img.save(output, "PNG")
        finally: img.close()
