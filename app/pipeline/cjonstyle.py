"""Read the public product-detail response used by CJ OnStyle's detail iframe."""
import hashlib
import re
from html import escape
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .product_page import ProductBodyUnavailable


def detail_api_url(url):
    parsed = urlsplit(url)
    if parsed.hostname not in ('display.cjonstyle.com', 'base.cjonstyle.com'):
        return None
    match = re.fullmatch(r'/p/item/(\d{1,20})(?:/itemExplainAreaInfo)?/?', parsed.path)
    if not match:
        return None
    channel = parse_qs(parsed.query).get('channelCode', ['30002002'])[0]
    if not re.fullmatch(r'\d{8}', channel):
        channel = '30002002'
    return (f'https://display.cjonstyle.com/c/rest/item/{match[1]}/explainAreaInfo'
            f'?channelCode={channel}&isMyzone=false&isEmployee=false')


def read_product(url, *, ocr_images):
    """Use only published description/specification HTML, not promotions or reviews."""
    from ..network_safety import fetch_public
    from .ingest import UA, from_product_html

    api_url = detail_api_url(url)
    response = fetch_public(api_url, timeout=20, max_bytes=4 * 1024 * 1024,
                            max_redirects=2, headers={'User-Agent': UA},
                            allowed_hosts={'display.cjonstyle.com'}, https_only=True)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProductBodyUnavailable('상품 상세정보를 읽지 못했습니다. 상세페이지 캡처나 PDF를 올려 주세요.') from exc
    if not isinstance(payload, dict) or str(payload.get('code')) != '1' or not isinstance(payload.get('result'), dict):
        raise ProductBodyUnavailable('상품 상세정보를 받지 못했습니다. 상세페이지 캡처나 PDF를 올려 주세요.')
    product = payload['result']
    item_id = urlsplit(api_url).path.split('/')[4]
    fragments = []
    for key in ('itemSpecHtmlInfo', 'itemSpecTableInfo1', 'itemSpecTableInfo2'):
        rows = product.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows[:100]:
            if not isinstance(row, dict) or not isinstance(row.get('html'), str):
                continue
            entity = row.get('texplainEntity')
            if isinstance(entity, dict) and str(entity.get('itemCd', item_id)) != item_id:
                continue
            fragment = BeautifulSoup(row['html'], 'html.parser')
            for node in fragment.select('script,style,head,iframe,object,embed'):
                node.decompose()
            root = fragment.body or fragment
            fragments.append(''.join(str(node) for node in root.contents))
    title = product.get('displayItemName')
    title = title if isinstance(title, str) and title.strip() else 'CJ OnStyle 상품 설명'
    html = (f'<title>{escape(title)}</title><div class="product-description">'
            + '\n'.join(fragments) + '</div>')
    result = from_product_html(html, str(response.url), ocr_images=ocr_images)
    # Preserve the fetched response's provenance, rather than presenting the
    # locally assembled wrapper as an original source snapshot.
    digest = hashlib.sha256(response.content).hexdigest()
    result.source_capture.update(source_page_url=url, source_response_sha256=digest,
                                 snapshot_sha256=digest)
    for section in result.source_capture['sections']:
        if section.get('source_type', 'html') == 'html':
            section.update(snapshot_sha256=digest, source_type='product_detail_api')
    result.notes.insert(0, '판매페이지가 별도로 불러오는 공개 상품 상세정보를 확인했습니다.')
    return result
