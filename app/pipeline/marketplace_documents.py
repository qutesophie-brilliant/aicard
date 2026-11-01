"""Public GS Shop description data, without executing retailer JavaScript.

Only the current product's declared description is used. Review/recommendation
state and general page JSON are never treated as product copy.
"""
import json
import re
from urllib.parse import urlsplit, urljoin, parse_qs
from bs4 import BeautifulSoup


def gs_product_id(url, *, detail=False):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in ('m.gsshop.com', 'www.gsshop.com')
            or p.username or p.password or p.port not in (None, 443)
            or p.path != ('/prd/prdDesc.gs' if detail else '/prd/prd.gs')):
        return None
    ids = parse_qs(p.query, keep_blank_values=True).get('prdid', [])
    return ids[0] if len(ids) == 1 and re.fullmatch(r'\d{6,12}', ids[0]) else None


def _gs_product(soup):
    values = []
    for node in soup.select('script:not([src])'):
        text = node.get_text()
        match = re.match(r'\s*var\s+renderJson\s*=\s*', text)
        if not match or len(text) > 2_000_000:
            continue
        try:
            value, end = json.JSONDecoder().raw_decode(text[match.end():])
        except (ValueError, TypeError, RecursionError):
            continue
        remainder = text[match.end()+end:].lstrip()
        # The parent page declares unrelated config variables after this JSON.
        # Read the literal up to its statement terminator; never execute the rest.
        if remainder and not remainder.startswith(';'):
            continue
        if isinstance(value, dict) and isinstance(value.get('prd'), dict):
            values.append(value['prd'])
    return values[0] if len(values) == 1 else {}


def gs_description_link(soup, url):
    product = gs_product_id(url)
    if not product or soup.select_one('product-description') is None:
        return None
    data = _gs_product(soup)
    href = data.get('prdDescUrl')
    if str(data.get('prdCd')) != product or not isinstance(href, str):
        return None
    source = urljoin(url, href)
    parent, target = urlsplit(url), urlsplit(source)
    if (target.netloc != parent.netloc or target.fragment
            or parse_qs(target.query, keep_blank_values=True) != {'prdid': [product]}
            or gs_product_id(source, detail=True) != product):
        return None
    return source


def expand_gs_description(soup, url):
    """Materialize only the seller HTML assigned to the description container."""
    if not gs_product_id(url, detail=True):
        return
    roots = soup.select('#productDescription')
    if len(roots) != 1 or roots[0].find(True) is not None:
        return
    text = _gs_product(soup).get('prdImgDescd')
    if not isinstance(text, str) or not text.strip() or len(text) > 500_000:
        return
    fragment = BeautifulSoup(text, 'html.parser')
    # Script content is never executed or displayed. Normal downstream product
    # selection also strips navigation, forms, recommendations and reviews.
    for node in list(fragment.select('script,style,iframe,object,embed,form')):
        node.decompose()
    roots[0].append(fragment)
