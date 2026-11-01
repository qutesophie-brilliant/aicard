"""Select product copy before quick-check extraction; never fall back to site-wide text."""
import re
import json
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment


class ProductBodyUnavailable(ValueError):
    pass


_NOISE = re.compile(
    r"(?:^|[\s_-])(?:gnb|lnb|nav|navigation|menu|breadcrumb|header|footer|"
    r"recommend(?:ed|ation|ations)?|related|crosssell|upsell|review|reviews|"
    r"rating|ratings|comment|comments|cart|cookie|cookies|banner)(?:$|[\s_-])", re.I
)
_UNAVAILABLE = (
    "상품 설명 본문을 충분히 읽지 못했어요. 이미지나 별도 화면으로 불러오는 설명은 "
    "주소만으로 확인하기 어려울 수 있어요. 상세페이지를 캡처한 이미지·PDF를 올리거나, "
    "제품 설명을 복사해 붙여 넣어 주세요."
)


def image_source(image):
    """Resolve lazy image attributes used by product-detail editors."""
    lazy = next((image.get(key) for key in
                 ('ec-data-src', 'data-src', 'data-original', 'data-lazy-src', 'data-echo')
                 if image.get(key) and not image.get(key).startswith('data:')), '')
    if lazy:
        return lazy
    srcset = image.get('data-srcset') or image.get('srcset')
    if not srcset and image.find_parent('picture'):
        sources = image.find_parent('picture').select('source[data-srcset], source[srcset]')
        # Prefer the unqualified/desktop source, not a small mobile thumbnail.
        sources.sort(key=lambda n: bool(n.get('media') and 'max-width' in n['media']))
        srcset = next((n.get('data-srcset') or n.get('srcset') for n in sources), '')
    if srcset:
        # Largest explicitly supplied variant; no URL construction or alt-text OCR.
        choices = []
        # CDN transformation queries can contain commas (e.g. fit=stretch,1).
        # Only split candidate separators, not characters inside the URL.
        for entry in re.split(r',\s+|(?<=[wx]),\s*', srcset.strip()):
            parts = entry.strip().split()
            if parts and not parts[0].startswith('data:'):
                try: size = float(parts[1].rstrip('wx')) if len(parts) > 1 else 1
                except ValueError: size = 1
                choices.append((size, parts[0]))
        if choices:
            return max(choices)[1]
    return image.get('src', '') if not image.get('src', '').startswith('data:') else ''


DETAIL_SELECTOR = (
    '[itemprop="description"], #productDescription, #product-description, '
    '.product-description, .product__description, .product-feature, #product-detail, '
    '#productDetail, #prodDetail, #prdDetail, .product-detail, .product_detail, '
    '#compGoodsFeatures, #prdDetailContent, #prdDetailCont, #goods_description, '
    '#goodsDescription, #goods_contents, #goodsContents, .goods_description, '
    '#sit_inf_explan, .prod_view_detail, .prd_detail.prd_editor, '
    '.xans-product-additional #prdDetail .cont, #detailIfrm, #ifrmDesc, '
    '#productDescriptionArea, #llis-root, #llis-goodsdetail-frame, '
    'body.template-product .product-media-and-text, '
    'body.template-product .shopify-section[id*="__product_specifications_"], '
    'body.template-product main .common-section-container:is('
    '.new-common-product-swiper-section, .new-common-scroll-text, '
    '.new-common-img-layout, .media-container), '
    '.product-info .ndev-product-key-features, '
    '.shopify-section.shopify-ndev-narwal-main, '
    '#main-content > .shopify-section > section:is('
    '.ndev-x30-ksp, .ndev-x30-product-parameters, .ndev-x30-product-sell-point, '
    '.ndev-x30-other-function, .ndev-x30-install-environment, .ndev-x30-app-control, .ndev-x30-specs), '
    '#product-features, .product-features, [data-testid="product-features"], '
    '#see-all-benefits, #CMD.features, '
    '.template-product .overview-tab, '
    '.productOverview .overContent, .product-info .overview-content, '
    'main .shopify-section--custom-html .overview-content, '
    'main #ipc-listing .ipc_template > :is('
    '[id^="GraphicMore_"], [id^="MultiLayoutGraphicBlock_"], '
    '[id^="GraphicOverlay_"], [id^="AplusDesc_"], [id^="Graphic_"], '
    '[id^="ImageWithText_"]), '
    '#overviewContentInsertWrapper #CMD:has(section.wd__section)'
)


def _detail_media(node):
    return (node.name == 'c-hp-hero-banner' or node.find_parent('c-hp-hero-banner') is not None or
            'c-hp-hero-banner__description' in node.get('class', []) or
            ('heroBanner' in node.get('class', []) and node.find('c-hp-hero-banner') is not None) or
            'usp-banner-wrap' in node.get('class', []) or
            ('common-section-container' in node.get('class', []) and
             'new-common-product-swiper-section' in node.get('class', [])) or
            # Product carousel layout, not the site's navigation header.
            ('header-content' in node.get('class', []) and
             node.find_parent(class_='new-common-product-swiper-section') is not None) or
            ('list-item-banner' in node.get('class', []) and
             node.find_parent(class_='product-media-and-text') is not None))


def _detail_heading(node):
    # A feature-card-header is a product heading, not the site's header.
    # Retaining it also stops a preceding specification joining the next feature.
    return ((node.name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
             and node.find_parent(class_=lambda value: value in ('feature-card', 'product-feature')) is not None)
            or ('LargeStaticImageHotSpot__ContentHeader' in node.get('class', [])
                and node.find_parent(class_='LargeStaticImageHotSpot__DescriptionContainer') is not None)
            or ('c-hp-ksp-carousel__header' in node.get('class', [])
                and node.find_parent('c-hp-ksp-carousel') is not None))


def structured_description(soup):
    """One Product or ProductGroup's shared description, never variant/list copy."""
    products = []
    def visit(value, depth=0):
        if depth > 8: return
        if isinstance(value, list):
            for child in value[:100]: visit(child, depth + 1)
        elif isinstance(value, dict):
            types = value.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(t in types for t in ('Product', 'ProductGroup')):
                products.append(value)
            for key in ('@graph', 'mainEntity'):
                if key in value: visit(value[key], depth + 1)
    for script in soup.select('script[type="application/ld+json"]'):
        try: visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError): pass
    if len(products) != 1: return ''
    description = products[0].get('description')
    return description if isinstance(description, str) and len(description) <= 500000 else ''


def embedded_description(soup):
    """Read JSON data, not JavaScript. Only explicit product detail fields."""
    descriptions = []
    def visit(value, product=False, depth=0):
        if depth > 8 or not isinstance(value, dict): return
        if product:
            for key in ('detailHtml', 'descriptionHtml', 'detailContent', 'detailDescription'):
                text = value.get(key)
                if isinstance(text, str) and 30 <= len(text) <= 500000:
                    descriptions.append(text)
        for key in ('props','pageProps','data','product','productInfo','productDetail','goods','goodsInfo'):
            child = value.get(key)
            if isinstance(child, dict):
                visit(child, product or key in ('product','productInfo','productDetail','goods','goodsInfo'), depth+1)
    for script in soup.select('script[type="application/json"], script#__NEXT_DATA__'):
        try: visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError): pass
    descriptions = list(dict.fromkeys(descriptions))
    return descriptions[0] if len(descriptions) == 1 else ''


def product_faq(soup):
    """Last-resort partial source: visible FAQs explicitly naming this model.

    Require two questions beginning with the sole model-bearing H1, and answers
    present in page text. Generic AI primers, shopping FAQs and other models do
    not qualify. No JavaScript is executed and no metadata-only answer is added.
    """
    norm = lambda text: re.sub(r'\s+', '', text).casefold()
    titles = soup.select('h1')
    if len(titles) != 1:
        return ''
    name = norm(titles[0].get_text(' ', strip=True))
    if len(name) < 8 or not re.search(r'\d', name):
        return ''
    visible = BeautifulSoup(str(soup), 'html.parser')
    for node in list(visible.select('script,style,nav,header,footer')):
        node.decompose()
    visible_text = norm(visible.get_text(' ', strip=True))
    matches = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        entries = value if isinstance(value, list) else [value]
        for entry in entries[:20]:
            if not isinstance(entry, dict) or entry.get('@type') != 'FAQPage':
                continue
            questions = entry.get('mainEntity', [])
            if not isinstance(questions, list):
                continue
            for item in questions[:50]:
                if not isinstance(item, dict) or item.get('@type') != 'Question':
                    continue
                question = item.get('name', '')
                answer = item.get('acceptedAnswer', {})
                answer = answer.get('text', '') if isinstance(answer, dict) else ''
                if not isinstance(question, str) or not isinstance(answer, str):
                    continue
                answer = BeautifulSoup(answer, 'html.parser').get_text(' ', strip=True)
                if (norm(question).startswith(name) and 30 <= len(answer) <= 5000
                        and norm(question) in visible_text and norm(answer) in visible_text):
                    matches.append((question, answer))
    matches = list(dict.fromkeys(matches))
    if len(matches) < 2:
        return ''
    fragment = BeautifulSoup('<section></section>', 'html.parser')
    for question, answer in matches[:10]:
        heading, paragraph = fragment.new_tag('h2'), fragment.new_tag('p')
        heading.string, paragraph.string = question, answer
        fragment.section.extend([heading, paragraph])
    return str(fragment.section)


DETAIL_LABELS = {
    '상품상세', '상품상세정보', '상품상세설명', '상품설명', '제품상세',
    '제품상세정보', '제품설명', '제품특징', '상세설명', '주요기능', '주요특징',
    'productdetails', 'productdescription', 'productfeatures', 'technicaldetails', 'keyfeatures',
    'featurehighlights',
}


def _detail_label(text):
    return re.sub(r'[\s:：]+', '', text).lower() in DETAIL_LABELS


def linked_detail_sections(soup):
    """Follow explicit in-page description controls, never remote/action links."""
    roots = []
    for control in soup.select('button,[role="tab"],a[href^="#"]'):
        if not _detail_label(control.get_text(' ', strip=True)):
            continue
        target_id = control.get('aria-controls', '')
        if not target_id:
            target_id = control.get('href', '').removeprefix('#')
        if not target_id or re.search(r'\s', target_id):
            continue
        targets = soup.find_all(id=target_id)
        if len(targets) != 1:
            continue
        target = targets[0]
        if target.name not in ('div', 'section', 'article'):
            continue
        if any(parent is target for parent in control.parents):
            continue
        roots.append(target)
    return roots


def semantic_details(soup):
    """Find explicitly labelled product sections without knowing a seller's CSS.

    Do not use the whole main/body or infer product claims from generic images.
    A section ends at the next peer heading, preserving source order and footnotes.
    """
    def named(text):
        return _detail_label(text)
    def rank(node):
        if re.fullmatch(r'h[1-6]', node.name):
            return int(node.name[1])
        value = node.get('aria-level', '2')
        return int(value) if str(value).isdigit() else 2
    roots = linked_detail_sections(soup) + [n for n in soup.select('section[aria-label],article[aria-label],div[role="region"][aria-label],div[role="tabpanel"][aria-label]')
             if named(n.get('aria-label', ''))]
    for heading in soup.select('h1,h2,h3,h4,h5,h6,[role="heading"],p[data-testid="target-text"]'):
        if not named(heading.get_text(' ', strip=True)):
            continue
        if any(root is parent for root in roots for parent in heading.parents):
            continue
        level = rank(heading)
        anchor = heading
        # A title is commonly wrapped in an otherwise empty div. Ascend only
        # those title-only wrappers; never swallow a page-level container.
        for _ in range(2):
            parent = anchor.parent
            if (not parent or parent.name not in ('div', 'header')
                    or parent.get_text(' ', strip=True) != heading.get_text(' ', strip=True)
                    or parent.find(['img', 'iframe', 'table'])):
                break
            anchor = parent
        parts = [str(heading)]
        for sibling in list(anchor.next_siblings)[:60]:
            if getattr(sibling, 'name', None):
                headings = ([sibling] if re.fullmatch(r'h[1-6]', sibling.name) or sibling.get('role') == 'heading'
                            else sibling.select('h1,h2,h3,h4,h5,h6,[role="heading"]'))
                if any(rank(h) <= level for h in headings):
                    break
            parts.append(str(sibling))
        fragment = BeautifulSoup('<section class="product-description">' + ''.join(parts) + '</section>', 'html.parser').section
        if (len(fragment.get_text(' ', strip=True)) - len(heading.get_text(' ', strip=True)) >= 30
                or any(image_source(im) for im in fragment.select('img'))
                or any(frame.get('src') or frame.get('data-src') for frame in fragment.select('iframe'))):
            roots.append(fragment)
    return roots


def product_body(html: str, url: str) -> BeautifulSoup:
    """Return only identified product sections, retaining their conditions/footnotes.

    Host adapters deliberately exclude price tables, recommendation rails and reviews.
    Unknown layouts need a product description node, rather than a body/main fallback.
    """
    soup = BeautifulSoup(html, "html.parser")
    structured = structured_description(soup) or embedded_description(soup)
    faq = '' if structured else product_faq(soup)
    host = (urlsplit(url).hostname or "").lower()
    if host in ('hp.com', 'www.hp.com') and '/laptops/home-laptops/omnibook/' in urlsplit(url).path:
        # This HP animation class is also attached to benefit carousels and
        # accordion answers. It is not a navigation menu in those components.
        for node in soup.select('c-hp-ksp-carousel.dropdown-menu-detached, '
                                'c-hp-ksp-carousel .dropdown-menu-detached, '
                                'c-hp-bg-container#faqs .dropdown-menu-detached'):
            node['class'] = [c for c in node.get('class', []) if c != 'dropdown-menu-detached']
    if host in ('lenovo.com', 'www.lenovo.com') and '/p/' in urlsplit(url).path:
        from .embedded_features import lenovo_features
        features = lenovo_features(soup)
        if features:
            soup.append(BeautifulSoup('<div class="product-description" data-aicard-embedded-features="true">' + features + '</div>', 'html.parser'))
    if host == "rental.lghellovision.net":
        # The seller embeds escaped editor HTML inside a detail/review wrapper.
        # Select the editor first: the shared wrapper is otherwise noise, and
        # its AI review summary must never become a product feature.
        editors = soup.select('.panel-area__detail-info .detail-img')
        fragments = []
        for editor in editors:
            fragments.append(editor.decode_contents() if editor.find(True)
                             else editor.get_text())
        soup = BeautifulSoup('<div class="product-description">' +
                             '\n'.join(fragments) + '</div>', 'html.parser')
    linked_sections = linked_detail_sections(soup)
    for node in list(soup.select("script,style,noscript,nav,header,footer,svg,button,[role=button],[role=navigation],[role=menu]")):
        if node.parent is not None:
            node.decompose()
    for node in list(soup.find_all(True)):
        if node.parent is None or node.name in ('html', 'body', 'main'):
            continue
        # CSS-module layout names such as Layout_NavEmpty can contain "nav"
        # while wrapping the entire main content. Remove actual navigation
        # descendants, not the document's main-content ancestor.
        if node.find('main') is not None:
            continue
        identity = " ".join([node.get("id", ""), *node.get("class", [])])
        identity = re.sub(r"([a-z])([A-Z])", r"\1 \2", identity)
        if _NOISE.search(identity) and not _detail_media(node) and not _detail_heading(node):
            node.decompose()

    if soup.select_one('[data-aicard-ai-selected="true"]'):
        selector = '[data-aicard-ai-selected="true"]'
    elif host in ('global.roborock.com', 'us.roborock.com') and urlsplit(url).path.startswith('/pages/'):
        selector = '[class$="_product-container"]'
    elif host in ('soundcore.com', 'www.soundcore.com'):
        # The feature modules are siblings of purchase controls, FAQs and reviews.
        # Select the modules themselves, never their shared page wrapper.
        selector = ('main #sensor, main .sensor-lists, main .control-container, '
                    'main .charging, main .hiFi, main .swiper-slide:has(.text-pretty), '
                    'main .w-screen:has(#reactNoise)')
    elif host in ('dyson.co.kr', 'www.dyson.co.kr') and soup.select_one('body.catalog-product-view'):
        # Dyson's product CMS uses feature modules rather than a description
        # container. Keep feature copy/images and caveats, never its shop-wide
        # purchase benefits, product finder, customer reviews or promotions.
        selector = ('.dyson_cms_row_config:has(.rich-content-component), '
                    '.dyson_cms_row_config:has(.promos), '
                    '.dyson_cms_row_config[id*="caveat"]')
    elif host in ('mi.com', 'www.mi.com') and re.match(r'^/[a-z]{2}/product/[^/]+/?$', urlsplit(url).path):
        selector = '.product_v4 main[class*="fly-birds-page"]'
    elif host in ('hp.com', 'www.hp.com') and '/laptops/home-laptops/omnibook/' in urlsplit(url).path:
        # HP's product copy lives in custom elements. Keep product benefits and
        # product FAQ; exclude generic AI-PC education, support and model-comparison
        # modules so another configuration's features do not enter this card.
        selector = ('c-hp-hero-banner .c-hp-hero-banner__description, '
                    'c-hp-ksp-carousel#benefits, c-hp-bg-container#discover, '
                    'c-hp-bg-container#faqs, c-hp-footnotes')
    elif host in ('sony.co.kr', 'www.sony.co.kr') and '/products/' in urlsplit(url).path:
        # These are visible feature descriptions, not the feature-navigation
        # cards linking to other pages. Preserve the explanatory paragraph.
        selector = '.LargeStaticImageHotSpot__DescriptionContainer, .Highlights__KeySpec'
    elif host == 'llis-goodsdetail.lotteon.com':
        selector = '#llis-root'
    elif host in ("navimro.com", "www.navimro.com"):
        selector = ".spec-sum > .spec .desc_info"
    elif host == "prod.danawa.com":
        selector = ".spec_list, #productDescriptionArea"
    elif host in ("11st.co.kr", "www.11st.co.kr"):
        selector = "#detailIfrm, #ifrmDesc"
    elif host in ("lge.co.kr", "www.lge.co.kr"):
        # The first section can be a short promotional strip. The complete PC
        # product section contains the feature copy and its footnotes; mobile
        # copies and separate purchase/recommendation modules are not merged.
        selector = "section.wcms-usp-scope.pc"
    elif host in ("samsung.com", "www.samsung.com"):
        # Product features only: the page also contains AI-subscription terms,
        # recommendations and customer reviews that are not product claims.
        selector = "#compGoodsFeatures"
    else:
        selector = DETAIL_SELECTOR
    nodes = soup.select(selector)
    if not nodes or all(n.name == 'iframe' for n in nodes):
        nodes = soup.select(DETAIL_SELECTOR)
    nodes += [node for node in linked_sections if node.parent is not None
              and not any(node is existing for existing in nodes)]
    selection_method = 'explicit_product_container'
    if not nodes or all(len(n.get_text('', strip=True)) < 30 and not any(image_source(im) for im in n.select('img')) for n in nodes):
        discovered = semantic_details(soup)
        if discovered:
            nodes = discovered
            selection_method = 'labelled_product_section'
        elif structured:
            # The single Product's description can identify an otherwise unnamed
            # visible block. Keep its real paragraph/list boundaries instead of
            # replacing it with flattened metadata or launching a browser again.
            # Search only the cleaned DOM: menus, reviews and recommendations
            # cannot qualify. An exact full-text match excludes neighbouring copy.
            norm = lambda value: re.sub(r'\s+', '', value)
            key = norm(BeautifulSoup(structured, 'html.parser').get_text(' ', strip=True))
            matches = [node for node in soup.select('p,div,section,article,ul,ol')
                       if len(key) >= 30 and norm(node.get_text(' ', strip=True)) == key]
            if matches:
                nodes = [min(matches, key=lambda node: len(str(node)))]
                selection_method = 'visible_product_description'
    if host in ("lge.co.kr", "www.lge.co.kr") and nodes:
        for root in nodes:
            for common in list(root.select('.KRP0021')):
                common.decompose()
        nodes = [max(nodes, key=lambda n: len(n.get_text(" ", strip=True)) + 50 * len(n.select('img')))]
        # Accessible video scene descriptions describe staged visuals, not
        # necessarily a product claim. Keep actual copy and footnote dialogs.
        for node in list(nodes[0].select('.cap-section, [title="동영상 해설"], .caption-wrap, .visual-area > .hidden, .pdp-product-table-wrap, .KRP0021')):
            if node.parent is not None:
                node.decompose()
    ids = {id(n) for n in nodes}
    roots = [n for n in nodes if not any(id(p) in ids for p in n.parents)]
    selected = BeautifulSoup("<section></section>", "html.parser")
    selected.section['data-aicard-selection'] = selection_method
    for node in roots:
        selected.section.append(node)
    if selected.select_one('[data-aicard-ai-selected="true"]'):
        selected.section['data-aicard-selection'] = 'ai_selected_product_sections'
    # A product's introduction can sit outside its feature/detail container.
    # Add it only when the single-product description is also present verbatim
    # in the cleaned page. Never promote metadata-only copy or review text.
    if roots and structured:
        intro = BeautifulSoup(structured, 'html.parser').get_text(' ', strip=True)
        squash = lambda value: re.sub(r'\s+', '', value)
        key = squash(intro)
        selected_text = squash(selected.get_text(' ', strip=True))
        if len(key) >= 30 and key not in selected_text:
            visible = any(squash(n.get_text(' ', strip=True)) == key
                          for n in soup.select('p,div,section,article'))
            if visible:
                section = selected.new_tag('section')
                heading = selected.new_tag('h2')
                heading.string = '제품 소개'
                paragraph = selected.new_tag('p')
                paragraph.string = intro
                section.extend([heading, paragraph])
                selected.section.append(section)
    if (not roots or (len(re.sub(r'\s+', '', selected.get_text())) < 30
                      and not any(image_source(im) for im in selected.select('img')))) and structured:
        selected.section.clear()
        selected.section.append(BeautifulSoup(structured, 'html.parser'))
        selected.section['data-aicard-selection'] = 'structured_product_description'
    # Some editors emit escaped HTML rather than child nodes.
    for node in list(selected.select('div,p,section')):
        if not node.find(True) and re.search(r'<(?:img|p|div|table)\b', node.get_text(), re.I):
            fragment = BeautifulSoup(node.get_text(), 'html.parser')
            node.clear()
            node.append(fragment)
    for node in list(selected.select('script,style,iframe,object,embed')):
        node.decompose()
    for node in list(selected.find_all(True)):
        if node.parent is None: continue
        identity = ' '.join([node.get('id', ''), *node.get('class', [])])
        identity = re.sub(r'([a-z])([A-Z])', r'\1 \2', identity)
        if _NOISE.search(identity) and not _detail_media(node) and not _detail_heading(node):
            node.decompose()
    text = selected.get_text(" ", strip=True)
    # Empty/short shells and title-only pages are inconclusive, not negative AI findings.
    if len(re.sub(r"\s+", "", text)) < 30 and not any(image_source(im) for im in selected.select('img')):
        if faq:
            selected.section.clear()
            selected.section.append(BeautifulSoup(faq, 'html.parser'))
            selected.section['data-aicard-selection'] = 'structured_product_faq'
            return selected
        raise ProductBodyUnavailable(_UNAVAILABLE)
    return selected


def product_text(body: BeautifulSoup) -> str:
    """Preserve paragraph boundaries without splitting every inline span or line wrap."""
    copy = BeautifulSoup(str(body), "html.parser")
    # Source-code line wraps inside a text node are layout whitespace, not paragraphs.
    for node in list(copy.find_all(string=True)):
        if isinstance(node, Comment):
            node.extract()
        else:
            node.replace_with(re.sub(r"\s+", " ", str(node)))
    for br in copy.select("br"):
        # Specification cells use BR as a row boundary. Joining their delivery
        # notes to the feature sentence makes the downstream noise filter drop both.
        br.replace_with("\n" if br.find_parent("td") else " ")
    for node in copy.select("p,li,dt,dd,h1,h2,h3,h4,h5,h6,div,td,tr"):
        node.append("\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in copy.get_text(" ").splitlines()]
    from .extract import SCENE_DESCRIPTION
    return "\n".join(line for line in lines if line and not SCENE_DESCRIPTION.search(line))
