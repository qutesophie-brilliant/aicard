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
_SAFETY = re.compile(r"보안|안전\s*장치|과열|과부하|누전|어린이.{0,8}잠금|차일드\s*락|펌웨어|소프트웨어|업데이트|지원\s*기간|자동\s*정지|긴급\s*(?:정지|멈춤)", re.I)
_REMEDY = re.compile(r"고객\s*센터|서비스\s*센터|A\s*/\s*S|보증|환불|반품|보상|교환|수리|피해\s*구제|소비자\s*분쟁", re.I)


def _matches(key: str, text: str) -> bool:
    if key == "data":
        return bool(_DATA_SUBJECT.search(text))
    if key == "optout":
        return bool(_OPTOUT.search(text) and _AI_CONTEXT.search(text))
    if key == "safety":
        return bool(_SAFETY.search(text))
    if key == "remedy":
        return bool(_REMEDY.search(text))
    return len(coverage._KEYWORDS[key].findall(text)) >= 2


def fill(index):
    out = {}
    for key, query in coverage.QUERIES.items():
        candidates = index.search(query, k=30, prefer_kinds=("설명서", "사양표", "공공DB"))
        hits = []
        seen = set()
        for c in candidates:
            normalized = re.sub(r"\s+", "", c["quote"])
            if normalized not in seen and _matches(key, c["quote"]):
                hits.append(c)
                seen.add(normalized)
            if len(hits) == 3:
                break
        out[key] = {
            "status": "근거 확인" if hits else "자료 부족",
            "summary": "이 페이지에 관련 문장이 있어요." if hits else "이 페이지에서는 관련 문장을 찾지 못했어요.",
            "evidence": [{k:c[k] for k in ("doc_id", "chunk", "quote", "kind")} for c in hits],
        }
    return out
