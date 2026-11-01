"""Conservative topic matching for the unsaved, rule-only quick-check preview.

These rules do not change formal-card judgments or stored research outputs.
"""
import re
from . import coverage

_DATA_SUBJECT = re.compile(
    r"개인\s*정보|개인\s*데이터|(?:사용자|이용자|고객).{0,15}(?:데이터|정보|기록|패턴)|"
    r"(?:사용|이용|출수|시청|청소|생활)\s*(?:이력|기록|패턴|데이터)|"
    r"(?:음성|영상|얼굴|위치|지도|카메라).{0,8}(?:데이터|정보|기록|저장|전송|수집|보관)|"
    r"(?:데이터|정보).{0,15}(?:서버|클라우드|전송|수집|삭제|보관|암호화)|"
    r"(?:서버|클라우드).{0,20}(?:데이터|정보|처리|저장)|"
    r"(?:목소리|음성|영상|얼굴|위치|촬영한?\s*(?:사진|이미지)).{0,25}(?:녹음|녹화|수집|저장|전송|삭제|보관|처리)|"
    r"(?:녹음|녹화).{0,20}(?:파일|기록).{0,25}(?:저장|전송|삭제|보관|처리)", re.I)
_OPTOUT = re.compile(r"비활성화|해제|끄(?:기|면|거나|는|고|세요)|끌\s*수|일반\s*모드|수동\s*모드", re.I)
_EN_OPTOUT = re.compile(
    r"\b(?:disable[ds]?|deactivat(?:e[ds]?|ion))\b|"
    r"\b(?:turn|switch)(?:ed|ing)?\s+(?:\w+[ -]){0,8}off\b|"
    r"\b(?:AI|인공지능|자동|스마트)(?:[ -]\w+){0,5}\s*[:：=]?\s+OFF\b", re.I)
_AI_CONTEXT = re.compile(r"\bAI\b|인공지능|학습|생성형|음성|인식|감지|스마트|맞춤|자동|모드", re.I)
_SAFETY = re.compile(r"안전\s*장치|과열|과부하|누전|어린이.{0,8}잠금|차일드\s*락|업데이트|지원\s*기간|자동\s*정지|긴급\s*(?:정지|멈춤)|보안.{0,20}(?:패치|암호화|인증|보호|지원|취약점)|(?:펌웨어|소프트웨어).{0,20}(?:갱신|패치|배포|지원\s*종료)", re.I)
_REMEDY = re.compile(r"고객\s*센터|서비스\s*센터|A\s*/\s*S|보증|환불|반품|보상|교환|수리|피해\s*구제|소비자\s*분쟁", re.I)
_EN_REMEDY = re.compile(r'\bwarrant(?:y|ies)\b|\b(?:customer|technical)\s+(?:service|support)\b|\b(?:refund|return)\s+policy\b', re.I)
_POWER_ACTION = re.compile(
    r'(?:전원|제품\s*전체|본체|조명|화면|Wi-Fi)(?:을|를|은|는|의)?\s*'
    r'(?:버튼을\s*눌러\s*(?:제품을\s*)?)?'
    r'(?:끄(?:기|면|거나|는|고|세요)|끌\s*수|꺼(?:짐|집니다)|차단|해제|비활성화)', re.I)
_REPLACEMENT = re.compile(
    r'(?:필터|소모품|배터리|브러시|먼지\s*봉투|물걸레|카트리지).{0,35}(?:교환|교체)', re.I)
_SUPPORT_POLICY = re.compile(r'고객\s*센터|서비스\s*센터|A\s*/\s*S|보증|환불|반품|보상|불량|하자|수리|피해\s*구제|소비자\s*분쟁', re.I)
# A multi-brand retailer may include several manufacturers' service contacts
# in one shared image. An explicitly different provider is not this product's
# support. Unknown/unnamed providers are not guessed from a phone number.
_SERVICE_BRANDS = (
    r'캐논|\bCanon\b', r'소니|\bSony\b', r'\bDJI\b|디제이아이',
    r'니콘|\bNikon\b', r'삼성|\bSamsung\b', r'LG전자|\bLG\b',
    r'파나소닉|\bPanasonic\b', r'후지필름|\bFujifilm\b',
    r'올림푸스|\bOlympus\b', r'\bMSI\b',
)


def _other_service_brand(text, product_name):
    target = {i for i, p in enumerate(_SERVICE_BRANDS) if re.search(p, product_name, re.I)}
    named = {i for i, p in enumerate(_SERVICE_BRANDS) if re.search(p, text, re.I)}
    return bool(target and named - target)


def _matches(key: str, text: str) -> bool:
    # A footer sentence must not veto an independent product statement. Keep
    # punctuation and A/S intact; decimal numbers are not sentence boundaries.
    sentences = re.split(r'(?<=[.!?。])\s+(?=\S)|[;\n]+', text)
    return any(_matches_sentence(key, sentence) for sentence in sentences if sentence.strip())


def _matches_sentence(key: str, text: str) -> bool:
    from .extract import SCENE_DESCRIPTION, SITE_COMMON
    if SCENE_DESCRIPTION.search(text) or SITE_COMMON.search(text):
        return False
    if key == "data":
        # Interface throughput is not a statement about collection or handling
        # of a consumer's data. Preserve explicit personal/cloud-data clauses.
        interface_spec = re.search(r'USB\d*|Type[ -]?C|Thunderbolt|썬더볼트|포트|인터페이스', text, re.I)
        throughput = re.search(r'데이터\s*전송|전송\s*속도|[GM]bps|[GM]B/s', text, re.I)
        personal_handling = re.search(r'개인\s*정보|개인\s*데이터|사용자|이용자|고객|수집|서버|클라우드|암호화|삭제|보관', text, re.I)
        if interface_spec and throughput and not personal_handling:
            return False
        return bool(_DATA_SUBJECT.search(text))
    if key == "optout":
        # A refrigerator compartment's power switch does not show that its AI
        # feature can be disabled. Require a named intelligent/automatic function.
        # Product names in a test configuration must not qualify unrelated
        # actions such as "Prestige AI / Disable Wi-Fi / Disable lighting".
        # Keep the named intelligent function and the action in one clause.
        for clause in re.split(r'[/;。!?]|\.(?:\s|$)', text):
            # Translated "unlock AI experiences/potential" is a benefit, not
            # an instruction for turning the feature off.
            clause = re.sub(r'(?:경험|잠재력|가능성)(?:을|를)\s*해제', '', clause)
            # Remove actions on the appliance/power itself before looking for
            # a separate AI-disable instruction. An AI product name elsewhere
            # in this clause cannot turn its power switch into AI opt-out.
            clause = _POWER_ACTION.sub('', clause)
            if not re.search(r'\bAI\b|인공지능|학습|생성형|음성|인식|감지|스마트|자동', clause, re.I):
                continue
            if _OPTOUT.search(clause) or _EN_OPTOUT.search(clause):
                return True
        return False
    if key == "safety":
        if re.search(r'(?:상품|제품|가격|배송|주문|재고)\s*(?:정보|목록|상태)?.{0,8}업데이트', text) and not re.search(r'펌웨어|소프트웨어|보안|안전\s*장치', text):
            return False
        return bool(_SAFETY.search(text))
    if key == "remedy":
        if _REPLACEMENT.search(text) and not (_SUPPORT_POLICY.search(text) or _EN_REMEDY.search(text)):
            return False
        return bool(_REMEDY.search(text) or _EN_REMEDY.search(text))
    # Stock, delivery and return restrictions do not describe AI operation.
    if re.search(r'재고|생산월|매장|반품|교환|철거|회수|추가\s*비용|살균\s*효과', text):
        return False
    if re.search(r'(?:사용|이용)(?:하려면|하기\s*위해).{0,60}앱.{0,20}실행\s*중이어야', text):
        return True
    from .condition_type import condition_type
    explicit_trial=re.search(r'^(?:[\s*※-])*(?:시험|실험|테스트)\s*(?:부하|조건|환경|방법|모델)\s*[:：]',text)
    kind=condition_type(text)
    if kind == 'unspecified' and not re.search(r'경우|따라|필요|권장|제한|이상|이하|미만|초과|에서만|려면|않|못|전용|연결해야|지원하는|저하|느려|어려|달라', text):
        return False
    return not explicit_trial and kind != 'test' and bool(_AI_CONTEXT.search(text)) and (
        kind=='operating' or len(set(coverage._KEYWORDS[key].findall(text))) >= 2)


def fill(index, product_name=''):
    out = {}
    all_sentences = index.condition_context()
    for key, query in coverage.QUERIES.items():
        candidates = index.search(query, k=30, prefer_kinds=("설명서", "사양표", "공공DB"))
        # Retrieval ranks the display, but must not decide whether a category
        # exists. Check all acquired sentences for all five topics, including
        # synonyms and explicit negative statements. Quotes stay unmodified.
        candidates += all_sentences
        hits = []
        seen = set()
        for c in candidates:
            if key == 'remedy' and _other_service_brand(c['quote'], product_name):
                continue
            normalized = re.sub(r"\s+", "", c["quote"])
            if normalized not in seen and _matches(key, c["quote"]):
                hits.append(c)
                seen.add(normalized)
            if len(hits) == 3:
                break
        out[key] = {
            "status": "근거 확인" if hits else "자료 부족",
            "summary": "읽어 온 자료에서 관련 문장을 찾았습니다." if hits else "읽어 온 자료에서 관련 문장을 찾지 못했습니다.",
            "evidence": [{k:c[k] for k in ("doc_id", "chunk", "quote", "kind")} for c in hits],
        }
    return out
