"""이미지·PDF에서 글자 읽기.

소비자가 '바로 확인'에 상세페이지 캡처(JPG·PNG·PDF)를 올리면 여기서 글자를 꺼낸다.
두 가지 길이 있다.
  1) 비전 LLM(Gemini) — 정확하지만 크레딧을 쓴다. 이미지 6장을 한 번에 보내 호출 수를 줄인다.
  2) tesseract(kor+eng) — 무료·로컬. 영문 'AI'를 '시로'로 읽는 식의 오인식이 있어 보정 규칙을 둔다.
allow_llm=False 이면 2)만 쓴다. 어느 쪽이든 결과는 '글자'일 뿐이고 판정은 하지 않는다.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from . import llm

MAX_SIDE = 1600          # LLM·OCR에 보내기 전 긴 변 기준 축소
LLM_BATCH = 6
_FIX = [  # tesseract kor 모델의 흔한 오인식
    (re.compile(r"(?<![가-힣])시로(?=\s*\d|\s*[가-힣])"), "AI로"),
    (re.compile(r"\bAl\b"), "AI"),
    (re.compile(r"\bA1\b"), "AI"),
    (re.compile(r"\bAI\s*로\b"), "AI로"),
]


@dataclass
class OcrResult:
    texts: list[str] = field(default_factory=list)   # 입력 순서대로
    engine: str = ""                                  # llm | tesseract | none
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(t for t in self.texts if t.strip())


def _shrink(data: bytes) -> bytes:
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > MAX_SIDE:
            r = MAX_SIDE / max(w, h)
            im = im.resize((int(w * r), int(h * r)))
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return data


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


_BUNDLED = os.path.join(os.path.dirname(__file__), "tessdata")
_TESS_OPTS: list[str] | None = None


def _tess_opts() -> list[str]:
    """시스템에 한국어 데이터가 있으면 그대로, 없으면 저장소에 넣어 둔 kor+eng(fast)를 쓴다."""
    global _TESS_OPTS
    if _TESS_OPTS is not None:
        return _TESS_OPTS
    opts = ["-l", "eng"]
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout
        langs = {l.strip() for l in out.splitlines()[1:]}
        if "kor" in langs:
            opts = ["-l", "kor+eng"]
        elif os.path.exists(os.path.join(_BUNDLED, "kor.traineddata")):
            opts = ["--tessdata-dir", _BUNDLED, "-l", "kor+eng"]
    except Exception:
        pass
    _TESS_OPTS = opts
    return opts


def _tesseract(data: bytes, timeout_seconds: float = 120) -> str:
    """로컬 OCR. 글자가 작은 캡처가 많아 2배로 키우고 회색조로 바꾼 뒤 읽는다."""
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("L")
    w, h = im.size
    if max(w, h) < 2400:
        im = im.resize((w * 2, h * 2), Image.LANCZOS)
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.png")
        im.save(src)
        r = subprocess.run(["tesseract", src, "-", *_tess_opts(), "--psm", "6"],
                           capture_output=True, text=True, timeout=timeout_seconds)
        text = r.stdout
    for pat, rep in _FIX:
        text = pat.sub(rep, text)
    lines = [l.strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def read_images(imgs: list[bytes], allow_llm: bool = True, local_timeout_seconds: float = 120) -> OcrResult:
    imgs = [_shrink(b) for b in imgs if b]
    res = OcrResult()
    if not imgs:
        return res
    if allow_llm and llm.available():
        try:
            for i in range(0, len(imgs), LLM_BATCH):
                res.texts.extend(llm.ocr_images(imgs[i:i + LLM_BATCH]))
            res.engine = "llm"
            if len(res.texts) < len(imgs):
                res.texts.extend([""] * (len(imgs) - len(res.texts)))
            return res
        except Exception as e:  # 키 한도·일시 오류면 로컬로 넘어간다
            res.notes.append("비전 모델에 연결하지 못해 로컬 글자 인식으로 대신 읽었어요.")
    if tesseract_available():
        try:
            res.texts = [_tesseract(b, timeout_seconds=local_timeout_seconds) for b in imgs]
            res.engine = "tesseract"
            if "kor" not in " ".join(_tess_opts()):
                res.notes.append("한국어 글자 인식 데이터가 없어 영문만 읽었어요.")
            else:
                res.notes.append("자동 글자 인식이라 숫자나 영문에 오탈자가 있을 수 있어요.")
            return res
        except Exception as e:
            res.notes.append(f"로컬 글자 인식 실패({str(e)[:40]})")
    if os.name == 'nt':
        try:
            import asyncio
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.globalization import Language
            from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
            from winrt.windows.storage.streams import DataWriter
            from PIL import Image
            async def recognize():
                engine = OcrEngine.try_create_from_language(Language('ko'))
                if engine is None: raise RuntimeError('Korean OCR unavailable')
                texts = []
                for data in imgs:
                    with Image.open(io.BytesIO(data)) as source:
                        im = source.convert('RGBA')
                        writer = DataWriter(); bitmap = None
                        try:
                            writer.write_bytes(im.tobytes('raw', 'BGRA'))
                            bitmap = SoftwareBitmap.create_copy_with_alpha_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, im.width, im.height, BitmapAlphaMode.IGNORE)
                            result = await engine.recognize_async(bitmap)
                            texts.append('\n'.join(line.text for line in result.lines))
                        finally:
                            if bitmap is not None: bitmap.close()
                            writer.close(); im.close()
                return texts
            res.texts = asyncio.run(recognize()); res.engine = 'windows_ocr'
            res.notes.append('Windows 한국어 OCR로 이미지의 글자를 읽었어요. 오인식이 있을 수 있어요.')
            return res
        except (ImportError, RuntimeError, OSError):
            res.notes.append('Windows OCR을 사용할 수 없어요.')
    res.engine = "none"
    res.notes.append("이미지에서 글자를 읽을 수 있는 장치가 없어요.")
    return res


def read_pdf(data: bytes, allow_llm: bool = True, max_pages: int = 12, image_reader=None) -> OcrResult:
    """글자가 든 PDF는 그대로 꺼내고, 스캔·이미지 PDF는 페이지를 그림으로 바꿔 읽는다."""
    import pymupdf
    res = OcrResult(engine="pdf")
    doc = pymupdf.open(stream=data, filetype="pdf")
    scans: list[bytes] = []
    idx: list[int] = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            res.notes.append(f"{max_pages}쪽까지만 읽었어요.")
            break
        t = page.get_text().strip()
        if len(t) >= 30:
            res.texts.append(t)
        else:
            res.texts.append("")
            # Bound allocation before rendering unusually large PDF pages.
            # The image OCR path already resizes inputs to MAX_SIDE.
            longest = max(page.rect.width, page.rect.height)
            if longest <= 0:
                raise ValueError("PDF page has invalid dimensions")
            scale = min(130 / 72, MAX_SIDE / longest)
            scans.append(page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes("png"))
            idx.append(i)
    if scans:
        sub = (image_reader or read_images)(scans, allow_llm=allow_llm)
        for j, i in enumerate(idx):
            if j < len(sub.texts):
                res.texts[i] = sub.texts[j]
        res.notes.extend(sub.notes)
        res.engine = sub.engine or res.engine
    return res
