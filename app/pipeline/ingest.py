"""문서 수집. URL·PDF·붙여넣은 텍스트·이미지를 받아 문장 단위 청크로 만든다.

국내 상세페이지는 대부분 이미지라서, 본문 글자가 적고 이미지가 많으면
이미지를 내려받아 비전 LLM으로 글자를 읽는다.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote

import httpx
from bs4 import BeautifulSoup

from .. import config
from . import llm, product_page

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@dataclass
class Ingested:
    title: str
    text: str
    chunks: list[str]
    notes: list[str] = field(default_factory=list)
    source_capture: dict = field(default_factory=dict)


# ---------- 청크 ----------
_SENT_SPLIT = re.compile(r"(?<=[\.\!\?。다요음함됨임니다])\s+|\n+")


_LINE_END = re.compile(r"[\.\!\?。다요음함됨임니다]\s*[\)\]”\"']?$|[:：]$")


def merge_ocr_lines(text: str) -> str:
    """OCR 결과는 문장 중간에서 줄이 바뀐다. 문장이 끝나지 않은 줄은 다음 줄과 잇는다.
    짧은 줄(표 항목·제목)과 각주 표시로 시작하는 줄은 그대로 둔다."""
    out: list[str] = []
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            if out and not out[-1].endswith("\n"):
                out.append("")
            continue
        if (out and out[-1] and len(out[-1]) >= 12 and not _LINE_END.search(out[-1])
                and not re.match(r"^[※\*†①-⑳\-•·]|^\d{1,2}[\)\.]\s", line) and len(line) >= 4):
            out[-1] = out[-1] + " " + line
        else:
            out.append(line)
    return "\n".join(l for l in out)


def normalize_spacing(text: str) -> str:
    """OCR 결과에 빠진 띄어쓰기를 보탠다: 문장 끝 뒤("청소합니다.AI"), 한글 뒤 로마자 대문자 단어("인식은RGB"),
    그리고 문장 중간에 이어 붙은 각주 표시("…입니다.* RGB 카메라는")는 줄을 나눠 각주가 따로 서게 한다."""
    # 문장 끝 뒤 띄어쓰기: 앞이 한글·영문 소문자·닫는 괄호일 때만(숫자 사이 소수점 "PM1.0", "99.5"와 주소 "lge.co.kr"는 건드리지 않는다)
    text = re.sub(r"(?<=[가-힣\)])([\.\!\?。])(?=[가-힣A-Z\(\[])", r"\1 ", text)
    text = re.sub(r"([가-힣\.\,\)])(?=[A-Z][A-Za-z]{1,})", r"\1 ", text)
    text = re.sub(r"(?<=[\.\!\?。다요음함됨임])\s*(?=[※\*†]\s?[가-힣A-Za-z])", "\n", text)
    # OCR이 소수점 뒤를 띄운 수치("PM 1. 0 센서", "0. 3㎛")를 붙인다. 단위나 센서가 바로 따라올 때만(번호 목록 "1. 2" 보호)
    text = re.sub(r"(?<=\d)\.\s(?=\d{1,2}\s?(?:㎛|μm|um|mm|cm|㎜|㎝|%|kg|L\b|W\b|센서|이하|이상))", ".", text)
    return text


def chunk_text(text: str, max_len: int = 220, ocr: bool = False) -> list[str]:
    if ocr:
        text = merge_ocr_lines(text)
    """문장 단위로 자르되, 너무 짧은 조각은 앞 조각에 붙이고 너무 긴 조각은 나눈다."""
    text = normalize_spacing(text)
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
    return Ingested(title=title or "이미지", text=text, chunks=chunk_text(text, ocr=True), notes=notes)


def _fetch(url: str) -> httpx.Response:
    from ..network_safety import fetch_public
    return fetch_public(url, headers={"User-Agent": UA})


def _from_consumer_file(url: str, data: bytes, *, pdf: bool, read_ocr: bool) -> Ingested:
    """Direct file links use the same bounded OCR as consumer uploads."""
    import hashlib
    import io
    from PIL import Image
    from ..upload_safety import check_upload, IMAGE_FORMATS
    from . import ocr, product_ocr, quick_diagrams

    if pdf:
        filename, content_type = 'source.pdf', 'application/pdf'
    else:
        try:
            with Image.open(io.BytesIO(data)) as probe:
                suffix = next((ext for ext, fmt in IMAGE_FORMATS.items() if fmt == probe.format), None)
            if suffix is None:
                raise ValueError('지원하지 않는 이미지 형식입니다. JPG·PNG로 저장해 올려 주세요.')
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise product_page.ProductBodyUnavailable('이미지 파일을 읽지 못했습니다. JPG·PNG 파일이나 제품 설명이 있는 주소를 확인해 주세요.') from exc
        filename, content_type = 'source' + suffix, 'image/' + ('jpeg' if suffix in ('.jpg', '.jpeg') else suffix[1:])
    try:
        checked = check_upload(filename, content_type, data, max_bytes=config.CHECK_MAX_FILE_MB * 1024 * 1024)
    except ValueError as exc:
        raise product_page.ProductBodyUnavailable(str(exc)) from exc
    readings = []

    def reader(images, allow_llm=True):
        if not read_ocr:
            return ocr.OcrResult(texts=[''] * len(images), notes=['이미지 속 글자는 읽지 않았습니다.'])
        rows = product_ocr.read_images(images, allow_llm=allow_llm,
                                       local_timeout_seconds=8, recover_unconfirmed=True)
        readings.extend(rows)
        engines = {row['engine'] for row in rows if row['text'].strip()}
        result = ocr.OcrResult(texts=[row['text'] for row in rows],
                              engine=next(iter(engines)) if len(engines) == 1 else 'mixed' if engines else 'none')
        result.notes.extend(product_ocr.provider_failure_notes(rows))
        unread = sum(not row['text'].strip() or row['status'] == 'partial' for row in rows)
        if unread:
            result.notes.append('일부 이미지 또는 구간의 글자 읽기를 확인하지 못했습니다. 확인된 내용만 분석하며 같은 원본으로 다시 시도할 수 있습니다.')
        return result

    try:
        result = (ocr.read_pdf(checked.data, allow_llm=config.CHECK_OCR_LLM, max_pages=12, image_reader=reader)
                  if pdf else reader([checked.data], allow_llm=config.CHECK_OCR_LLM))
    except ValueError as exc:
        raise product_page.ProductBodyUnavailable(str(exc)) from exc
    if not result.text.strip():
        message = ' '.join(result.notes + ['파일에서 읽을 수 있는 글자를 확인하지 못했습니다. 같은 원본으로 다시 시도하거나 설명 글을 붙여 넣어 주세요.'])
        raise product_page.ProductBodyUnavailable(message)
    capture = quick_diagrams.capture_uploads(result.texts, url)
    digest = hashlib.sha256(data).hexdigest()
    base_url = urlunsplit(urlsplit(url)._replace(fragment=''))
    for section in capture['sections']:
        section.update(snapshot_sha256=digest, ocr_engine=result.engine)
        if pdf:
            section['source_url'] = base_url + '#page=' + str(section['upload_unit'] + 1)
    if not pdf and readings:
        row = dict(readings[0], url=url, image_number=1, image_sha256=digest,
                   download_attempts=1, ocr_attempts=readings[0]['attempts'])
        capture['images'] = [row]
    capture.update(source_response_sha256=digest)
    capture['coverage'].update(image_ocr_unconfirmed=sum(not row['text'].strip() or row['status'] == 'partial' for row in readings),
                               direct_file=True)
    title = unquote(urlsplit(url).path.rsplit('/', 1)[-1]) or ('PDF 문서' if pdf else '제품 설명 이미지')
    notes = ['주소에서 내려받은 PDF를 페이지별로 확인했습니다.' if pdf else '주소에서 내려받은 원문 이미지를 확인했습니다.']
    notes.extend(result.notes)
    return Ingested(title=title, text=result.text, chunks=chunk_text(result.text),
                    notes=notes, source_capture=capture)


def from_url(url: str, ocr_images: bool = True, product_only: bool = False) -> Ingested:
    r = _fetch(url)
    ctype = r.headers.get("content-type", "")
    direct_pdf = 'pdf' in ctype or urlsplit(str(r.url)).path.lower().endswith('.pdf')
    if product_only and (direct_pdf or ctype.startswith('image/')):
        return _from_consumer_file(str(r.url), r.content, pdf=direct_pdf, read_ocr=ocr_images)
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return from_pdf(url.rsplit("/", 1)[-1], r.content)
    if ctype.startswith("image/"):
        return from_image(url.rsplit("/", 1)[-1], r.content)

    if product_only:
        from . import cjonstyle
        if cjonstyle.detail_api_url(str(r.url)):
            return cjonstyle.read_product(str(r.url), ocr_images=ocr_images)
        return from_product_html(r.text, str(r.url), ocr_images=ocr_images)
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else url)
    if product_only:
        soup = product_page.product_body(r.text, str(r.url))
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "svg"]):
        tag.decompose()
    body_text = product_page.product_text(soup) if product_only else soup.get_text("\n")
    body_text = "\n".join(l.strip() for l in body_text.splitlines() if l.strip())
    notes: list[str] = (["상품 설명 영역의 글자로 확인했어요. 메뉴·추천상품은 제외했으며, 이미지 속 글자는 읽지 않았어요."]
                        if product_only and not ocr_images else [])

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
            body_text += "\n" + merge_ocr_lines("\n".join(ocr_texts))
    elif imgs and not llm.available():
        notes.append(f"이미지 {len(imgs)}장이 있으나 LLM 키가 없어 OCR을 건너뛰었습니다.")

    capture = {}
    if product_only:
        from .source_capture import capture_product_html
        capture = capture_product_html(r.text, str(r.url))
        if capture['coverage']['unresolved_panel_targets']:
            notes.append('별도 화면의 세부정보 중 원문을 읽지 못한 항목이 있습니다. 조건이 없다는 뜻은 아닙니다.')
    return Ingested(title=title, text=body_text, chunks=chunk_text(body_text), notes=notes,
                    source_capture=capture)


def from_product_html(html: str, url: str, ocr_images: bool = False) -> Ingested:
    """Use an explicitly supplied rendered product snapshot, without hidden fetches."""
    from .source_capture import capture_product_html
    capture = capture_product_html(html, url)
    if ocr_images:
        from .product_images import capture_images
        capture_images(html, url, capture)
    engines = {i.get('engine') for i in capture.get('images', []) if i.get('text')}
    engine_note = ('AI 문자 인식' if 'llm' in engines else '서버 내 문자 인식')
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else url
    image_count = capture.get("coverage", {}).get("image_candidates", 0)
    read_count = sum(bool(i["text"]) for i in capture.get("images", []))
    coverage = capture['coverage']
    image_notes = []
    if ocr_images:
        from .product_ocr import provider_failure_notes
        attempted = coverage.get('image_attempted',0)
        image_notes.append(f"제품 설명 이미지 {image_count}장 중 {attempted}장을 확인 대상으로 선택했고, {read_count}장에서 글자를 읽었습니다.")
        if read_count: image_notes.append(f"글자 읽기: {engine_note}")
        image_notes.extend(provider_failure_notes(capture.get('images', [])))
        if coverage.get('image_skipped_limit'):
            image_notes.append(f"처리량 제한으로 {coverage['image_skipped_limit']}장은 이번 분석에서 읽기를 시도하지 않았습니다.")
        if coverage.get('image_download_failed'):
            image_notes.append(f"이미지 {coverage['image_download_failed']}장은 내려받지 못했습니다.")
        if coverage.get('image_ocr_unconfirmed'):
            image_notes.append(f"이미지 {coverage['image_ocr_unconfirmed']}장에 글자 읽기를 확인하지 못한 구간이 있습니다. 확인한 글자만 분석에 사용했으며, 미확인 구간에 글자가 없다는 뜻은 아닙니다.")
        if coverage.get('image_ocr_retried'):
            image_notes.append(f"이미지 {coverage['image_ocr_retried']}장은 재시도하거나 다른 방식으로 글자 읽기를 한 번 더 시도했습니다.")
        image_notes.append("읽지 못했거나 시도하지 않은 이미지의 내용은 결과에 포함되지 않습니다.")
    return Ingested(title=title, text=capture['text'], chunks=chunk_text(capture['text']),
                    source_capture=capture,
                    notes=['제품 설명과 포함된 세부정보를 확인했습니다.'] + (image_notes if ocr_images else ['이미지 속 글자는 읽지 않았습니다.']))


# ---------- 이미지 상세페이지 ----------
def _tiles(data: bytes, max_w: int = 1000, max_pixels: int = 12_000_000) -> list[bytes]:
    """세로로 긴 상세 이미지를 읽기 좋은 크기로 자른다. 글자가 뭉개지지 않게 폭을 맞추고 높이를 나눈다.
    무료 서버(512MB) 대비: 전체를 RGB로 바꾸거나 통째로 줄이지 않고, JPEG는 축소 디코딩(draft)으로 받고
    조각마다 잘라서 변환·저장한 뒤 바로 버린다."""
    import gc
    import io
    import math
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    px = im.width * im.height
    if px > max_pixels:
        f = 2 ** math.ceil(math.log2(math.sqrt(px / max_pixels)))
        if im.format == "JPEG":
            im.draft(im.mode if im.mode in ("RGB", "L") else "RGB", (im.width // f, im.height // f))
        elif px > max_pixels * 3:
            im.close()
            return []  # PNG 등은 축소 디코딩이 없어 너무 크면 건너뛴다
    im.load()
    scale = min(1.0, max_w / im.width)
    w = int(im.width * scale)
    tile_src_h = max(200, int(im.width * 1.6))
    out = []
    for top in range(0, im.height, tile_src_h):
        part = im.crop((0, top, im.width, min(im.height, top + tile_src_h)))
        if part.height * scale < 80:
            continue
        if part.mode != "RGB":
            part = part.convert("RGB")
        if scale < 1.0:
            part = part.resize((w, max(1, int(part.height * scale))))
        buf = io.BytesIO()
        part.save(buf, "JPEG", quality=80)
        out.append(buf.getvalue())
        del part
    im.close()
    del im
    gc.collect()
    return out


def from_image_urls(title: str, urls: list[str], per_call: int = 6) -> Ingested:
    """이미지 URL 목록 → 조각 → 여러 장씩 묶어 OCR. 호출 수 = 조각 수 / per_call."""
    notes: list[str] = []
    tiles: list[bytes] = []
    def _rss_mb() -> int:
        """현재 RSS(MB). ru_maxrss 는 최고치라 쓰지 않는다."""
        try:
            with open("/proc/self/statm") as f:
                return int(f.read().split()[1]) * 4096 // (1024 * 1024)
        except Exception:
            return 0
    import os as _os
    limit_mb = int(_os.getenv("OCR_MEM_LIMIT_MB", "1400"))
    for u in urls[:60]:
        if _rss_mb() > limit_mb:  # 서버 메모리 보호(현재 사용량 기준)
            notes.append("메모리가 부족해 남은 이미지는 읽지 않았습니다.")
            break
        try:
            r = _fetch(u)
            if len(r.content) < 15_000 or len(r.content) > 12_000_000:
                continue
            tiles.extend(_tiles(r.content))
            del r
            import gc
            gc.collect()
        except Exception as e:
            notes.append(f"이미지 받기 실패: {u[-50:]} ({e})")
        if len(tiles) >= 120:  # OCR 호출 20회 상한
            notes.append("상세 이미지가 많아 앞부분 120조각까지만 읽었습니다.")
            break
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
    return Ingested(title=title, text=text, chunks=chunk_text(text, ocr=True), notes=notes)


# ---------- 띄어쓰기 보정 ----------
# 판매페이지를 저장할 때 줄바꿈(<br>)이 사라져 "감지하고적외선", "사물과가구"처럼 단어가 붙은 문장이 생긴다.
# LLM에 띄어쓰기만 고치게 하고, 공백을 뺀 글자가 원문과 한 글자라도 다르면 버린다(내용은 절대 바뀌지 않는다).
SPACING_PROMPT = """아래 문장들은 웹페이지에서 줄바꿈이 사라져 단어가 붙어 버린 한국어 문장입니다.
띄어쓰기만 바로잡으세요. 글자·숫자·기호·순서는 하나도 바꾸거나 더하거나 빼지 마세요. 이미 맞게 띄어 쓴 곳은 그대로 둡니다.
출력(JSON 객체, 번호를 키로): {{"0": "고친 문장", "1": "고친 문장"}}

{body}
"""
# 붙은 단어 의심: 한글 6자 이상 연속(예: "사물과가구"는 5자라 연결어미·조사 규칙으로 잡는다)
_GLUE = re.compile(r"[가-힣]{6,}|[가-힣](과|와|고|며|하고|하며|하여|되어|해서|으로|에서|까지)(?=[가-힣]{2,})[가-힣]")


def _squash(t: str) -> str:
    return re.sub(r"\s+", "", t)


def fix_spacing(chunks: list[str], cache: dict[str, str] | None = None) -> list[str]:
    """붙은 단어가 의심되는 청크만 골라 LLM으로 띄어쓰기를 고친다. cache(원문→고친 문장)로 같은 문장은 다시 부르지 않는다."""
    cache = {} if cache is None else cache
    todo = [c for c in dict.fromkeys(chunks) if c not in cache and _GLUE.search(c)]
    if todo and llm.available():
        for i in range(0, len(todo), 40):
            part = todo[i:i + 40]
            body = "\n".join(f"[{k}] {t}" for k, t in enumerate(part))
            try:
                data = llm.ask_json(SPACING_PROMPT.format(body=body), max_tokens=6000)
            except llm.LLMError:
                continue
            if not isinstance(data, dict):
                continue
            for k, t in enumerate(part):
                new = data.get(str(k))
                if isinstance(new, str) and _squash(new) == _squash(t):
                    cache[t] = re.sub(r" {2,}", " ", new.strip())
                else:
                    cache[t] = t  # 글자가 바뀌었거나 빠졌으면 원문 유지
    return [cache.get(c, c) for c in chunks]
