"""Public GS Shop description data, without executing retailer JavaScript.

Only the current product's declared description is used. Review/recommendation
state and general page JSON are never treated as product copy.
"""
import json
import re
from urllib.parse import urlsplit, urljoin, parse_qs
from bs4 import BeautifulSoup


def compuzone_product_id(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.netloc != 'www.compuzone.co.kr'
            or p.path != '/product/product_detail.htm'):
        return None
    ids = parse_qs(p.query, keep_blank_values=True).get('ProductNo', [])
    return ids[0] if len(ids) == 1 and re.fullmatch(r'\d{1,12}', ids[0]) else None


def compuzone_description_link(soup, url):
    product = compuzone_product_id(url)
    if not product or soup.select_one('#pdtl_product_detail_img .product_detail_data') is None:
        return None
    # Follow only the published description request for this exact product.
    # Do not execute scripts or fetch recommendation/review AJAX endpoints.
    script = '\n'.join(n.get_text() for n in soup.select('script:not([src])'))
    ids = re.findall(r"\bconst\s+PRODUCT_NO\s*=\s*['\"](\d+)['\"]\s*;", script)
    if ids != [product]:
        return None
    if ('?actype=getProductDetail&productNo=${PRODUCT_NO}' not in script
            or '../product/product_detail_ajax.php${params}' not in script):
        return None
    return urljoin(url, 'product_detail_ajax.php?actype=getProductDetail&productNo=' + product)


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


def product_heading(soup, url):
    """Keep the actual current product name alongside recovered image copy."""
    p = urlsplit(url)
    from .product_page import himart_product_id
    himart = himart_product_id(url)
    if himart:
        def meta(key):
            nodes = soup.select('meta[property="' + key + '"][content]')
            return nodes[0]['content'].strip() if len(nodes) == 1 else ''
        names = soup.select('.hm-goods-name')
        name = names[0].get_text(' ', strip=True) if len(names) == 1 else ''
        # The social title is only the retailer name. Require the visible
        # product name and independently published item ID/name to agree.
        if (name and meta('eg:itemId') == himart
                and meta('eg:itemName') == name and meta('og:description') == name):
            return name
        return ''
    titles = soup.select('meta[property="og:title"][content]')
    if len(titles) != 1:
        return ''
    og = titles[0]['content'].strip()
    product = gs_product_id(url)
    if product:
        data = _gs_product(soup)
        name = data.get('prdNm')
        if (str(data.get('prdCd')) == product and isinstance(name, str)
                and name.strip() == og.removeprefix('[GS SHOP]').strip()):
            return name.strip()
    elif p.hostname == 'www.11st.co.kr' and re.fullmatch(r'/products/\d+/?', p.path):
        names = [n.get_text(' ', strip=True) for n in soup.select('h1.title')]
        if len(names) == 1 and names[0] == og.removeprefix('[11번가]').strip():
            return names[0]
    elif p.netloc == 'www.29cm.co.kr' and re.fullmatch(r'/products/\d+/?', p.path):
        names = soup.select('h2#pdp_product_name')
        canonical = soup.select('link[rel="canonical"][href]')
        if len(names) > 1 or len(canonical) != 1:
            return ''
        target = urlsplit(urljoin(url, canonical[0]['href']))
        name = names[0].get_text(' ', strip=True) if names else ''
        if not names:
            # The streamed page can supply its name in published Product JSON
            # before the visible heading is hydrated. Require both item IDs,
            # canonical URL and social title; never use the social title alone.
            products = []
            for node in soup.select('script[type="application/ld+json"]'):
                try:
                    value = json.loads(node.string or node.get_text())
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict) and value.get('@type') == 'Product':
                    products.append(value)
            item = p.path.rstrip('/').rsplit('/', 1)[-1]
            if (len(products) == 1 and products[0].get('sku') == item
                    and products[0].get('@id') == 'urn:29cm:product:' + item
                    and isinstance(products[0].get('name'), str)):
                name = products[0]['name'].strip()
        # The actual product name, social title, and canonical product ID must
        # agree. A title alone or another product's recommendation is not copy.
        if (name and target.scheme == p.scheme and target.netloc == p.netloc
                and target.path.rstrip('/') == p.path.rstrip('/')
                and not target.query and not target.fragment
                and og in (name, name + ' - 모든 취향 곁에, 29CM')):
            return name
    return ''
