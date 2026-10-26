"""Public HTML display masking, deliberately separate from search and stored evidence.

This is not an access-control or anonymization boundary: source links, form data,
search indexes and the downloadable original data remain intact.
"""
import re
from bs4 import BeautifulSoup, Comment, Doctype


PUBLIC_TEMPLATES = frozenset({
    "index.html", "card.html", "compare.html", "about.html", "findings.html",
    "check.html", "check_result.html", "_check_body.html", "check_thanks.html",
    "check_receive.html", "capture_receive.html", "capture.html",
})

# Fixed ordering keeps manufacturer aliases stable when more cards are added.
MAKERS = ("삼성전자", "LG전자", "나르왈", "다이슨", "드리미", "로보락",
          "로지텍", "모바", "샤오미", "에코백스", "위니아", "위닉스",
          "캐논", "코웨이", "쿠쿠전자", "필립스", "ASUS", "TP-Link")
MAKER_ALIASES = {
    "삼성전자": ("삼성", "Samsung", "삼성닷컴"),
    "LG전자": ("LG", "엘지", "LG Electronics"),
    "나르왈": ("Narwal",), "다이슨": ("Dyson",), "드리미": ("Dreame",),
    "로보락": ("Roborock",), "로지텍": ("Logitech",), "모바": ("MOVA",),
    "샤오미": ("Xiaomi",), "에코백스": ("Ecovacs",), "위니아": ("Winia",),
    "위닉스": ("Winix",), "캐논": ("Canon",), "코웨이": ("Coway",),
    "쿠쿠전자": ("쿠쿠", "Cuckoo"), "필립스": ("Philips",),
    "ASUS": ("에이수스",), "TP-Link": ("티피링크", "TP Link"),
}
# Brand families / branded feature names can identify a company inside quotations.
BRAND_TERMS = ("Bespoke", "비스포크", "ThinQ", "SmartThings", "스마트싱스",
               "Knox", "녹스", "오브제컬렉션", "휘센", "퓨리케어", "트롬",
               "코드제로", "디봇", "DEEBOT", "Qrevo", "Tapo", "Zenbook",
               "Vivobook", "갤럭시", "Galaxy", "무풍", "윈도우핏",
               "Dreamehome", "에어드레서", "스타일러", "트윈워시")
CATEGORY_NAMES = {"에어드레서": "의류관리기", "스타일러": "의류관리기"}
NOTICE = ("회사명·제품명·모델명은 가명으로 표시합니다. 실제 명칭으로 검색할 수 있습니다. "
          "원문과 원본 데이터를 열면 실제 식별정보가 표시됩니다.")


def letters(number):
    number = max(int(number), 1)
    result = ""
    while number:
        number, rest = divmod(number - 1, 26)
        result = chr(65 + rest) + result
    return result


def product_alias(pid):
    return f"제품 {int(pid):03d}"


def mask_public_html(html, products, *, context=None, fragment=False):
    """Mask rendered text/accessible labels, not URLs, forms, scripts or search data."""
    context = context or {}
    products = [dict(p) for p in products]
    extra = context.get("product")
    if extra and not any(p.get("id") == extra.get("id") for p in products):
        products.append(dict(extra))
    by_id = {str(p["id"]): p for p in products if p.get("id")}
    replacements = {}
    categories = {str(p.get("category") or "").strip() for p in products}
    name_ids = {}
    model_ids = {}
    for p in products:
        if p.get("id"):
            name_ids.setdefault(str(p.get("name") or "").strip(), set()).add(int(p["id"]))
            model_ids.setdefault(str(p.get("model") or "").strip(), set()).add(int(p["id"]))
    for p in products:
        for key, label in (("name", "[제품명]"), ("model", "[모델명]")):
            value = str(p.get(key) or "").strip()
            if len(value) >= 3 and value not in categories:
                ids = (name_ids if key == "name" else model_ids).get(value, set())
                if key == "name" and len(ids) == 1:
                    label = product_alias(next(iter(ids)))
                elif key == "model" and ids:
                    label = f"모델 {min(ids):03d}"
                replacements[value] = label
    pname = context.get("pname")
    if pname and len(str(pname).strip()) >= 3:
        replacements[str(pname).strip()] = "입력한 제품"
    for term in BRAND_TERMS:
        replacements[term] = "[브랜드명]"
    for i, maker in enumerate(MAKERS, 1):
        for term in (maker, *MAKER_ALIASES.get(maker, ())):
            replacements[term] = "제조사 " + letters(i)
    for p in products:
        maker = str(p.get("maker") or "").strip()
        if maker and maker not in {"제조사", "미확인", "기타"} and maker not in replacements:
            replacements[maker] = "[제조사명]"

    # Longest match wins. ASCII boundaries avoid replacing LG inside a model or URL.
    terms = []
    lookup = {}
    for term in sorted(replacements, key=len, reverse=True):
        key = re.sub(r"\s+", " ", term).casefold()
        lookup[key] = replacements[term]
        pattern = r"\s+".join(re.escape(part) for part in term.split())
        if term[0].isascii() and term[0].isalnum():
            pattern = r"(?<![A-Za-z0-9])" + pattern
        if term[-1].isascii() and term[-1].isalnum():
            pattern += r"(?![A-Za-z0-9])"
        terms.append(pattern)
    rx = re.compile("|".join(terms), re.I) if terms else None

    def text(value):
        if value.strip() in CATEGORY_NAMES:
            return value.replace(value.strip(), CATEGORY_NAMES[value.strip()])
        if rx:
            value = rx.sub(lambda m: lookup[re.sub(r"\s+", " ", m[0]).casefold()], value)
        # Related-model references and public certification identifiers in quotations.
        value = re.sub(r"(?<![\w])(?:R-[RC]-[A-Za-z0-9*-]+|[A-Z]{2,}\d[A-Z0-9*_-]{3,})(?![\w])", "[모델·인증번호]", value)
        return value

    soup = BeautifulSoup(html, "html.parser")
    for node in list(soup.find_all(string=True)):
        if isinstance(node, (Comment, Doctype)) or node.find_parent(["script", "style", "textarea"]):
            continue
        masked = text(str(node))
        if masked != str(node):
            node.replace_with(masked)
    for el in soup.find_all(True):
        for attr in ("aria-label", "title", "alt", "data-share", "label"):
            if el.has_attr(attr):
                el[attr] = text(el[attr])
        if el.name == "meta" and el.get("name", el.get("property", "")) in (
            "description", "og:title", "og:description", "twitter:title", "twitter:description"
        ):
            el["content"] = text(el.get("content", ""))

    def label(pid):
        p = by_id.get(str(pid), {})
        category = CATEGORY_NAMES.get(p.get("category"), p.get("category"))
        return product_alias(pid) + (" · " + category if category else "")

    for el in soup.select("#compare-category option[value], optgroup[label]"):
        original_category = el.get("value") if el.name == "option" else None
        if el.name == "option" and original_category in CATEGORY_NAMES:
            count = re.search(r"\(\d+\)", el.get_text())
            el.string = CATEGORY_NAMES[original_category] + " · 별도 분류" + (" " + count[0] if count else "")

    # Structured product headings get unique aliases, even when names are duplicated.
    for el in soup.select("[data-display-product]"):
        pid = el["data-display-product"]
        if str(pid).isdigit():
            el.string = label(pid)
    for article in soup.select("article.label[id^='label-']"):
        pid = article["id"].removeprefix("label-")
        if pid.isdigit():
            heading = article.select_one(".lb-name")
            if heading:
                target = heading.find("a") or heading
                target.string = label(pid)
            article["aria-label"] = label(pid) + " AI 정보카드"
            share = article.select_one("[data-share]")
            if share:
                share["data-share"] = label(pid) + " AI 정보카드"
    for mini in soup.select("a.mini[data-name]"):
        match = re.match(r"/card/(\d+)", mini.get("href", ""))
        if match:
            pid = match[1]
            mini.select_one("h3").string = label(pid)
            mini["data-name"] += " " + label(pid)
    for option in soup.select("option[data-search][value]"):
        pid = option["value"]
        if pid.isdigit():
            old = option.get_text()
            release = re.search(r" · ([0-9][^·]*출시)", old)
            option.string = label(pid) + (" · " + release[1] if release else "")
            option["data-search"] += " " + label(pid)
    for link in soup.select(".mm-from a"):
        match = re.match(r"/card/(\d+)", link.get("href", ""))
        if match:
            link.string = label(match[1]) + " 카드에서 보기"
    for el in soup.select(".shop .bar .url, .quick-meta a"):
        el.string = "제조사 원문" if el.name == "a" else "제품 판매페이지"
    for el in soup.select(".pub code"):
        el.string = "[인증번호]"
    quick = soup.select_one("article.quick .lb-name")
    if quick:
        quick.string = "입력한 제품 · 자동 분석"
    # Request form must retain the actual product name for correct review routing.
    for field in soup.select("article.quick input[name='name']"):
        field["type"] = "hidden"
        field.insert_after(soup.new_string("입력한 제품"))

    if extra and extra.get("id") and soup.title:
        title = label(extra["id"]) + " | AI 정보카드"
        soup.title.string = title
        for el in soup.select('meta[property="og:title"], meta[name="twitter:title"]'):
            el["content"] = title
    # Notice is text, not just a color/blur cue; it is also present in AJAX results.
    notice = soup.new_tag("aside", attrs={"class": "identity-notice", "aria-label": "식별정보 표시 안내", "data-nosnippet": ""})
    strong = soup.new_tag("strong")
    strong.string = "식별정보 마스킹"
    notice.append(strong)
    span = soup.new_tag("span")
    span.string = NOTICE
    notice.append(span)
    main = soup.find("main")
    if main:
        main.insert(0, notice)
    elif fragment:
        soup.insert(0, notice)
    return str(soup)
