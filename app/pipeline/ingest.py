"""문서 수집. URL·PDF·붙여넣은 텍스트·이미지를 받아 문장 단위 청크로 만든다.

국내 상세페이지는 대부분 이미지라서, 본문 글자가 적고 이미지가 많으면
이미지를 내려받아 비전 LLM으로 글자를 읽는다.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .. import config
from . import llm

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@dataclass
class Ingested:
    title: str
    text: str
    chunks: list[str]
    notes: list[str] = field(default_factory=list)


# ---------- 청크 ----------
_SENT_SPLIT = re.compile(r"(?<=[\.\!\?。다요음함됨임니다])\s+|\n+")


def chunk_text(text: str, max_len: int = 220) -> list[str]:
    """문장 단위로 자르되, 너무 짧은 조각은 앞 조각에 붙이고 너무 긴 조각은 나눈다."""
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    pieces = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    chunks: list[str] = []
    for p in pieces:
        if len(p) > max_len:
            for i in range(0, len(p), max_len):
                chunks.append(p[i:i + max_len])
            continue
        if chunks and len(chunks[-1]) < 18:
            chunks[-1] = chunks[-1] + " " + p
        else:
            chunks.append(p)
    return [c for c in chunks if len(c) >= 6]


# ---------- 입력 종류별 ----------
def from_text(title: str, text: str) -> Ingested:
    return Ingested(title=title or "붙여넣은 텍스트", text=text, chunks=chunk_text(text))


def from_pdf(title: str, data: bytes) -> Ingested:
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    notes = []
    for i, page in enumerate(doc, 1):
        t = page.get_text()
        if len(t.strip()) < 30 and llm.available():
            # 스캔 PDF: 페이지를 이미지로 렌더해 OCR
            pix = page.get_pixmap(dpi=110)
            t = llm.ocr_image(pix.tobytes("png"))
            notes.append(f"{i}쪽: 글자가 적어 이미지 OCR 사용")
        pages.append(f"[{i}쪽] {t}")
    text = "\n".join(pages)
    return Ingested(title=title or "설명서 PDF", text=text, chunks=chunk_text(text), notes=notes)


def from_image(title: str, data: bytes) -> Ingested:
    text = llm.ocr_image(data) if llm.available() else ""
    notes = [] if text else ["LLM 키가 없어 이미지 OCR을 건너뛰었습니다."]
    return Ingested(title=title or "이미지", text=text, chunks=chunk_text(text), notes=notes)


def _fetch(url: str) -> httpx.Response:
    with httpx.Client(follow_redirects=True, timeout=25, headers={"User-Agent": UA}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r


def from_url(url: str, ocr_images: bool = True) -> Ingested:
    r = _fetch(url)
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return from_pdf(url.rsplit("/", 1)[-1], r.content)
    if ctype.startswith("image/"):
        return from_image(url.rsplit("/", 1)[-1], r.content)

    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "svg"]):
        tag.decompose()
    title = (soup.title.string.strip() if soup.title and soup.title.string else url)
    body_text = soup.get_text("\n")
    body_text = "\n".join(l.strip() for l in body_text.splitlines() if l.strip())
    notes: list[str] = []

    # 메타·사양표 보강
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        body_text = meta_desc["content"] + "\n" + body_text

    # 이미지가 본문인 상세페이지 처리
    imgs = []
    for im in soup.find_all("img"):
        src = im.get("data-src") or im.get("data-original") or im.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        full = urljoin(url, src)
        w = im.get("width")
        try:
            if w and int(str(w).rstrip("px")) < 300:
                continue
        except ValueError:
            pass
        if re.search(r"(icon|logo|btn|button|badge|sprite|blank|\.svg)", full, re.I):
            continue
        imgs.append(full)
    korean_chars = len(re.findall(r"[가-힣]", body_text))
    if ocr_images and imgs and llm.available() and (korean_chars < 1500 or len(imgs) >= 5):
        ocr_texts = []
        for src in imgs[: config.MAX_OCR_IMAGES]:
            try:
                ir = _fetch(src)
                if len(ir.content) < 15_000:   # 작은 이미지는 장식일 가능성이 큼
                    continue
                t = llm.ocr_image(ir.content)
                if t.strip():
                    ocr_texts.append(f"[이미지 {src.rsplit('/', 1)[-1][:40]}] {t}")
            except Exception as e:  # 이미지 하나 실패해도 계속
                notes.append(f"이미지 OCR 실패: {src[:60]} ({e})")
        if ocr_texts:
            notes.append(f"상세페이지 이미지 {len(ocr_texts)}장을 OCR로 읽었습니다.")
            body_text += "\n" + "\n".join(ocr_texts)
    elif imgs and not llm.available():
        notes.append(f"이미지 {len(imgs)}장이 있으나 LLM 키가 없어 OCR을 건너뛰었습니다.")

    return Ingested(title=title, text=body_text, chunks=chunk_text(body_text), notes=notes)


# ---------- 이미지 상세페이지 ----------
def _tiles(data: bytes, max_w: int = 1000) -> list[bytes]:
    """세로로 긴 상세 이미지를 읽기 좋은 크기로 자른다. 글자가 뭉개지지 않게 폭을 맞추고 높이를 나눈다."""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)))
    tile_h = int(im.width * 1.6)
    out = []
    for top in range(0, im.height, tile_h):
        part = im.crop((0, top, im.width, min(im.height, top + tile_h)))
        if part.height < 80:
            continue
        buf = io.BytesIO()
        part.save(buf, "JPEG", quality=82)
        out.append(buf.getvalue())
    return out


def from_image_urls(title: str, urls: list[str], per_call: int = 6) -> Ingested:
    """이미지 URL 목록 → 조각 → 여러 장씩 묶어 OCR. 호출 수 = 조각 수 / per_call."""
    notes: list[str] = []
    tiles: list[bytes] = []
    for u in urls[:30]:
        try:
            r = _fetch(u)
            if len(r.content) < 15_000:
                continue
            tiles.extend(_tiles(r.content))
        except Exception as e:
            notes.append(f"이미지 받기 실패: {u[-50:]} ({e})")
    if not tiles:
        return Ingested(title=title, text="", chunks=[], notes=notes or ["읽을 이미지가 없습니다."])
    if not llm.available():
        return Ingested(title=title, text="", chunks=[], notes=["LLM 키가 없어 이미지 글자를 읽지 못했습니다."])
    texts: list[str] = []
    calls = 0
    for i in range(0, len(tiles), per_call):
        try:
            pages = llm.ocr_images(tiles[i:i + per_call])
            calls += 1
            texts.extend(p for p in pages if p.strip())
        except Exception as e:
            notes.append(f"이미지 글자 읽기 실패({i // per_call + 1}번째 묶음): {e}")
    text = "\n".join(texts)
    notes.append(f"상세 이미지 {len(urls)}장을 {len(tiles)}조각으로 나눠 {calls}번 호출로 읽었습니다.")
    return Ingested(title=title, text=text, chunks=chunk_text(text), notes=notes)
