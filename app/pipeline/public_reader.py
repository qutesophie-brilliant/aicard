"""Bounded HTML acquisition from a public reader, without generated content.

Used once for a directly refused public product page. Credentials, signed URLs,
login/challenge responses and changed product identities are never accepted.
The returned HTML still goes through ordinary product selection and OCR.
"""
import re
from urllib.parse import parse_qsl, urlsplit

import httpx
from bs4 import BeautifulSoup

from .. import check_budget
from ..network_safety import fetch_public, public_addresses
from ..product_identity import source_identity
from .product_page import ProductBodyUnavailable


def read(url):
    parsed, _ = public_addresses(url)
    clean = str(parsed)
    if (parsed.scheme != 'https' or parsed.port not in (None, 443)
            or source_identity(clean) is None
            or re.search(r'/(?:login|signin|oauth|account|checkout|admin|review)(?:/|$)', parsed.path, re.I)
            or any(re.search(r'token|secret|password|signature|credential|auth|session|api.?key|email', k, re.I)
                   for k, _ in parse_qsl(urlsplit(clean).query))):
        raise ProductBodyUnavailable('공개 상품 주소만 보조 수집할 수 있습니다.')
    if check_budget.remaining(30) < 5:
        raise ProductBodyUnavailable('원문을 추가로 가져올 시간이 부족합니다.')
    timeout = check_budget.remaining(30)
    response = fetch_public('https://r.jina.ai/' + clean, timeout=timeout,
                            max_bytes=3 * 1024 * 1024, max_redirects=0,
                            allowed_hosts={'r.jina.ai'}, https_only=True,
                            headers={'Accept': 'application/json', 'X-Return-Format': 'html',
                                     'X-Timeout': str(max(1, min(25, int(timeout) - 1))), 'DNT': '1'})
    payload = response.json()
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or payload.get('code') != 200 or data.get('httpStatus') != 200:
        raise ProductBodyUnavailable('보조 수집 경로에서도 상품 원문을 받지 못했습니다.')
    # Do not accept a reader redirect to a different model, homepage or login.
    for target in (data.get('url'), data.get('finalUrl')):
        if not isinstance(target, str) or source_identity(target) != source_identity(clean):
            raise ProductBodyUnavailable('보조 수집 결과의 상품 주소가 일치하지 않습니다.')
        public_addresses(target)
    html = data.get('html')
    if not isinstance(html, str) or not 100 <= len(html) <= 3 * 1024 * 1024:
        raise ProductBodyUnavailable('상품 원문 HTML을 확인하지 못했습니다.')
    from .page_recovery import access_problem
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    visible = soup.get_text(' ', strip=True)
    if access_problem(html, clean) or (len(visible) < 2000 and re.search(
            r'접속이\s*(?:잠시\s*)?제한|비정상적인\s*접근|자동화된\s*환경|verify\s+you\s+are\s+human|captcha|access\s+denied', visible, re.I)):
        raise ProductBodyUnavailable('보조 수집 경로가 접근 제한 화면을 반환했습니다.')
    return httpx.Response(200, text=html, headers={'content-type': 'text/html'},
                          request=httpx.Request('GET', clean))
