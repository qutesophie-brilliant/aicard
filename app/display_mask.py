"""Mask only the illustrative card at the top of the home page.

Search listings, comparisons, detail cards, analysis results and source data use
actual identities. This is a presentation treatment of one example only.
"""
import re
from bs4 import BeautifulSoup, Comment

PUBLIC_TEMPLATES = frozenset({"index.html"})

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


def mask_public_html(html, products, *, context=None, fragment=False):
    soup = BeautifulSoup(html, "html.parser")
    example = soup.select_one(".shop")
    if example is None:
        return html
    # Restrict every replacement to the home-page example, including its quotes.
    replacements = {}
    for product in products:
        for key, replacement in (("name", "제품 A"), ("model", "[모델명]"), ("maker", "제조사 A")):
            value = str(product.get(key) or "").strip()
            if value and value != product.get("category"):
                replacements[value] = replacement
    for maker, aliases in MAKER_ALIASES.items():
        for value in (maker, *aliases):
            replacements[value] = "제조사 A"
    for term in BRAND_TERMS:
        replacements[term] = "[브랜드명]"
    terms, lookup = [], {}
    for value in sorted(replacements, key=len, reverse=True):
        lookup[re.sub(r"\s+", " ", value).casefold()] = replacements[value]
        term = r"\s+".join(re.escape(part) for part in value.split())
        if value[0].isascii() and value[0].isalnum():
            term = r"(?<![A-Za-z0-9])" + term
        if value[-1].isascii() and value[-1].isalnum():
            term += r"(?![A-Za-z0-9])"
        terms.append(term)
    rx = re.compile("|".join(terms), re.I)

    def mask(text):
        return rx.sub(lambda m: lookup[re.sub(r"\s+", " ", m[0]).casefold()], text)

    for node in list(example.find_all(string=True)):
        if isinstance(node, Comment) or node.find_parent(["script", "style"]):
            continue
        value = mask(str(node))
        if value != str(node):
            node.replace_with(value)
    for el in example.find_all(True):
        for attr in ("aria-label", "title", "alt"):
            if el.has_attr(attr):
                el[attr] = mask(el[attr])
    heading = example.select_one("h3")
    if heading:
        heading.string = "제품 A"
    address = example.select_one(".bar .url")
    if address:
        address.string = "제품 판매페이지"
    return str(soup)


def catalog_display_label(product):
    """Keep only generic category/specs explicitly present in the product name."""
    category = str(product.get("category") or "제품").strip()
    name = str(product.get("name") or "")
    model = str(product.get("model") or "").strip()
    if model:
        name = name.replace(model, " ")
    parts = []
    if category in ("TV", "모니터"):
        panel = re.search(r"(?<![A-Za-z])(?:OLED|QLED|Mini LED|Micro RGB|LED)(?![A-Za-z])", name, re.I)
        if panel:
            parts.append(panel.group(0))
    parts.append(category)
    title = " ".join(parts)
    specs = re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)*\s*(?:cm|kg|L|ℓ|㎡|인치)(?![A-Za-z0-9])", name, re.I)
    if specs:
        title += " · " + " / ".join(dict.fromkeys(re.sub(r"\s+", "", value) for value in specs))
    return title
