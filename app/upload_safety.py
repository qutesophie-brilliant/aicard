"""Bounded checks for consumer uploads before any OCR or external AI call.

This is document-content validation and reconstruction, not a general-purpose
antivirus. Originals are neither executed nor retained by this module.
"""
from __future__ import annotations

import io
import re
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_PIXELS = 32_000_000
MAX_IMAGE_SIDE = 1600
MAX_PDF_PAGES = 200
MAX_PDF_OBJECTS = 20_000
IMAGE_FORMATS = {
    '.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP',
    '.gif': 'GIF', '.bmp': 'BMP',
}
BLOCKED_PDF_NAMES = {
    'JavaScript', 'JS', 'Launch', 'EmbeddedFiles', 'EmbeddedFile',
    'FileAttachment', 'XFA', 'RichMedia', 'RichMediaContent', 'RichMediaSettings',
    'SubmitForm', 'ImportData', 'GoToR', 'GoToE', 'Rendition', 'Movie', 'Sound',
}
_PDF_DELIMITERS = b'\x00\t\n\x0c\r ()<>[]{}/%'


class UploadSafetyError(ValueError):
    """Safe user-facing reason to reject an upload before reading its content."""


@dataclass(frozen=True)
class CheckedUpload:
    data: bytes
    content_type: str


def check_image_batch(payloads):
    """Check each file header; uploads are decoded and read in bounded groups."""
    for filename, content_type, data in payloads:
        if Path(filename).suffix.lower() not in IMAGE_FORMATS:
            continue
        try:
            with Image.open(io.BytesIO(data)) as image:
                pixels = image.width * image.height
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise UploadSafetyError('이미지를 읽지 못했습니다. JPG·PNG로 다시 저장해 올려 주세요.') from exc
        if pixels > MAX_PIXELS:
            raise UploadSafetyError('이미지 한 장의 해상도가 너무 큽니다. 필요한 제품 설명 부분을 나누어 올려 주세요.')


def _pdf_names(text: str):
    """Read PDF name tokens, excluding strings, comments, and hexadecimal data.

    MuPDF expands compressed object streams in xref_object(). Name escapes such
    as /Java#53cript must also be decoded; a raw substring search misses these.
    """
    data = text.encode('latin-1', errors='replace')
    i = 0
    while i < len(data):
        ch = data[i]
        if ch == 37:  # comment
            end = data.find(b'\n', i)
            i = len(data) if end < 0 else end + 1
        elif ch == 40:  # literal string, including escaped or nested parentheses
            depth = 1
            i += 1
            while i < len(data) and depth:
                if data[i] == 92:
                    i += 2
                    continue
                depth += (data[i] == 40) - (data[i] == 41)
                i += 1
        elif ch == 60 and data[i:i + 2] != b'<<':  # hexadecimal string
            end = data.find(b'>', i + 1)
            i = len(data) if end < 0 else end + 1
        elif data[i:i + 2] == b'<<':
            i += 2
        elif ch == 47:  # name
            start = i = i + 1
            while i < len(data) and data[i] not in _PDF_DELIMITERS:
                i += 1
            name = re.sub(rb'#([0-9a-fA-F]{2})', lambda m: bytes([int(m[1], 16)]), data[start:i])
            yield name.decode('latin-1')
        else:
            i += 1


def _check_pdf(data: bytes) -> CheckedUpload:
    import pymupdf

    if not data.startswith(b'%PDF-'):
        raise UploadSafetyError('PDF 파일의 형식이 올바르지 않습니다. 원본에서 PDF로 다시 저장하거나 캡처 이미지로 올려 주세요.')
    try:
        with pymupdf.open(stream=data, filetype='pdf') as document:
            if not document.is_pdf or document.needs_pass:
                raise UploadSafetyError('암호가 설정된 PDF는 확인할 수 없습니다. 암호를 해제한 파일이나 캡처 이미지를 올려 주세요.')
            if not 1 <= document.page_count <= MAX_PDF_PAGES or document.xref_length() > MAX_PDF_OBJECTS:
                raise UploadSafetyError('PDF의 구성이 너무 복잡합니다. 필요한 제품 설명 부분만 PDF나 캡처 이미지로 올려 주세요.')
            if document.embfile_count():
                raise UploadSafetyError('첨부파일이 포함된 PDF는 처리하지 않습니다. 제품 설명 페이지만 새 PDF나 캡처 이미지로 올려 주세요.')
            for xref in range(1, document.xref_length()):
                names = set(_pdf_names(document.xref_object(xref, compressed=False)))
                if names & BLOCKED_PDF_NAMES:
                    raise UploadSafetyError('스크립트·실행 동작 또는 첨부 콘텐츠가 포함된 PDF는 처리하지 않습니다. 제품 설명을 캡처하거나 해당 요소를 제외한 PDF로 올려 주세요.')
            document.set_metadata({})
            document.del_xml_metadata()
            # Rebuild the document, removing trailing data and unused objects;
            # keep visible content and ordinary source links for extraction.
            clean = document.tobytes(garbage=4, deflate=True)
            return CheckedUpload(clean, 'application/pdf')
    except UploadSafetyError:
        raise
    except Exception as exc:
        raise UploadSafetyError('PDF의 안전한 처리를 확인하지 못했습니다. 제품 설명을 새 PDF나 캡처 이미지로 올려 주세요.') from exc


def _check_image(data: bytes, expected_format: str) -> CheckedUpload:
    try:
        with Image.open(io.BytesIO(data)) as probe:
            try:
                if probe.format != expected_format:
                    raise UploadSafetyError('파일 확장자와 실제 이미지 형식이 다릅니다. JPG·PNG로 다시 저장한 뒤 올려 주세요.')
                if probe.width * probe.height > MAX_PIXELS:
                    raise UploadSafetyError('이미지 해상도가 너무 큽니다. 필요한 제품 설명 부분을 나누어 캡처한 뒤 올려 주세요.')
                probe.verify()
            finally:
                probe.close()
        with Image.open(io.BytesIO(data)) as source, ExitStack() as images:
            # Image's context manager closes its file, but not its decoded pixels.
            images.callback(source.close)
            # Keep long screenshots legible: OCR splits them into bounded tiles.
            # Ordinary photographs still use the existing size limit.
            if max(source.size) / min(source.size) < 2:
                source.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            oriented = source
            if source.getexif().get(274, 1) in (2, 3, 4, 5, 6, 7, 8):
                oriented = ImageOps.exif_transpose(source)
                images.callback(oriented.close)
            if 'A' in oriented.getbands() or 'transparency' in oriented.info:
                rgba = oriented
                if oriented.mode != 'RGBA':
                    rgba = oriented.convert('RGBA')
                    images.callback(rgba.close)
                pixels = Image.new('RGB', rgba.size, 'white')
                images.callback(pixels.close)
                alpha = rgba.getchannel('A')
                images.callback(alpha.close)
                pixels.paste(rgba, mask=alpha)
            else:
                pixels = oriented
                if oriented.mode != 'RGB':
                    pixels = oriented.convert('RGB')
                    images.callback(pixels.close)
            pixels.info.clear()
            with io.BytesIO() as stream:
                pixels.save(stream, format='PNG')
                return CheckedUpload(stream.getvalue(), 'image/png')
    except UploadSafetyError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise UploadSafetyError('이미지의 안전한 처리를 확인하지 못했습니다. JPG·PNG로 다시 저장한 뒤 올려 주세요.') from exc


def check_upload(filename: str, content_type: str, data: bytes, *, max_bytes: int) -> CheckedUpload:
    """Validate and reconstruct one bounded upload, or fail closed."""
    if not data or len(data) > max_bytes:
        raise UploadSafetyError('파일이 비어 있거나 허용 용량을 초과했습니다. 제품 설명 부분을 다시 저장해 올려 주세요.')
    suffix = Path(filename).suffix.lower()
    if suffix == '.pdf':
        if content_type.startswith('image/'):
            raise UploadSafetyError('파일 이름과 형식이 다릅니다. 원본에서 PDF로 다시 저장해 올려 주세요.')
        return _check_pdf(data)
    if suffix not in IMAGE_FORMATS or content_type == 'application/pdf':
        raise UploadSafetyError('지원하는 이미지(JPG·PNG 등)나 PDF 파일을 올려 주세요.')
    return _check_image(data, IMAGE_FORMATS[suffix])
