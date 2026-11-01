"""Known public description document routes published by retailer product pages.

These documents are unauthenticated HTML, not private APIs. Keep product IDs and
final routes exact; never treat a login page or another product as evidence.
"""
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .product_page import ProductBodyUnavailable

HOST = 'desc.skstoa.com'


def detail_url(url):
    p = urlsplit(url)
    if (p.scheme not in ('http', 'https') or p.hostname not in ('www.skstoa.com', 'skstoa.com')
            or p.username or p.password or p.port not in (None, 80, 443)):
        return None
    match = re.fullmatch(r'/display/goods/(\d{6,12})/?', p.path)
    return f'https://{HOST}/display/detail/goods/pc/describe/{match[1]}' if match else None


def read_product(url, *, ocr_images):
    from ..network_safety import public_addresses, fetch_public
    from .ingest import from_product_html
    from .page_recovery import access_problem
    public_addresses(url)
    source = detail_url(url)
    if not source:
        raise ProductBodyUnavailable('상품 상세 설명 주소를 확인하지 못했습니다.')
    response = fetch_public(source, timeout=12, max_bytes=4*1024*1024,
                            max_redirects=2, allowed_hosts={HOST}, https_only=True)
    final = urlsplit(str(response.url))
    soup = BeautifulSoup(response.text, 'html.parser')
    root = soup.select_one('.pic1_info_detail')
    if (final.hostname != HOST or final.path != urlsplit(source).path or final.query
            or 'html' not in response.headers.get('content-type','').lower()
            or root is None or access_problem(response.text, str(response.url))):
        raise ProductBodyUnavailable('동일 상품의 공개 상세 설명을 확인하지 못했습니다.')
    root['class'] = [*root.get('class', []), 'product-description']
    result = from_product_html(str(root), str(response.url), ocr_images=ocr_images)
    result.title = 'SK스토아 상품 설명 · ' + final.path.rsplit('/',1)[-1]
    result.source_capture.update(source_page_url=url,
        documents=[{'url':str(response.url), 'status':'read'}],
        acquisition=[{'stage':'public_product_description', 'status':'read'}])
    result.notes.insert(0, '판매페이지가 사용하는 공개 상세 설명에서 본문·이미지를 확인했습니다. 가격·재고·구매 옵션은 포함하지 않습니다.')
    return result
