"""One acquisition ladder for product URLs; OCR runs once after acquisition.

Retailer denial is not an empty product and is never retried as a bot challenge.
The rendered fallback is isolated and finishes before image decoding begins.
"""
import time
import httpx
from . import product_page, product_documents, page_selector


def fetch_retry(url, fetch):
    for attempt in range(2):
        try:
            return fetch(url)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            transient = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in (502, 503, 504)
            if attempt or not transient:
                raise
            time.sleep(0.25)


class InitialFetchUnavailable(product_page.ProductBodyUnavailable):
    """Both direct connection and rendered acquisition failed transiently."""


def fetch_initial(url, fetch, renderer=None):
    """Recover a transient initial connection failure, not an access denial."""
    try:
        return fetch_retry(url, fetch), None
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError,
            httpx.HTTPStatusError) as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (502, 503, 504):
            raise
        if renderer is None:
            from .rendered_page import render
            renderer = render
        try:
            rendered = renderer(url)
        except (product_page.ProductBodyUnavailable, httpx.TimeoutException,
                httpx.ConnectError, httpx.RemoteProtocolError) as render_error:
            raise InitialFetchUnavailable('직접 연결과 자동 열기로 상품 원문을 받지 못했습니다.') from render_error
        if access_problem(rendered['html'], rendered['url']):
            raise product_page.ProductBodyUnavailable('판매사이트가 로그인 또는 접근 확인을 요구했습니다.')
        return httpx.Response(200, text=rendered['html'], headers={'content-type': 'text/html'},
                              request=httpx.Request('GET', rendered['url'])), rendered


def access_problem(html, url):
    from bs4 import BeautifulSoup
    from urllib.parse import urlsplit
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True).lower() if soup.title else ''
    host = (urlsplit(url).hostname or '').lower()
    if host == 'nid.naver.com' or any(p in title for p in ('로그인', 'access denied', 'captcha', 'just a moment', '접근이 제한', '상품을 찾을 수 없습니다')):
        return True
    return False


def _remember_body(html, url):
    """Keep an identified body before following slower detail links."""
    from .. import check_budget
    if not check_budget.active():
        return
    from bs4 import BeautifulSoup
    from .source_capture import capture_product_html
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    check_budget.remember_title(title)
    try:
        capture = capture_product_html(html, url)
    except product_page.ProductBodyUnavailable:
        return
    check_budget.remember_capture(capture, title or url)


def acquire(html, url, fetch, renderer=None):
    """Return an identified product body, with the actual strategies recorded."""
    if access_problem(html, url):
        raise product_page.ProductBodyUnavailable('판매사이트가 로그인·접근 제한 또는 상품 없음 화면을 보내 왔습니다. 공개된 상품 상세주소인지 확인해 주세요.')
    _remember_body(html, url)
    overview = product_documents.overview_link(html, url)
    canonical = None
    if not overview:
        try:
            body = product_page.product_body(html, url)
            needs_detail = body.section.get('data-aicard-selection', '').startswith('structured_product_')
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
            _remember_body(html, url)
            overview_stages.append({'stage': 'canonical_product_detail' if canonical else 'product_overview', 'status': 'read', 'url': final})
        except (ValueError, httpx.HTTPError):
            overview_stages.append({'stage': 'canonical_product_detail' if canonical else 'product_overview', 'status': 'unavailable'})
    prepared, documents = product_documents.prepare(html, url, lambda u: fetch_retry(u, fetch))
    _remember_body(prepared, url)
    stages = overview_stages + [{'stage': 'html_and_linked_description', 'status': 'read'}]
    have_body = False
    summary_only = False
    try:
        body = product_page.product_body(prepared, url)
        have_body = True
        summary_only = body.section.get('data-aicard-selection', '').startswith('structured_product_')
        if not summary_only and not any(d['status'] == 'unavailable' for d in documents):
            return prepared, url, documents, stages
        stages[-1]['status'] = 'partial'
    except product_page.ProductBodyUnavailable:
        stages[-1]['status'] = 'insufficient'
    selector_attempted = False
    if page_selector.llm.available() and len(page_selector.candidates(prepared)[1]) >= 2:
        selector_attempted = True
        selected = page_selector.recover(prepared, url)
        if selected:
            product_page.product_body(selected, url)
            _remember_body(selected, url)
            stages.append({'stage':'source_block_selection', 'status':'read'})
            return selected, url, documents, stages
        stages.append({'stage':'source_block_selection', 'status':'unavailable'})
    if renderer is None:
        from .rendered_page import render
        renderer = render
    try:
        rendered = renderer(url)
        final_url = rendered['url']
        if access_problem(rendered['html'], final_url):
            raise product_page.ProductBodyUnavailable('판매사이트가 자동 수집에 로그인·접근 확인을 요구해 상품 설명을 받지 못했습니다.')
        recovered, extra = product_documents.prepare(rendered['html'], final_url, lambda u: fetch_retry(u, fetch))
        if not selector_attempted:
            try:
                found = product_page.product_body(recovered, final_url)
                need_selection = found.section.get('data-aicard-selection','').startswith('structured_product_')
            except product_page.ProductBodyUnavailable:
                need_selection = True
            if need_selection:
                selected = page_selector.recover(recovered, final_url)
                if selected:
                    recovered = selected
                    stages.append({'stage':'source_block_selection', 'status':'read'})
        # A blank browser shell is NOT a successful negative AI finding.
        recovered_body = product_page.product_body(recovered, final_url)
        if have_body and recovered_body.section.get('data-aicard-selection', '').startswith('structured_product_'):
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
