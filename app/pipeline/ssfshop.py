"""Read SSF's public lazy-loaded product description, without reviews/recommendations.

The storefront's goodsDetail bundle publishes viewGoodsLazyLoading (GET, godNo).
Resolve the product ID from the supplied page, never from a guessed image URL.
"""
import re
from urllib.parse import urlsplit, parse_qs
from bs4 import BeautifulSoup
from .product_page import ProductBodyUnavailable

HOSTS = {'m.ssfshop.com', 'www.ssfshop.com', 'ssfshop.com'}


def detail_url(url):
    p = urlsplit(url)
    if (p.scheme not in ('http', 'https') or p.hostname not in HOSTS
            or p.username or p.password or p.port not in (None, 80, 443)):
        return None
    match = re.fullmatch(r'/[^/]+/([A-Z0-9]{8,24})/good/?', p.path)
    return f'https://{p.hostname}/public/goods/viewGoodsLazyLoading?godNo={match[1]}' if match else None


def read_product(url, html, *, ocr_images):
    from ..network_safety import fetch_public
    from .ingest import from_product_html
    from .page_recovery import access_problem
    source = detail_url(url)
    if not source:
        raise ProductBodyUnavailable('상품 상세 설명 주소를 확인하지 못했습니다.')
    product_id = parse_qs(urlsplit(source).query)['godNo'][0]
    parent = BeautifulSoup(html, 'html.parser')
    # Require the fetched parent to identify this exact product. An unrelated
    # redirect/error page must never be promoted into a successful analysis.
    ids = [n.get('value') for n in parent.select('input[name="godNo"], input#godNo, input#bskGodGodNo')]
    if product_id not in ids or access_problem(html, url):
        raise ProductBodyUnavailable('입력한 주소와 동일한 상품인지 확인하지 못했습니다.')
    response = fetch_public(source, timeout=12, max_bytes=4*1024*1024,
                            max_redirects=2, allowed_hosts={urlsplit(source).hostname}, https_only=True)
    final = urlsplit(str(response.url))
    if (final.hostname != urlsplit(source).hostname or final.path != urlsplit(source).path
            or parse_qs(final.query) != {'godNo': [product_id]}
            or 'html' not in response.headers.get('content-type', '').lower()
            or access_problem(response.text, str(response.url))):
        raise ProductBodyUnavailable('동일 상품의 공개 상세 설명을 확인하지 못했습니다.')
    detail = BeautifulSoup(response.text, 'html.parser')
    root = detail.select_one('#goodsInfoTabArea .prd-pinch-wrapper')
    if root is None or root.select_one('#detailImgDiv') is None:
        raise ProductBodyUnavailable('별도로 불러오는 상품 상세 설명을 확인하지 못했습니다.')
    # goodsInfoTabArea also holds other products. Keep only this product's copy.
    for node in root.select('script, style, button'):
        node.decompose()
    root['class'] = ['product-description']
    page = BeautifulSoup('<html><head></head><body></body></html>', 'html.parser')
    if parent.title:
        page.head.append(parent.title)
    page.body.append(root)
    result = from_product_html(str(page), url, ocr_images=ocr_images)
    result.source_capture.update(source_page_url=url,
        documents=[{'url': str(response.url), 'status': 'read'}],
        acquisition=[{'stage': 'public_product_description', 'status': 'read'}])
    result.notes.insert(0, '판매페이지가 별도로 불러오는 상품 상세 설명과 이미지를 가져와 확인했습니다.')
    return result
