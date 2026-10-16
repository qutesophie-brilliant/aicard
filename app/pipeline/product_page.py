"""Select product copy before quick-check extraction; never fall back to site-wide text."""
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


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


def product_body(html: str, url: str) -> BeautifulSoup:
    """Return only identified product sections, retaining their conditions/footnotes.

    Host adapters deliberately exclude price tables, recommendation rails and reviews.
    Unknown layouts need a product description node, rather than a body/main fallback.
    """
    soup = BeautifulSoup(html, "html.parser")
    for node in list(soup.select("script,style,noscript,nav,header,footer,svg,button,[role=button],[role=navigation],[role=menu]")):
        if node.parent is not None:
            node.decompose()
    for node in list(soup.find_all(True)):
        if node.parent is None:
            continue
        identity = " ".join([node.get("id", ""), *node.get("class", [])])
        identity = re.sub(r"([a-z])([A-Z])", r"\1 \2", identity)
        if _NOISE.search(identity):
            node.decompose()

    host = (urlsplit(url).hostname or "").lower()
    if host in ("navimro.com", "www.navimro.com"):
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
    else:
        selector = (
            '[itemprop="description"], #productDescription, #product-description, '
            '.product-description, .product__description, #product-detail, '
            '#productDetail, #prodDetail, .product-detail, .product_detail'
        )
    nodes = soup.select(selector)
    if host in ("lge.co.kr", "www.lge.co.kr") and nodes:
        nodes = [max(nodes, key=lambda n: len(n.get_text(" ", strip=True)))]
        # Accessible video scene descriptions describe staged visuals, not
        # necessarily a product claim. Keep actual copy and footnote dialogs.
        for node in list(nodes[0].select('.cap-section, [title="동영상 해설"], .caption-wrap, .visual-area > .hidden, .pdp-product-table-wrap')):
            if node.parent is not None:
                node.decompose()
    ids = {id(n) for n in nodes}
    roots = [n for n in nodes if not any(id(p) in ids for p in n.parents)]
    selected = BeautifulSoup("<section></section>", "html.parser")
    for node in roots:
        selected.section.append(node)
    text = selected.get_text(" ", strip=True)
    # Empty/short shells and title-only pages are inconclusive, not negative AI findings.
    if len(re.sub(r"\s+", "", text)) < 30:
        raise ProductBodyUnavailable(_UNAVAILABLE)
    return selected


def product_text(body: BeautifulSoup) -> str:
    """Preserve paragraph boundaries without splitting every inline span or line wrap."""
    copy = BeautifulSoup(str(body), "html.parser")
    for br in copy.select("br"):
        br.replace_with(" ")
    for node in copy.select("p,li,dt,dd,h1,h2,h3,h4,h5,h6,div,td,tr"):
        node.append("\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in copy.get_text(" ").splitlines()]
    return "\n".join(line for line in lines if line)
