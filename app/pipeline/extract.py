"""AI 표시 문구(주장) 추출. 판매페이지·사양표에서 'AI' 관련 표현이 들어간 문장을 뽑는다."""
import json
import re
from typing import Any

from . import llm
from .. import config
from ..schema import ITEM_KEYS

AI_PATTERN = re.compile(
    r"(AI|A\.I\.|인공지능|딥러닝|머신러닝|신경망|학습(?:해|하여|한|합니다|된)|스스로|자동(?:으로)?\s*(?:인식|판단|감지|학습|조절|분석)|"
    r"인식(?:해|하여|합니다|하고)|감지(?:해|하여|합니다)|분석(?:해|하여|합니다)|맞춤|추천|음성\s*(?:인식|명령)|비전|"
    r"알고리즘|스마트\s*(?:감지|인식|진단|케어)|LiDAR|라이다|3D\s*센서|ToF|Gemini|GPT|LLM|생성형)",
    re.I)

PROMPT = """아래는 한 제품의 판매페이지 문장들입니다. 각 문장 앞의 [번호]는 청크 번호입니다.

이 가운데 소비자에게 'AI 기능'을 표시·광고하는 문장을 **빠짐없이** 고르세요. 이 제품은 'AI'를 내세우는 제품이므로, 기기가 무엇을 인식·감지·판단·분석·학습한다고 말하는 문장은 모두 표시 문구입니다.

고르는 문장
- 'AI', '인공지능', '학습', '딥러닝', '스스로', '알아서', '지능형'이 기능에 붙은 문장
- 기기가 사물·공간·바닥·오염·장애물·상황 등을 인식·감지·판단·분석·구분·학습한다고 말하는 문장. 센서·카메라·LiDAR 같은 부품 이름이 함께 적혀 있어도 고릅니다(예: "8종의 센서가 장애물과 높낮이를 인식해", "dToF LiDAR로 집 구조를 인식합니다").
- 제품 전체를 '인공지능 제품'이라고 부르는 광고 문장(예: "차원이 다른 인공지능 물걸레 로봇청소기")
- 제목과 설명이 나뉘어 있으면 설명이 담긴 완전한 문장을 고릅니다. 제목만 'AI'를 말하고 설명이 없으면 제목을 고릅니다.

고르지 않는 문장
- 모델명·브랜드명·플랫폼명만 있는 줄(예: "Bespoke AI 스팀 울트라", "SmartThings 연동")
- 부품 사양만 나열하고 무엇을 인식·판단하는지 말하지 않는 줄(예: "LiDAR 센서 탑재", "듀얼 카메라")
- 음성·앱·리모컨·원격 조작, 예약, 자동 비움·자동 세척·자동 충전·자동 급배수처럼 정해진 동작을 수행하는 기능
- 보안 기술, 인증 사실(KC, AI+ 인증 등), 수상 이력, 가격·배송·구성품·크기·무게·색상 안내
- 다른 문장의 일부만 잘린 조각. 같은 내용을 되풀이한 문장은 대표 문장 하나만 고릅니다.

각 문장을 7항목 중 하나로 분류합니다: {items}
  (functions: AI 기능·목적 / conditions: 작동 조건·한계 / data: 데이터 수집·처리·학습 /
   optout: 기능 해제·대체모드 / safety: 안전장치·업데이트·지원 / remedy: 문의·환불·보상 / evidence: 근거 자체를 제시하는 문장)
최대 {max_claims}개. 해당하는 문장이 없으면 빈 배열을 돌려주세요.

출력(JSON 배열): [{{"chunk": 번호, "text": "표시 문구 문장 그대로", "item": "functions", "label": "6자 이내 짧은 기능명"}}]

문장들:
{body}
"""

_SENT = re.compile(r"(?<=[\.\!\?。])\s+(?=\S)")


def _one_sentence(text: str) -> str:
    """여러 문장이 붙어 온 문구는 'AI' 표지가 든 문장 하나로 줄인다(카드에 보이는 문구는 한 문장이어야 읽힌다)."""
    parts = [p.strip() for p in _SENT.split(text.strip()) if p.strip()]
    if len(parts) <= 1:
        return text.strip()[:160]
    scored = sorted(parts, key=lambda p: (bool(CORE_AI.search(p)), bool(STRONG.search(p)), len(p)), reverse=True)
    best = scored[0]
    if len(re.sub(r"\W", "", best)) < 12:  # 너무 짧으면 바로 뒤 문장과 함께 둔다
        i = parts.index(best)
        best = " ".join(parts[i:i + 2])
    return best[:160]


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{c['chunk']}] {c['quote']}" for c in chunks)


def _quoted_in(text: str, source: str) -> bool:
    """Allow layout whitespace, but never an invented suffix or changed number."""
    text = re.sub(r"\s+", "", text)
    return bool(text) and text in re.sub(r"\s+", "", source)


def extract_claims(source_chunks: list[dict[str, Any]], max_claims: int = 14) -> list[dict[str, Any]]:
    """source_chunks: [{doc_id, chunk, quote, kind}] 판매페이지 계열만."""
    if not source_chunks:
        return []
    if llm.available():
        # 후보를 먼저 규칙으로 좁혀 토큰을 아낀다(AI 표현이 전혀 없는 문장은 뺌)
        cand = [c for c in source_chunks if AI_PATTERN.search(c["quote"]) or CORE_AI.search(c["quote"])] or source_chunks
        cand = cand[:160]
        # 3차 개선: 같은 프롬프트를 EXTRACT_PASSES번 불러 합친다(되풀이는 _postfilter가 거른다). 한 번이라도 성공하면 그 결과를 쓴다.
        data: list[Any] = []
        ok = 0
        for _ in range(config.EXTRACT_PASSES):
            try:
                part = llm.ask_json(PROMPT.format(items=", ".join(ITEM_KEYS), max_claims=max_claims,
                                                  body=_format_chunks(cand)))
            except llm.LLMError:
                continue
            if isinstance(part, list):
                data.extend(part)
                ok += 1
        if not ok:
            return _rules_extract(source_chunks, max_claims)
        by_idx = {(c["doc_id"], c["chunk"]): c for c in source_chunks}
        # 청크 번호가 문서마다 겹칠 수 있어, 번호→실제 청크 매핑은 후보 목록 순서로 푼다
        idx_map: dict[int, dict[str, Any]] = {}
        for c in cand:
            idx_map.setdefault(c["chunk"], c)
        out = []
        for i, row in enumerate(data if isinstance(data, list) else [], 1):
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                continue
            try:
                src = idx_map.get(int(row.get("chunk", -1)))
            except (TypeError, ValueError):
                src = None
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            text = _one_sentence(text)
            if src is None or not _quoted_in(text, src["quote"]):
                src = next((c for c in source_chunks if _quoted_in(text, c["quote"])), None)
            if src is None:  # 원문에 없는 문장(번역·요약)은 인용할 수 없으므로 뺀다
                continue
            item = row.get("item") if row.get("item") in ITEM_KEYS else "functions"
            out.append({"text": text, "item": item,
                        "label": str(row.get("label", ""))[:12],
                        "source": {"doc_id": src["doc_id"], "chunk": src["chunk"], "quote": src["quote"]}})
        product_has_ai = any(CORE_AI.search(c["quote"]) for c in source_chunks)
        out = _postfilter(out, max_claims, product_has_ai)
        out = _must_include(out, cand, max_claims)
        if config.EXTRACT_VERIFY and len(out) >= 2:
            out = _verify(out)
        return out
    return _rules_extract(source_chunks, max_claims)


VERIFY_PROMPT = """아래는 한 제품의 판매페이지에서 'AI 표시 문구'로 뽑힌 문장들입니다. 각 문장이 정말로 '이 기능이 AI(인공지능·학습·스스로 인식·판단)다'라고 소비자에게 말하는 문장인지 한 번 더 검토하세요.

빼야 하는 문장: 제품명·브랜드·플랫폼 이름만 있는 문장, 센서·부품 사양만 적은 문장(인식·판단을 AI로 설명하지 않음), 음성·앱·원격 제어나 자동 비움·세척·충전처럼 정해진 동작의 자동화, 보안 기술·인증·수상, '스마트·맞춤·추천'이라는 말만 있는 문장, 다른 문장의 조각.
남겨야 하는 문장: 'AI·인공지능·학습·딥러닝'을 기능에 붙인 문장, 또는 기기가 스스로 인식·판단·분석·학습한다고 말하는 문장(센서 이름이 함께 있어도 '인식·판단'을 말하면 남깁니다). 제품 전체를 '인공지능 제품'이라고 부르는 광고 문장(예: "차원이 다른 인공지능 물걸레 로봇청소기")도 표시 문구이므로 남깁니다. 다만 모델명·브랜드명 자체(예: "Bespoke AI 스팀 울트라")만 있는 줄은 뺍니다.

{body}

출력(JSON 객체): {{"drop": ["c3", "c7"], "reason": {{"c3": "제품명만 있음"}}}}  빼야 할 것이 없으면 {{"drop": []}}
"""


def _verify(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """2차 개선: 추출 결과를 한 번 더 걸러 정밀도를 올린다(호출 1회). 실패하면 그대로 둔다."""
    body = "\n".join(f"{c['id']}: {c['text']}" for c in claims)
    try:
        data = llm.ask_json(VERIFY_PROMPT.format(body=body), max_tokens=800)
    except llm.LLMError:
        return claims
    drop_values = data.get("drop") if isinstance(data, dict) else None
    drop = {value for value in drop_values if isinstance(value, str)} if isinstance(drop_values, list) else set()
    kept = [c for c in claims if c["id"] not in drop]
    if len(kept) < max(1, len(claims) // 2):  # 절반 넘게 지우자고 하면 믿지 않는다
        return claims
    for i, c in enumerate(kept, 1):
        c["id"] = f"c{i}"
    return kept


# 2차 개선: 1차 평가에서 뺀 23건의 유형(조각·중복 9, 센서·수동 기능 7, 제품명·플랫폼 4, 대조군 오탐 2, 인증 1)을
# LLM 응답 뒤에 한 번 더 거른다. 강한 표지가 하나도 없는 문장, 다른 문구의 조각, 되풀이는 뺀다.
STRONG = re.compile(
    r"((?<![A-Za-z])AI(?![A-Za-z])|A\.I\.|인공지능|딥러닝|머신러닝|신경망|학습|스스로|알아서|지능형|알고리즘|비전|"
    r"인식|감지|분석|판단|구분|파악|분류|스캔|맞춤|추천)", re.I)
NAME_ONLY = re.compile(r"^[\w\s·\-\.\(\)\+]*(Bespoke AI|AI 올인원|AI 로보킹|코드제로 AI|홈봇 AI|SmartThings|ThinQ|Mi Home|Ai 로봇)[\w\s·\-\.\(\)\+]*$", re.I)
# 활용형 서술어(명사 "학습·분석"만으로는 문장이 아니다)
AD_HEADLINE = re.compile(r"(청소기|청정기|제습기|가습기|세탁기|건조기|냉장고|에어컨|가전)\s*$")
PREDICATE = re.compile(r"(합니다|습니다|됩니다|해요|어요|이에요|예요|했|한다|된다|하여|하고|해서|하며|되어|되며|돼|"
                       r"(?:인식|감지|분석|판단|학습|구분|조절|제거|측정|청소|회피|예측)(?:해|하고|하여|하는|한|할|합|되|된|돼))")
VERBISH = re.compile(r"(해|하여|합니다|니다|어요|돼|됩니다|해요|인식|감지|활용|사용|학습|분석|판단)")
CERT = re.compile(r"(인증을 받았|인증 획득|수상|어워드|KC\s*인증|AI\+)")


def _bigrams(t: str) -> set[str]:
    t = re.sub(r"\W", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


CORE_AI = re.compile(r"((?<![A-Za-z])AI(?![A-Za-z])|A\.I\.|인공지능|딥러닝|머신러닝|신경망|학습|스스로|알아서|지능형)", re.I)
FOOTNOTE_LINE = re.compile(r"^\s*(?:[-*※†•]|\[[^\]]{1,20}\]\s*-)\s")
TESTNOTE = re.compile(r"(시험\s*(?:내용|방법|조건|모드|기관|일시|대상)\s*[:：]|인증\s*(?:기관|내용)\s*[:：]|별도 구매가 필요|업데이트 후|에서만 지원하며|사용이 가능합니다)")
SITE_COMMON = re.compile(r"(AI로 연결됩니다|AI Home\b|AI 리뷰 요약|AI Chat|AI 챗에게|질문해 보세요|고객지원센터|위클리리포트|세탁기 추천|냉장고 추천|제품 추천|"
                         r"장면입니다|시각화 장면|이미지는 .{0,30}표현한|연출된 (?:영상|이미지|화면)|평이 (?:많아요|있어요)|평가가 많|후기가 많|리뷰가 많|"
                         r"구독하면|케어십|가전 구독|상담 예약|매장 찾기)")
FOOTNOTE_INLINE = re.compile(r"^\s*\[[^\]]{1,30}\]\s*[\*※]")
COND_ONLY = re.compile(r"^(?:사용\s?환경|제품|모델|설치\s?환경|주변\s?환경)[^.]{0,30}(?:따라|의해)[^.]{0,30}(?:차이가|다를 수|달라질 수)")
SCENE_DESCRIPTION = re.compile(r'(?:이미지|화면)(?:의)?\s*(?:왼쪽|오른쪽|상단|하단|중앙)|(?:텍스트|문구)(?:가|와 함께).{0,25}(?:적혀|표시되어|이미지)|이미지.{0,15}(?:보입니다|있습니다)')
FRAG_START = re.compile(r"^\S{1,8}(니다|습니다|어요|세요|죠)\.\s")  # "작습니다. 고정밀 …"처럼 앞 문장 꼬리가 붙은 조각


def _postfilter(rows: list[dict[str, Any]], max_claims: int, product_has_ai: bool = True, *, allow_spaced_ai: bool = False, exact_duplicates: bool = False) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for r in rows:
        t = r["text"]
        if len(re.sub(r"\W", "", t)) < 8 or FRAG_START.match(t):
            continue  # 조각
        # 5차 개선: 각주·시험 안내 줄("- 시험 내용 : …", "※ … 지원합니다")은 소비자에게 보여 주는 표시 문구가 아니라 근거 자료다.
        if FOOTNOTE_LINE.match(t) or TESTNOTE.search(t) or re.search(r'^[^*※]{0,30}세요[.!?]\s*[*※]', t):
            continue
        # 15회차(LG 개발 세트): 사이트 공통 요소는 그 제품의 표시 문구가 아니다 — 플랫폼 배너("당신의 집, 이제 AI로 연결됩니다"),
        # AI 리뷰 요약·AI 챗 안내, 추천 위젯, 이미지 대체 설명("…개념적으로 표현한 시각화 장면입니다"), 후기 요약("…평이 많아요"),
        # 조건 안내만 있는 줄("사용환경에 따라 동작 조건은 차이가 있을 수 있습니다"), 각주가 본문에 섞여 들어온 줄("[인공지능 DD] * …")
        # (각주가 본문에 섞인 "[AI 절약모드] * …" 줄은 4차 라벨러가 3:4로 갈려 그대로 둔다)
        if SITE_COMMON.search(t) or COND_ONLY.search(t) or SCENE_DESCRIPTION.search(t):
            continue
        # 5차 개선: 서술어 없이 기능 이름만 이어 붙인 제목 줄("물걸레 온수 세척 기능 인공지능 관리 재세척")은 표시 문구로 보지 않는다
        ai_text = re.sub(r'인공\s+지능', '인공지능', t) if allow_spaced_ai else t
        if not STRONG.search(ai_text):
            continue  # '스마트', '음성 제어', '추천'만 있는 문장
        if not product_has_ai and not CORE_AI.search(ai_text):
            continue  # 3차 개선: 페이지 어디에도 'AI·학습·스스로'가 없는 제품(대조군)에서는 센서 인식 문장을 AI 표시로 보지 않는다
        if CERT.search(t) or (NAME_ONLY.match(t) and not VERBISH.search(t)):
            continue  # 제품명·플랫폼 이름만 있는 문장
        b = _bigrams(t)
        dup = False
        for k in kept:
            if exact_duplicates:
                # Preview recall: shared wording does not make two different
                # functions or numeric conditions the same quotation.
                if re.sub(r'\s+', '', t) == re.sub(r'\s+', '', k['text']):
                    dup = True
                    break
                continue
            kb = _bigrams(k["text"])
            if b and kb and len(b & kb) / min(len(b), len(kb)) >= 0.6:
                # 되풀이·조각: 더 긴(설명이 담긴) 쪽을 남긴다
                if len(t) > len(k["text"]):
                    k.update(text=t, item=r["item"], label=r["label"], source=r["source"])
                dup = True
                break
        if not dup:
            kept.append(r)
    for i, r in enumerate(kept, 1):
        r["id"] = f"c{i}"
    return kept[:max_claims]


# 3차 개선: 2차 측정에서 LLM이 빠뜨린 표시 문구 12건 중 'AI·인공지능·학습·스스로'가 기능에 붙은 완전한 문장이 4건이었다.
# 이런 문장은 LLM이 고르지 않았어도 규칙으로 되살린다(플랫폼 광고·보안·인증·조각은 제외).
MUST = re.compile(r"((?<![A-Za-z])AI(?![A-Za-z])|A\.I\.|인공지능|딥러닝|머신러닝|신경망|학습(?:해|하여|한|합니다|된|하고)|스스로 (?:인식|감지|판단|분석|학습|구분|파악))", re.I)
MUST_SKIP = re.compile(r"(AI로 연결|ThinQ|SmartThings|Mi Home|앱에서|보안|Knox|암호화|인증|수상|어워드|빅스비는|Bixby는|Alexa는|구글 어시스턴트는|"
                       r"케어하니까|자동 비움|자동 세척|자동 충전|급배수|가격|배송|구성품|무게|색상|스테이션)", re.I)
SENTENCE_END = re.compile(r"(다|요|죠|니다|습니다|세요|해|하여|하고|해서|며|고|해요)[\.\!\)\*]*$")


def _must_include(kept: list[dict[str, Any]], cand: list[dict[str, Any]], max_claims: int) -> list[dict[str, Any]]:
    if len(kept) >= max_claims:
        return kept
    def _core(t: str) -> set[str]:  # 제품명(로마자)은 빼고 비교해야 같은 제품명을 단 다른 문장이 되풀이로 묶이지 않는다
        return _bigrams(re.sub(r"[A-Za-z][A-Za-z\.\-]*", " ", t)) or _bigrams(t)
    have = [_core(k["text"]) for k in kept]
    added = 0
    for c in cand:
        q = c["quote"].strip()
        if re.match(r"^[※\*†①-⑳]|^\d{1,2}\)", q) or re.search(r"(동일합니다|제외한|상위 모델|모델에 한해)", q):
            continue  # 각주·비교 안내는 근거 후보지 표시 문구가 아니다
        if not MUST.search(q) or MUST_SKIP.search(q) or CERT.search(q) or SITE_COMMON.search(q) or COND_ONLY.search(q):
            continue  # 15회차: 되살리기 경로도 사이트 공통 줄·이미지 설명·후기 요약은 건너뛴다
        if not VERBISH.search(q) or re.search(r"(경우|못하거나|않을 수|어려울 수|주의|권장)", q):
            continue  # 동사 없는 이름 줄, 조건·주의 문장
        if len(re.sub(r"\W", "", q)) < 14 or q.endswith(("/", "(", ",", "·")) or not SENTENCE_END.search(q):
            continue  # 조각·제목만 있는 줄
        if not re.search(r"(인식|감지|판단|분석|학습|구분|파악|분류|스캔|회피|탐지|식별|예측|조절|활용)", q):
            continue  # 기능을 말하지 않는 문장
        b = _core(q)
        if any(len(b & h) / min(len(b), len(h)) >= 0.5 for h in have if h):
            continue  # 이미 고른 문구와 같은 내용
        kept.append({"text": q[:160], "item": "functions", "label": "", "rule_added": True,
                     "source": {"doc_id": c["doc_id"], "chunk": c["chunk"], "quote": c["quote"]}})
        have.append(b)
        added += 1
        if len(kept) >= max_claims:
            break
    for i, r in enumerate(kept, 1):
        r["id"] = f"c{i}"
    return kept


# 즉석 확인(규칙만)에서 표시 문구로 보지 않는 줄: 상품평·기획전·구매 안내·화면 조작어. 북마클릿이 페이지 전체 글자를 보내므로 여기서 거른다.
_NOISE_LINE = re.compile(r"(추천\s*상품|추천\s*제품|AI\s*가격비교|유사상품을 자동으로 모아|상품평|리뷰|후기|별점|평점|기획전|추천 ?드립|추천합니다|구매하|구매해|장바구니|할인|쿠폰|카드 ?혜택|배송|설치 ?가이드|설치 ?안내|"
                         r"^#|^Previous|^Next|^다음$|^이전$|기대해 ?봅니다|생각이|같아요|좋아요|써보시|만족|후회|강력|고민|신혼|선물|\d+(?:년형|세대)|"
                         r"소모품|액세서리|함께 보면|관련 상품|비교하기|사양 보기|자세히|더 ?보기|닫기|목차|바로가기)")
_FUNC_CUE = re.compile(r"(코스|모드|인식|감지|분석|세탁|건조|절약|절전|자동|조절|최적|맞춤|학습|청소|제어|알림|탐지|판단|계산|스스로|알아서|"
                       r"제안|추천해|관리|보정|측정|예측|유지|조정|화질|음질|사운드|화면|냉장|냉동|보관|흡입|세척|가열|온도|습도|오염|먼지|냄새|"
                       r"음성|비서|에이전트|경로|지도|장애물|사물|정온|절감|소음)")


def _rules_extract(source_chunks: list[dict[str, Any]], max_claims: int, *, complete_quotes: bool = False) -> list[dict[str, Any]]:
    out = []
    seen: set[str] = set()
    for c in source_chunks:
        q = c["quote"]
        ai_match = AI_PATTERN.search(q)
        if complete_quotes and not ai_match:
            ai_match = CORE_AI.search(q) or re.search(r'인공\s+지능', q)
        if not ai_match:
            continue
        if complete_quotes and re.search(r'^\s*(?:learn|read|find\s+out)\s+more\b', q, re.I):
            continue  # Navigation copy does not describe this product's AI.
        # Quick previews also receive English copy on Korean manufacturer sites.
        # Keep exact source wording, without translating or changing formal rules.
        english_function = complete_quotes and bool(re.search(
            r'\b(?:detect\w*|distinguish\w*|classif\w*|recogniz\w*|recognis\w*|recognition|clean\w*|mop\w*|'
            r'navigat\w*|control\w*|track\w*|adjust\w*|learn\w*|optimiz\w*|'
            r'optimis\w*|voice|assistant|image|sound|temperature|noise|microphones?|calls?)\b', q, re.I))
        explicit_ai_basis = complete_quotes and bool(re.search(
            r'인공\s*지능.{0,12}기반|(?<![A-Za-z])AI(?:로|를\s*통해)\s*작동', q, re.I))
        image_enhancement = complete_quotes and bool(re.search(r'(?<![A-Za-z])AI.{0,20}(?:이미지|영상|사진).{0,12}(?:강화|개선|향상)', q, re.I))
        assistant_function = complete_quotes and '어시스턴트' in q
        if _NOISE_LINE.search(q) or not (_FUNC_CUE.search(q) or english_function or explicit_ai_basis or image_enhancement or assistant_function) or len(q) > (500 if complete_quotes else 200):
            continue
        if english_function and re.search(r'^\s*(?:[*†※]|note\s*:|test\s+conditions\s*:)|\b(?:customer review|add to cart|shipping|discount|coupon)\b', q, re.I):
            continue
        if complete_quotes and (not CORE_AI.search(re.sub(r'인공\s+지능', '인공지능', q)) or FOOTNOTE_INLINE.match(q)):
            continue  # Quick-check counts explicit AI wording, not every nearby automatic feature.
        key = re.sub(r'\s+', '', q) if complete_quotes else re.sub(r"\W", "", q)[:30]
        if key in seen:
            continue
        seen.add(key)
        item = "functions"
        if re.search(r"(조건|환경|경우|제한|한계|않을 수|어려울 수|권장)", q):
            item = "conditions"
        elif re.search(r"(데이터|정보|수집|서버|클라우드|학습에|개인정보|저장)", q):
            item = "data"
        elif re.search(r"(해제|끄|OFF|비활성|수동 모드|대체)", q):
            item = "optout"
        elif re.search(r"(업데이트|지원 기간|보안|안전장치|오작동|펌웨어)", q):
            item = "safety"
        elif re.search(r"(환불|보상|문의|고객센터|A/S|AS|피해)", q):
            item = "remedy"
        label = ai_match.group(0)[:8]
        out.append({"text": q if complete_quotes else q[:160], "item": item, "label": label,
                    "source": {"doc_id": c["doc_id"], "chunk": c["chunk"], "quote": q}})
        if len(out) >= max_claims * 3:
            break
    return _postfilter(out, max_claims, allow_spaced_ai=complete_quotes, exact_duplicates=complete_quotes)
