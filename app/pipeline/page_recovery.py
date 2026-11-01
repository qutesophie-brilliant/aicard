"""One acquisition ladder for product URLs; OCR runs once after acquisition.

Retailer denial is not an empty product and is never retried as a bot challenge.
The rendered fallback is isolated and finishes before image decoding begins.
"""
import time
import httpx
from . import product_page, product_documents


def fetch_retry(url, fetch):
    for attempt in range(2):
        try:
            return fetch(url)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            transient = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in (502, 503, 504)
            if attempt or not transient:
                raise
            time.sleep(0.25)


def access_problem(html, url):
    from bs4 import BeautifulSoup
    from urllib.parse import urlsplit
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True).lower() if soup.title else ''
    host = (urlsplit(url).hostname or '').lower()
    if host == 'nid.naver.com' or any(p in title for p in ('로그인', 'access denied', 'captcha', 'just a moment', '접근이 제한', '상품을 찾을 수 없습니다')):
        return True
    return False


def acquire(html, url, fetch, renderer=None):
    """Return an identified product body, with the actual strategies recorded."""
    if access_problem(html, url):
        raise product_page.ProductBodyUnavailable('판매사이트가 로그인·접근 제한 또는 상품 없음 화면을 보내 왔습니다. 공개된 상품 상세주소인지 확인해 주세요.')
    overview = product_documents.overview_link(html, url)
    canonical = None
    if not overview:
        try:
            body = product_page.product_body(html, url)
            needs_detail = body.section.get('data-aicard-selection') == 'structured_product_description'
        except product_page.ProductBodyUnavailable:
            needs_detail = True
        if needs_detail:
            canonical = product_documents.canonical_detail_link(html, url)
    overview = overview or canonical
    overview_stages = []
    if overview:
        try:
            response = fetch_retry(overview, fetch)
            if 'html' not in response.headers.get('content-type', '').lower():
                raise ValueError('not_html')
            final = str(response.url)
            if access_problem(response.text, final):
                raise ValueError('access_required')
            product_page.product_body(response.text, final)
            html, url = response.text, final
            overview_stages.append({'stage': 'canonical_product_detail' if canonical else 'product_overview', 'status': 'read', 'url': final})
        except (ValueError, httpx.HTTPError):
            overview_stages.append({'stage': 'canonical_product_detail' if canonical else 'product_overview', 'status': 'unavailable'})
    prepared, documents = product_documents.prepare(html, url, lambda u: fetch_retry(u, fetch))
    stages = overview_stages + [{'stage': 'html_and_linked_description', 'status': 'read'}]
    have_body = False
    summary_only = False
    try:
        body = product_page.product_body(prepared, url)
        have_body = True
        summary_only = body.section.get('data-aicard-selection') == 'structured_product_description'
        if not summary_only and not any(d['status'] == 'unavailable' for d in documents):
            return prepared, url, documents, stages
        stages[-1]['status'] = 'partial'
    except product_page.ProductBodyUnavailable:
        stages[-1]['status'] = 'insufficient'
    if renderer is None:
        from .rendered_page import render
        renderer = render
    try:
        rendered = renderer(url)
        final_url = rendered['url']
        if access_problem(rendered['html'], final_url):
            raise product_page.ProductBodyUnavailable('판매사이트가 자동 수집에 로그인·접근 확인을 요구해 상품 설명을 받지 못했습니다.')
        recovered, extra = product_documents.prepare(rendered['html'], final_url, lambda u: fetch_retry(u, fetch))
        # A blank browser shell is NOT a successful negative AI finding.
        recovered_body = product_page.product_body(recovered, final_url)
        if have_body and recovered_body.section.get('data-aicard-selection') == 'structured_product_description':
            stages.append({'stage': 'browser_render', 'status': 'no_additional_detail'})
            return prepared, url, documents, stages
    except product_page.ProductBodyUnavailable:
        if not have_body:
            raise
        stages.append({'stage':'browser_render', 'status':'unavailable'})
        return prepared, url, documents, stages
    prepared = recovered
    documents = extra
    stages.append({'stage': 'browser_render', 'status': 'read',
                   'requests': rendered.get('requests', 0),
                   'blocked_requests': rendered.get('blocked_requests', 0)})
    return prepared, final_url, documents, stages
