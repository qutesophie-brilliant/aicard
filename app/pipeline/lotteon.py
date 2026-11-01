"""Read the public description document used by LotteON's product iframe.

No cookies, login state or challenge handling. Only the observed LO product-ID
format is supported; unknown IDs/layouts return to the normal acquisition path.
"""
import re
from urllib.parse import parse_qs, quote, urlsplit
from bs4 import BeautifulSoup
from .product_page import ProductBodyUnavailable


HOST = 'llis-goodsdetail.lotteon.com'


def detail_url(url):
    p = urlsplit(url)
    if (p.scheme not in ('http', 'https') or p.hostname not in ('www.lotteon.com', 'lotteon.com')
            or p.username or p.password or p.port not in (None, 80, 443)):
        return None
    match = re.fullmatch(r'/[pm]/product/(LO\d{10})/?', p.path)
    if not match:
        return None
    product_id = match[1]
    digits = product_id[2:]
    model = 'itemdetail/LO/' + '/'.join(digits[i:i+2] for i in range(0, 10, 2)) + '/DSCRP_' + product_id
    return 'https://' + HOST + '/goodsdetail?model=' + quote(model, safe='')


def read_product(url, *, ocr_images):
    from ..network_safety import fetch_public, public_addresses
    from .ingest import from_product_html, UA
    public_addresses(url)
    source = detail_url(url)
    if not source:
        raise ProductBodyUnavailable('이 주소에 연결된 상품 설명을 확인하지 못했습니다.')
    response = fetch_public(source, timeout=20, max_bytes=4 * 1024 * 1024,
                            max_redirects=2, headers={'User-Agent': UA},
                            allowed_hosts={HOST}, https_only=True)
    # An unrelated redirect or generic page must not become product evidence.
    final = urlsplit(str(response.url))
    if (final.hostname != HOST or final.path != '/goodsdetail'
            or parse_qs(final.query).get('model') != parse_qs(urlsplit(source).query).get('model')
            or 'html' not in response.headers.get('content-type', '').lower()
            or not BeautifulSoup(response.text, 'html.parser').select_one('#llis-root')):
        raise ProductBodyUnavailable('같은 제품의 공개 상세 설명을 확인하지 못했습니다.')
    result = from_product_html(response.text, str(response.url), ocr_images=ocr_images)
    result.title = '롯데ON 상품 설명 · ' + urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]
    result.source_capture.update(source_page_url=url,
        documents=[{'url':str(response.url), 'status':'read'}],
        acquisition=[{'stage':'public_product_description', 'status':'read'}])
    result.notes.insert(0, '판매페이지에 연결된 공개 상품 상세 설명을 확인했습니다. 가격·재고·구매 옵션은 분석 대상에 포함하지 않습니다.')
    return result
