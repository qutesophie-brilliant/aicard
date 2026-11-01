"""User-initiated product DOM transfer; never collect browser credentials."""
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlsplit
from ..network_safety import public_addresses
from . import product_page, ingest


def read(html, url):
    public_addresses(url)
    if not isinstance(html, str) or len(html.encode('utf-8')) > 1500000:
        raise ValueError('상품 설명이 너무 큽니다. 필요한 설명 구간을 캡처해 올려 주세요.')
    soup = BeautifulSoup(html, 'html.parser')
    for n in list(soup.select('script,style,noscript,iframe,object,embed,form,input,textarea,select,button,nav,header,footer,aside,svg,[contenteditable]')):
        if n.parent: n.decompose()
    image_sources = {id(n): product_page.image_source(n) for n in soup.select('img')}
    for n in soup.find_all(True):
        # Resolve the public detail image before dropping lazy-load attributes.
        # Do not forward any unrelated data attributes or browser credentials.
        raw_src = image_sources.get(id(n), '')
        src = urljoin(url, raw_src) if raw_src else ''
        n.attrs = {k:v for k,v in n.attrs.items() if k in ('class','id','itemprop','src')}
        n.attrs.pop('src', None)
        if n.name == 'img':
            parsed = urlsplit(src)
            if parsed.scheme in ('http', 'https') and not parsed.username and not parsed.password:
                n['src'] = src
            else:
                n.decompose()
    # Only known product sections can become evidence; no whole-page fallback.
    product_page.product_body(str(soup), url)
    result = ingest.from_product_html(str(soup), url, ocr_images=True)
    result.source_capture['acquisition'] = [{'stage':'user_browser_capture','status':'read'}]
    result.notes.insert(0, '사용자가 브라우저에서 보낸 상품 설명과 이미지 주소를 분석했습니다. 판매페이지 전체를 서버에서 직접 확인한 결과는 아닙니다.')
    return result
