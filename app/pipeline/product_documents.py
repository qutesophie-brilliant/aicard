"""Recover explicitly linked product descriptions, with bounded safe fetches.

Does not execute scripts, guess private endpoints, scrape reviews or circumvent
login/challenge pages. Every remote fetch uses the existing pinned public-IP guard.
"""
from html import escape
import json
import re
from urllib.parse import urljoin, urlsplit, parse_qs
from bs4 import BeautifulSoup
from . import product_page


def canonical_detail_link(html, url):
    """Follow only a published same-origin extension of this product's path.

    Short product links sometimes serve a JS redirect/error shell while their
    canonical URL includes the full product slug. Never guess URLs or follow a
    canonical to the homepage, another product, or an external origin.
    """
    parsed = urlsplit(url)
    path = parsed.path.rstrip('/')
    if len(path.split('/')) < 3 or not re.search(r'\d', path):
        return None
    soup = BeautifulSoup(html, 'html.parser')
    links = soup.select('link[rel="canonical"][href]')
    if len(links) != 1:
        return None
    target = urljoin(url, links[0]['href'])
    dest = urlsplit(target)
    if (dest.scheme == parsed.scheme and dest.netloc == parsed.netloc
            and not dest.query and not dest.fragment
            and dest.path.startswith(path + '/')
            and dest.path.rstrip('/') != path):
        return target
    return None


def overview_link(html, url):
    """Use the product's published overview link; never guess another product URL."""
    parsed = urlsplit(url)
    if parsed.hostname not in ('mi.com', 'www.mi.com') or not re.fullmatch(r'/[a-z]{2}/product/[^/]+/buy/?', parsed.path):
        return None
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup.select('script:not([src])'):
        match = re.match(r'\s*window\.__PRELOADED_STATE__\s*=\s*', script.get_text())
        if not match: continue
        try:
            data, _ = json.JSONDecoder().raw_decode(script.get_text()[match.end():])
        except (ValueError, TypeError): continue
        buttons = data.get('pageButton', []) if isinstance(data, dict) else []
        for button in buttons if isinstance(buttons, list) else []:
            href = button.get('link') if isinstance(button, dict) else None
            if not isinstance(href, str): continue
            target = urljoin(url, href)
            dest = urlsplit(target)
            if (dest.scheme == 'https' and dest.hostname == parsed.hostname
                    and dest.path.rstrip('/') == parsed.path.rstrip('/').removesuffix('/buy')
                    and not dest.query and not dest.fragment):
                return target
    return None


def linked_html_descriptions(soup, url):
    """Read literal GET/HTML loads into product-detail containers; never run JS.

    Retailers often attach a public HTML description after the page loads. Only
    the URL actually published in that loader is followed. Variable expressions,
    POSTs, reviews, and general-purpose page containers are not accepted.
    """
    patterns = (
        re.compile(r'''commonAjax\(\s*['"](?P<url>[^'"\s]+)['"]\s*,\s*['"]['"]\s*,\s*['"]html['"]\s*,\s*['"]GET['"]\s*,\s*(?:true|false)\s*\)\s*\.done\(\s*function\(\s*(?P<arg>\w+)[^)]*\)\s*\{\s*\$\(\s*['"](?P<target>[.#][\w-]+)['"]\s*\)\.append\(\s*(?P=arg)\s*\)'''),
        re.compile(r'''\$\(\s*['"](?P<target>[.#][\w-]+)['"]\s*\)\.load\(\s*['"](?P<url>[^'"\s]+)['"]\s*\)'''),
    )
    links = []
    for script in soup.select('script:not([src])'):
        for pattern in patterns:
            for match in pattern.finditer(script.get_text()):
                targets = soup.select(match['target'])
                if len(targets) != 1:
                    continue
                target = targets[0]
                ancestors = [target, *target.parents]
                identities = [' '.join([n.get('id', ''), *n.get('class', [])])
                              for n in ancestors if n.name not in ('html', 'body', '[document]')]
                if any(product_page._NOISE.search(i) for i in identities):
                    continue
                if not any(re.search(r'(?:product|goods|prd)[_-]?(?:description|detail|info)|description', i, re.I)
                           for i in identities):
                    continue
                src = urljoin(url, match['url'])
                if urlsplit(src).scheme in ('http', 'https') and src != url and src not in links:
                    links.append(src)
    return links


def published_seller_frame(soup, url):
    """Resolve 11st's published detail URL prefix for this exact product only.

    No script execution or endpoint discovery: require the literal page variable,
    the matching description iframe and the product ID in the current URL.
    """
    parsed = urlsplit(url)
    product = re.fullmatch(r'/products/(\d+)/?', parsed.path)
    if parsed.hostname != 'www.11st.co.kr' or not product:
        return None
    frame = soup.select_one('#ifrmDesc iframe#prdDescIfrm')
    if frame is None or frame.get('src') != 'about:blank':
        return None
    prefixes = []
    for script in soup.select('script:not([src])'):
        prefixes += re.findall(r'''\bvar\s+_PRODUCT_DETAIL_URL_\s*=\s*["']([^"']+)["']\s*;''', script.get_text())
    if len(set(prefixes)) != 1:
        return None
    prefix = prefixes[0]
    dest = urlsplit(prefix)
    if (dest.scheme not in ('http', 'https') or dest.hostname != parsed.hostname
            or dest.port or dest.username or dest.password or dest.fragment
            or dest.path != '/product/SellerProductDetail.tmall'
            or parse_qs(dest.query, keep_blank_values=True) !=
               {'method':['getSellerProductDetail'], 'prdNo':['']}
            or not prefix.endswith('prdNo=')):
        return None
    return prefix + product.group(1)


def matching_description_frame(src, url):
    """Gmarket frames must describe this item, not another group item's detail."""
    parent, child = urlsplit(url), urlsplit(src)
    if parent.hostname == 'item.gmarket.co.kr':
        product = parse_qs(parent.query).get('goodsCode')
        return bool(product and len(product) == 1
                    and child.netloc == parent.netloc and child.scheme == parent.scheme
                    and child.path in ('/Item/ItemDetail', '/Item/ItemDetailV2')
                    and parse_qs(child.query).get('goodsCode') == product)
    return True


def prepare(html, url, fetch):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    host = (urlsplit(url).hostname or '').lower()
    if host == 'nid.naver.com' or ('로그인' in title and not soup.select(product_page.DETAIL_SELECTOR)):
        raise product_page.ProductBodyUnavailable(
            '판매사이트가 로그인 화면으로 연결해 상품 설명을 받지 못했습니다. 로그인 없이 열리는 상품 상세주소로 다시 확인해 주세요.')
    missing_heading = any(n.get_text(' ', strip=True).casefold() in
                          ('page not found', '404 not found', 'product not found') for n in soup.select('h1,h2'))
    if missing_heading or any(phrase in title.casefold() for phrase in ('상품을 찾을 수 없습니다', '접근이 제한', 'access denied', 'page not found')):
        raise product_page.ProductBodyUnavailable('판매사이트에서 상품 설명 대신 이용 제한 또는 상품 없음 화면을 보내 왔습니다. 상품 주소를 확인해 주세요.')

    # Iframes must be explicitly inside an identified description, or have a
    # known description-frame ID. Advertising/video/recommendation frames stay out.
    frames = []
    description_roots = soup.select(product_page.DETAIL_SELECTOR) + product_page.semantic_details(soup)
    for root in description_roots:
        for frame in ([root] if root.name == 'iframe' else root.select('iframe')):
            identity = ' '.join([frame.get('id', ''), frame.get('title', ''), frame.get('src', '')]).lower()
            if any(word in identity for word in ('youtube', 'vimeo', 'doubleclick', 'review', 'recommend', 'tracking', 'advert')):
                continue
            src = urljoin(url, frame.get('data-src') or frame.get('src') or '')
            if (src != url and urlsplit(src).scheme in ('http', 'https')
                    and src not in frames and matching_description_frame(src, url)):
                frames.append(src)
    for src in linked_html_descriptions(soup, url):
        if src not in frames:
            frames.append(src)
    seller_frame = published_seller_frame(soup, url)
    if seller_frame and seller_frame not in frames:
        frames.append(seller_frame)
    records = []
    for src in frames[:3]:
        try:
            response = fetch(src)
            if not matching_description_frame(str(response.url), url):
                raise ValueError('description_redirected_to_other_product')
            # Legacy detail URLs can redirect back to the complete product page.
            # Treating that page as a description would import recommendations.
            if str(response.url).rstrip('/') == url.rstrip('/'):
                raise ValueError('description_redirected_to_parent')
            if 'html' not in response.headers.get('content-type', '').lower():
                raise ValueError('not_html')
            child = BeautifulSoup(response.text, 'html.parser')
            child_title = child.title.get_text(' ', strip=True) if child.title else ''
            if any(word in child_title.lower() for word in ('로그인', 'login', 'access denied', 'captcha')):
                raise ValueError('access_required')
            # Explicit description frame permits its body, with navigation,
            # scripts and nested frames removed; never the parent site's body.
            for noise in list(child.select('script,style,noscript,nav,header,footer,iframe,button,object,embed')):
                noise.decompose()
            for node in list(child.find_all(True)):
                if node.parent is None: continue
                identity = ' '.join([node.get('id', ''), *node.get('class', [])])
                if product_page._NOISE.search(identity): node.decompose()
            base = str(response.url)
            for im in child.select('img'):
                im['src'] = urljoin(base, product_page.image_source(im))
                for key in ('ec-data-src','data-src','data-original','data-lazy-src','data-echo','srcset','data-srcset'):
                    im.attrs.pop(key, None)
            for a in child.select('a[href]'): a['href'] = urljoin(base, a['href'])
            fragment = child.body.decode_contents() if child.body else str(child)
            holder = BeautifulSoup('<div class="product-description" data-source-url="' + escape(base, quote=True) + '">' + fragment + '</div>', 'html.parser')
            # Attach within selected root so manufacturer-specific selectors work.
            roots = soup.select(product_page.DETAIL_SELECTOR)
            target = next((r for r in roots if r.name != 'iframe'), soup.body or soup)
            target.append(holder)
            records.append({'url':base, 'status':'read'})
        except Exception as exc:
            records.append({'url':src, 'status':'unavailable', 'reason':type(exc).__name__})
    if len(frames) > 3:
        records.extend({'url':s, 'status':'skipped_limit'} for s in frames[3:])
    return str(soup), records
