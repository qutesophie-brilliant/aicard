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
    r"(?:서버|클라우드).{0,20}(?:데이터|정보|처리|저장)", re.I)
_OPTOUT = re.compile(r"비활성화|해제|끄(?:기|면|거나|는|고|세요)|끌\s*수|\bOFF\b|일반\s*모드|수동\s*모드", re.I)
_AI_CONTEXT = re.compile(r"\bAI\b|인공지능|학습|생성형|음성|인식|감지|스마트|맞춤|자동|모드", re.I)
_SAFETY = re.compile(r"안전\s*장치|과열|과부하|누전|어린이.{0,8}잠금|차일드\s*락|업데이트|지원\s*기간|자동\s*정지|긴급\s*(?:정지|멈춤)|보안.{0,20}(?:패치|암호화|인증|보호|지원|취약점)|(?:펌웨어|소프트웨어).{0,20}(?:갱신|패치|배포|지원\s*종료)", re.I)
_REMEDY = re.compile(r"고객\s*센터|서비스\s*센터|A\s*/\s*S|보증|환불|반품|보상|교환|수리|피해\s*구제|소비자\s*분쟁", re.I)
_EN_REMEDY = re.compile(r'\bwarrant(?:y|ies)\b|\b(?:customer|technical)\s+(?:service|support)\b|\b(?:refund|return)\s+policy\b', re.I)
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
    from .extract import SCENE_DESCRIPTION, SITE_COMMON
    if SCENE_DESCRIPTION.search(text) or SITE_COMMON.search(text):
        return False
    if key == "data":
        return bool(_DATA_SUBJECT.search(text))
    if key == "optout":
        # A refrigerator compartment's power switch does not show that its AI
        # feature can be disabled. Require a named intelligent/automatic function.
        if not re.search(r'\bAI\b|인공지능|학습|생성형|음성|인식|감지|스마트|자동', text, re.I):
            return False
        return bool(_OPTOUT.search(text) and _AI_CONTEXT.search(text))
    if key == "safety":
        return bool(_SAFETY.search(text))
    if key == "remedy":
        return bool(_REMEDY.search(text) or _EN_REMEDY.search(text))
    # Stock, delivery and return restrictions do not describe AI operation.
    if re.search(r'재고|생산월|매장|반품|교환|철거|회수|추가\s*비용|살균\s*효과', text):
        return False
    from .condition_type import condition_type
    explicit_trial=re.search(r'^(?:[\s*※-])*(?:시험|실험|테스트)\s*(?:부하|조건|환경|방법|모델)\s*[:：]',text)
    kind=condition_type(text)
    if kind == 'unspecified' and not re.search(r'경우|따라|필요|권장|제한|이상|이하|미만|초과|에서만|려면|않|못|전용|연결해야|지원하는|저하|느려|어려|달라', text):
        return False
    return not explicit_trial and kind != 'test' and bool(_AI_CONTEXT.search(text)) and (
        kind=='operating' or len(set(coverage._KEYWORDS[key].findall(text))) >= 2)


def fill(index, product_name=''):
    out = {}
    for key, query in coverage.QUERIES.items():
        candidates = index.search(query, k=30, prefer_kinds=("설명서", "사양표", "공공DB"))
        if key == 'remedy':
            # Korean BM25 queries cannot retrieve an English warranty row.
            candidates += [c for c in index.condition_context() if _EN_REMEDY.search(c['quote'])]
        if key=='conditions':
            # Numerical limits may share no words with the retrieval query.
            # Use the same condition/topic checks on every available sentence.
            candidates += index.condition_context()
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
