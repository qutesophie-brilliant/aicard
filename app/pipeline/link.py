"""주장 ↔ 근거 연결과 4단계 확인상태 판정.

LLM 호출은 주장 전체를 한 번에 묶어서 보낸다(카드 한 장당 1회). 무료 등급 한도(하루 요청 수)를
아끼고 속도를 높이기 위해서다. 묶음 호출이 실패하면 주장별 규칙 판정으로 대신한다.
"""
import json
import copy
import re
from typing import Any

from . import llm
from .retrieve import ChunkIndex
from ..config import STATUSES
from ..schema import axes_from_status, status_from

BATCH_PROMPT = """제품 판매페이지의 AI 표시 문구(주장)들과, 각 주장마다 각주·사양표·설명서·공식 자료에서 찾은 후보 문장들입니다.

각 주장에 대해 다음을 판정하세요.
- status: 다음 네 가지 중 하나
  * "근거 확인": 주장을 뒷받침하는 구체적 설명(작동 방식·사용 센서나 카메라·측정 방법·시험 기관·수치)이 후보에 있음
  * "조건 확인": 위 설명이 있고, 적용 조건이나 한계(환경·크기·조도·바닥 재질·지원 모델·사전 설정·인식 실패 사례·앱 설정 필요)도 후보에 적혀 있음.
    각주(※, *, 1) 같은 표시로 시작하는 문장)에 그 기능의 작동 방식과 조건이 함께 적혀 있으면 근거이자 조건입니다.
    각주가 시험 기준·실험치·"환경에 따라 다를 수 있음" 같은 조건·한계만 적고 작동 방식이 없으면 근거가 아니므로 "자료 부족"입니다.
    다만 시험 각주가 그 기능의 이름을 들어 무엇을 시험했는지 적었으면(예: "시험 방법: AI 절약모드 켜짐 유/무에 따른 소비전력량 비교",
    "에너지 절약 20% 시험 조건: AI 맞춤건조+건조 정도 3") 그 기능을 다룬 시험 기록이므로 근거이자 조건("조건 확인")입니다.
  * "자료 부족": 후보가 주장을 되풀이하거나 광고 문구일 뿐, 작동 방식이나 조건이 없음
  * "추가 확인 필요": 주장과 후보가 서로 어긋남. 다음 중 하나라도 해당하면 반드시 이 값으로 둡니다.
    - 주장이 말하는 대상의 범위와 후보의 정의가 다름(예: 주장은 "액체와 고체", 각주는 "액체가 아닌 고체")
    - 주장은 자동·AI라고 하는데 후보는 그 인식·판단 자체를 사용자가 직접(수동으로) 해야 한다고 적음.
      기능을 앱에서 켜 두어야 한다거나 구역·금지 영역·사용자 맞춤 항목을 앱에서 추가할 수 있다는 안내는 어긋남이 아니라 조건입니다.
      어긋남은 그 기능을 말하는 문장에서만 판단합니다. 다른 기능의 수동 설정 안내를 끌어다 어긋남으로 보지 않습니다.
    - 후보가 그 기능을 "업데이트로 제공 예정"이라고 적어 구매 시점에는 없음
    - 수치·개수·시간이 서로 다름, 또는 다른 모델(상위 모델)의 설명임을 각주가 밝힘
- evidence: 근거로 쓴 후보 번호 목록(최대 3개). '자료 부족'이면 빈 배열. '추가 확인 필요'는 어긋난 후보 번호를 넣습니다.
- conditions: 소비자가 읽을 조건·한계 한 문장. '추가 확인 필요'면 무엇이 어떻게 어긋나는지 한 문장. 없으면 "".
- basis: 회사 설명이 기능을 무엇으로 설명하는지. "learn"(학습·모델·데이터·알고리즘 언급) / "vision"(카메라·영상 인식, 학습 언급 없음) /
  "sensor"(거리·초음파·적외선·LiDAR·자이로 같은 센서나 규칙) / "service"(빅스비·구글 어시스턴트·알렉사 같은 음성비서나 클라우드 AI,
  제3자 플랫폼의 이름을 들어 설명하고 기기 안의 작동 방식은 적지 않음) / "none"(쓰지 않음). '자료 부족'이면 "".
- note: 판정 이유 한 문장(검토자용). 우열·위법 표현 금지.

주의
- 주장과 같은 말을 되풀이한 문장(같은 광고 문구)은 근거가 아닙니다.
- 후보 문장이 주장의 기능과 관계없는 다른 기능을 설명하면 근거로 쓰지 않습니다. 기능 이름이 다르면 다른 기능입니다.
- 각주·사양표의 짧은 문장이라도 센서 종류, 인식 대상 개수, 조건을 적고 있으면 근거입니다. 길이로 판단하지 않습니다.
- 판매 본문 안의 작동 설명도 근거입니다. 주장이 제목처럼 짧으면 '문구 바로 옆 문장'이 그 기능의 설명인지 보세요.
- 판매 본문은 이미지에서 읽어 줄 단위로 잘려 있을 때가 많습니다. 주장이 "…온습도를 분석해"처럼 끝맺음 없이 잘렸으면
  "판매페이지(앞뒤 이어 읽음)" 후보나 '문구 바로 옆 문장'이 그 문장의 나머지입니다. 이어 읽어서 입력→동작이 드러나면
  (예: "사용 시간과 날씨, 실내외 온습도를 분석해 + 환기가 필요한 시점이 되면 음성으로 환기 알림", "상태를 진단하고 + 냉매량부터 모터와 센서까지",
  "사용 빈도를 파악 + 사용하지 않는 시간에는 절전모드로 에너지 사용량 최소화") 그 후보를 근거로 인정합니다.
  주장 문장 자체가 사용 센서·방식·수치를 말하거나, 무엇을 감지·입력으로 받아 무엇을 하는지("레이더가 움직임을 감지해 앱으로 알림 발송",
  "분위기를 요청하면 90가지 색상 중 골라 연출", "무게, 종류, 오염도에 맞는 코스로 세탁"), 점검 대상 목록("냉매량부터 모터와 센서까지 진단")을 적고 있으면
  ("판매페이지(문구 자체)" 후보) 그 문장을 근거로 인정합니다. 단, 'AI'라는 말만 있고 방식이 없으면 근거가 아닙니다.
- 후보에 "수동", "직접 설정", "제공될 예정", "업데이트를 통해" 같은 말이 있으면 주장과 어긋나는지 반드시 확인하세요.
- 주장이 '학습한다·스스로 배운다'고 말하면, 무엇을 학습하는지(이미지·사물 종류·데이터, 또는 사용자가 설정한 온도·사용 패턴·생활 패턴 피드백·사용 공간)와
  그 결과 무엇이 바뀌는지를 적은 문장이 있어야 근거입니다. 학습 데이터의 규모나 모델·알고리즘 자체의 공개까지 요구하지 않습니다.
  센서나 카메라 이름만으로는 학습의 근거가 되지 않습니다.
- 후보가 "~모델에 한해 지원", "~모델과 동일" 같은 지원 범위 안내만 하고 작동 방식을 말하지 않으면 그것만으로는 근거가 아닙니다('자료 부족').
- 후보가 주장과 같은 기능을 다른 말로 다시 적었을 뿐 센서·방식·수치·조건이 없으면 '자료 부족'입니다.
- 근거는 무엇으로 아는지(센서·카메라·측정 방법·학습 데이터) 또는 무엇을 알아보는지(인식 대상의 종류·목록·수치, 예: "먼지가 많으면 흡입력을 높이고 젖은 얼룩·액체가 감지되면 롤러를 들어 올림")를 구체적으로 적은 문장입니다.
- 에어컨·세탁기처럼 제어하는 기능은 무엇을 입력으로 쓰고(설정 온도·사용 패턴·실외 부하·습도·운전 시간·무게·옷감·오염도·위치 정보·Wi-Fi 신호)
  무엇을 어떻게 바꾸는지(기류·온습도·건조 시간·세탁 코스·모션·압축기 출력·전력)를 적은 문장이 작동 방식입니다.
  예: "설정 온도에 가까워지면 소프트바람으로 전환", "운전 시간에 따라 내부 건조 시간을 10~45분으로 자동 설정", "사용 패턴을 분석해 희망 온·습도를 자동 설정".
- 상태 진단 기능은 무엇을 점검하는지(점검 대상 목록)와 그 결과를 어떻게 알려 주는지가 적혀 있으면 작동 방식입니다. 판단 알고리즘 공개까지 요구하지 않습니다.
- 주장이 그 제품의 AI 기능을 묶어 부르는 표제("진정한 AI 세탁의 완성", "세탁의 시작부터 끝까지 AI로")이면, 같은 페이지에서 그 제품의 AI 기능
  작동 방식을 적은 후보를 근거로 씁니다. 다만 결과 수치만 있는 문장("최대 60% 절약", "에너지 사용량 절감")은 방식이 아니므로 근거가 아닙니다.
  주장의 기능 이름만 되풀이하고 결과 동작만 적은 문장(예: 주장 "반려동물 자동 인식" ↔ 후보 "반려동물에게 다가가면 브러시를 멈춥니다")은 근거가 아닙니다.
- "문구에 붙은 번호 각주"는 그 문구에 직접 달린 각주입니다. 거기 적힌 시험 기준("내부 테스트 기준", "실험실 데이터")이나 한계("환경에 따라 달라질 수 있음", "수동으로 활성화")는 그 기능의 조건으로 봅니다.
- "~할 경우 ~합니다"처럼 상황별 동작을 설명하는 문장은 작동 방식이지 조건·한계가 아닙니다. 조건은 적용 범위를 좁히거나 실패 가능성을 밝히는 말입니다.
- 같은 기능의 수치(최소 감지 크기, 인식 개수, 감지 항목 수)가 후보끼리 다르면 '추가 확인 필요'로 둡니다.
  세탁과 건조, 냉장과 냉동처럼 공정이 다르면 다른 기능입니다(세탁 "5종 옷감 감지"와 건조 "4종 옷감 감지"는 어긋남이 아닙니다).
- 시험기관·인증 문장(예: "KSA AI+ 인증을 받은 제품입니다")은 그 시험이 무엇을 어떻게 쟀는지 적혀 있을 때만 근거입니다. 기능 이름 아래 인증 사실만 적힌 각주는 제품 인증이지 그 기능의 작동 설명이 아니므로 근거가 아닙니다(4차 라벨러 3인 판단, 2026-10-13 수정).

{blocks}

출력(JSON 객체, 주장 id를 키로): {{"c1": {{"status":"...", "evidence":[0,2], "conditions":"...", "basis":"sensor", "note":"..."}}, "c2": {{...}}}}
"""

def _join_fragment(index: ChunkIndex, src: dict[str, Any], max_lines: int = 3) -> dict[str, Any] | None:
    """문구 줄이 끝맺음 없이 잘려 있으면 같은 문서의 다음 줄을 문장이 끝날 때까지 이어 붙인다(최대 max_lines줄).
    문구 줄이 '…하고,'처럼 앞 문장의 뒤쪽 조각이면 앞 줄도 하나 붙인다. 각주·연출 안내 줄에서 멈춘다."""
    doc_id, idx = src.get("doc_id"), src.get("chunk")
    if not isinstance(idx, int):
        return None
    lines = {i: t for d, i, t, k in index.entries if d == doc_id}
    cur = (lines.get(idx) or src.get("quote") or "").strip()
    if not cur:
        return None
    parts = [cur]
    ended = bool(_ENDS.search(cur))
    n = 0
    while not ended and n < max_lines:
        nxt = (lines.get(idx + n + 1) or "").strip()
        if not nxt or _is_footnote(nxt) or _BOILER.search(nxt) or _STEP.match(nxt):
            break
        parts.append(nxt)
        ended = bool(_ENDS.search(nxt))
        n += 1
    if n == 0:
        # 앞 줄이 끝맺음 없이 이 줄로 이어지는 경우("…감지하여," + "시원함을 정확히 전달하고")
        prev = (lines.get(idx - 1) or "").strip()
        if prev and not _ENDS.search(prev) and not _is_footnote(prev) and _CONNECT.search(prev) and re.search(r"[가-힣]", prev):
            parts.insert(0, prev)
        else:
            return None
    return {"doc_id": doc_id, "chunk": idx, "quote": " ".join(parts), "kind": "판매페이지(앞뒤 이어 읽음)", "score": 0.0, "joined": True}


_TESTIMONIAL = re.compile(
    r"강추(?:합니다|해요|드립니다|예요|요)|전속\s*모델|(?:구매|사용)\s*후기|"
    r"(?:고객(?:님)?(?:들)?|여러분).{0,80}(?:관심|성원).{0,80}감사", re.S
)


def link_claims(claims: list[dict[str, Any]], index: ChunkIndex, *, exclude_testimonials: bool = False) -> list[dict[str, Any]]:
    cand_map: dict[str, list[dict[str, Any]]] = {}
    condition_context = index.condition_context()
    # 9회차: 조건·한계 문장은 근거 문장과 떨어져 있거나 후보에 들지 않는 일이 많았다(조건 표시 일치 55%의 주원인).
    # 제품 문서 전체에서 조건·한계를 적은 문장을 모아 두고, 근거가 정해진 뒤 그 근거와 같은 기능의 조건을 붙인다.
    cond_pool = [t for t in index.by_kind(("판매페이지", "사양표", "설명서", "공식 자료"))
                 if _is_cond(t["quote"]) and not (exclude_testimonials and _TESTIMONIAL.search(t["quote"]))]
    for claim in claims:
        src = claim["source"]
        cands = index.search(claim["text"], k=10, exclude=(src["doc_id"], src["chunk"]),
                             prefer_kinds=("설명서", "사양표", "공식 자료", "공공DB"))
        # 같은 판매페이지에서 주장 문구를 그대로 되풀이한 문장은 근거 후보에서 뺀다
        cands = [c for c in cands if not _is_restatement(c, claim["text"])]
        # 2차 개선: 1차 평가에서 놓친 근거 8건 중 다수가 (1) 문구 바로 아래 설명 문장, (2) 문구 자체에 든 작동 방식이었다.
        # 제목형 문구의 앞뒤 문장을 후보에 넣고, 문구 자체가 센서·방식을 말하면 그 문장도 후보로 둔다(라벨 기준: 본문 안 작동 설명은 근거).
        seen = {(c["doc_id"], c["chunk"]) for c in cands}
        # 8차 개선: 범위를 앞뒤 2문장에서 6문장으로 넓힌다. 라벨러 3인은 같은 절 안에 있으면 근거로 묶었는데,
        # 설명 문장이 제목에서 서너 문장 아래 있는 경우가 많아 2문장으로는 닿지 않았다.
        for n in index.neighbors(src["doc_id"], src["chunk"], span=6):
            if (n["doc_id"], n["chunk"]) not in seen and not _is_restatement(n, claim["text"]) \
                    and (_EVID.search(n["quote"]) or _PROCESS.search(n["quote"])):
                cands.append(n)
                seen.add((n["doc_id"], n["chunk"]))
        # 13회차: 시험 세트(새 가전 9개)에서 놓친 18건 중 12건은 OCR 줄 조각 때문이었다. 판매 본문이 줄 단위로 잘려
        # "…온습도를 분석해" / "환기가 필요한 시점이 되면 음성으로 환기 알림을 드립니다"처럼 한 문장이 두세 조각으로 나뉘면,
        # 라벨러 3인은 앞뒤를 이어 읽어 근거로 삼았는데 파이프라인은 끝맺음이 없는 조각을 후보에서 버렸다.
        # 문구가 조각이면 같은 문서의 다음 줄을 문장이 끝날 때까지(최대 3줄) 이어 붙인 '이어 읽은 문장'을 후보로 넣고,
        # 문구 바로 앞뒤 2줄은 끝맺음이 없어도 후보에 둔다(바로 옆 줄임을 표시).
        joined = _join_fragment(index, src)
        if joined and not _is_restatement({"quote": joined["quote"], "kind": "판매페이지"}, claim["text"]) \
                and re.sub(r"\s", "", joined["quote"]) != re.sub(r"\s", "", claim["text"]):
            cands.insert(0, joined)
        for n in index.neighbors(src["doc_id"], src["chunk"], span=3):
            if (n["doc_id"], n["chunk"]) not in seen and not _is_restatement(n, claim["text"]) \
                    and len(re.sub(r"\W", "", n["quote"])) >= 10 and not _BOILER.search(n["quote"]):
                n["adjacent"] = True
                cands.append(n)
                seen.add((n["doc_id"], n["chunk"]))
        # 13회차: 문구와 내용어(무게·재질·오염도·모션·피드백 …)를 두 개 이상 공유하면서 동작을 적은 문장은 문서 어디에 있어도 후보에 넣는다.
        # (예: "무게, 의류재질, 오염도를 감지해 최적의 모션으로 세탁" ↔ 페이지 아래쪽 "부드러운 의류는 … 섬세한 모션을 사용하고")
        cn = _content_nouns(claim["text"])
        if len(cn) >= 2:
            far2 = 0
            for t in index.by_kind(("판매페이지", "사양표", "설명서", "공식 자료")):
                if far2 >= 3 or (t["doc_id"], t["chunk"]) in seen:
                    continue
                q = t["quote"]
                if len(cn & _content_nouns(q)) >= 2 and (_PROC_V.search(q) or _MECH.search(q)) and not _is_restatement(t, claim["text"]) \
                        and not _BOILER.search(q):
                    t.update(score=0.0, near=False, far=True)
                    cands.append(t); seen.add((t["doc_id"], t["chunk"])); far2 += 1
        # 3차 개선: 각주 문장은 BM25 점수가 낮아 후보에서 빠지기 쉬우므로, 문구와 겹치는 각주를 따로 넣는다
        cb = _bigrams(claim["text"])
        for f in index.footnotes():
            if (f["doc_id"], f["chunk"]) in seen or len(cands) >= 16:
                continue
            fb = _bigrams(f["quote"])
            if cb and fb and len(cb & fb) / min(len(cb), len(fb)) >= 0.2:
                cands.append(f)
                seen.add((f["doc_id"], f["chunk"]))
        # 3차 개선: 문구 옆 문장이 센서 이름(LiDAR 등)을 말하면, 그 센서를 설명하는 사양표·각주 문장(과 바로 다음 줄)도 후보에 넣는다.
        # 1차 라벨의 '집안 구조 인식' 근거가 "* dToF … 초당 10번 회전하며 … 스캔합니다" 같은 각주였기 때문이다.
        terms = set()
        for n in index.neighbors(src["doc_id"], src["chunk"], span=2) + [src]:
            terms.update(m.group(0) for m in _MECH_NAME.finditer(n["quote"]))
        if terms:
            for t in index.by_kind(("사양표", "설명서", "공식 자료")):
                if (t["doc_id"], t["chunk"]) in seen or len(cands) >= 18:
                    continue
                if any(term.lower() in t["quote"].lower() for term in terms):
                    t.update(score=0.0, near=False)
                    cands.append(t)
                    seen.add((t["doc_id"], t["chunk"]))
                    nxt = next((x for x in index.neighbors(t["doc_id"], t["chunk"], span=1) if x["chunk"] == t["chunk"] + 1), None)
                    if nxt and (nxt["doc_id"], nxt["chunk"]) not in seen and _is_footnote(nxt["quote"]):
                        nxt["near"] = False
                        cands.append(nxt)
                        seen.add((nxt["doc_id"], nxt["chunk"]))
        # 4차 개선: 문구에 붙은 각주 번호("회피1", "피해갑니다²", "모니터링14", "장애물*")를 따라가 그 번호의 각주와 다음 줄을 후보에 넣는다.
        # 성능 한계("실제 성능은 환경에 따라 달라질 수 있습니다")는 대개 번호 각주에만 적혀 있다.
        for f in _numbered_footnotes(index, claim["text"], src):
            if (f["doc_id"], f["chunk"]) not in seen:
                f["mark"] = True
                cands.insert(0, f)
                seen.add((f["doc_id"], f["chunk"]))
        # 5차 개선: 사양표·설명서의 센서 사양 줄("센서 방식: 3D TOF LiDAR", "PM 1.0 센서")은 BM25 점수가 낮아 빠지기 쉽다.
        # 문구가 인식·감지를 말하면, 문구의 인식 대상과 겹치거나 '센서 방식'을 적은 사양 줄을 3개까지 넣는다.
        if _PERCEIVE.search(claim["text"]) or _PERCEIVE_EN.search(claim["text"]):
            tgt = set(_TARGET.findall(claim["text"]))
            csize = _sizes(claim["text"])
            added = 0
            for t in index.by_kind(("사양표", "설명서", "공식 자료", "판매페이지")):
                if added >= 3 or (t["doc_id"], t["chunk"]) in seen:
                    continue
                q = t["quote"]
                spec_line = bool(re.search(r"센서\s*(?:방식|종류|구성|사양)\s*[:：]", q))
                # 12회차: "1.0㎛ 이하 극초미세먼지 감지" ↔ "PM 1.0 센서"처럼 측정 크기가 같은 센서 줄은 판매 본문이어도 넣는다
                size_hit = bool(csize and (_sizes(q) & csize) and _SENSOR_SPEC.search(q))
                if t["kind"] == "판매페이지" and not (spec_line or size_hit):
                    continue
                if _SENSOR_SPEC.search(q) and (spec_line or size_hit or (set(_TARGET.findall(q)) & tgt)):
                    t.update(score=0.0, near=False, spec=True)
                    cands.append(t); seen.add((t["doc_id"], t["chunk"])); added += 1
        # 5차 개선: 'AI' 기능 자체를 다룬 인증("인증내용 : AI공기질센서기능")은 라벨 기준상 근거다. 'AI' 문구면 그런 인증 줄을 넣는다
        if _GENERIC.search(claim["text"]):
            for t in index.by_kind(("사양표", "설명서", "공식 자료", "판매페이지")):
                if (t["doc_id"], t["chunk"]) in seen:
                    continue
                if _CERT.search(t["quote"]):
                    t.update(score=0.0, near=False)
                    cands.append(t); seen.add((t["doc_id"], t["chunk"]))
        # 5차 개선: 'STEP 2' 같은 단계 설명은 앞뒤 단계와 붙여야 작동 방식(STEP 1 '센서로 분석')이 드러난다
        merged, keep = set(), []
        for c in cands:
            if _STEP.match(c["quote"].strip()):
                group = [n for n in index.neighbors(c["doc_id"], c["chunk"], span=2) if _STEP.match(n["quote"].strip())]
                if group:
                    group = sorted(group + [{"chunk": c["chunk"], "quote": c["quote"]}], key=lambda x: x["chunk"])
                    key = (c["doc_id"], tuple(x["chunk"] for x in group))
                    if key in merged:
                        continue
                    merged.add(key)
                    c = dict(c, quote=" ".join(x["quote"].strip() for x in group), chunk=group[0]["chunk"])
            keep.append(c)
        cands = keep
        # 6차 개선: 근거 문장이 문구에서 멀리 떨어진 경우(앞뒤 2문장 밖, BM25 점수 낮음)를 놓쳤다.
        # 문서 전체에서 '작동 방식'을 말하면서 문구와 같은 인식 대상을 다루는 문장을 최대 3개까지 더 넣는다.
        ctgt = set(_TARGET.findall(claim["text"]))
        if ctgt or _MECH_EN.search(claim["text"]):
            far = 0
            for t in index.by_kind(tuple({c["kind"] for c in cands} | {"판매페이지", "사양표", "설명서", "공식 자료"})):
                if far >= 3 or (t["doc_id"], t["chunk"]) in seen:
                    continue
                q = t["quote"]
                if not (_MECH.search(q) or _MECH_EN.search(q) or _LEARN_EVID.search(q)):
                    continue
                if not (_PERCEIVE.search(q) or _PERCEIVE_EN.search(q)):
                    continue
                if ctgt and not (set(_TARGET.findall(q)) & ctgt) and not _MECH_EN.search(claim["text"]):
                    continue
                t.update(score=0.0, near=False, far=True)
                cands.append(t); seen.add((t["doc_id"], t["chunk"])); far += 1
        # 7차 개선: 근거를 문구 주변에서만 찾아, 같은 AI 기능을 설명한 문장·각주가 페이지의 다른 곳에 있으면 놓쳤다.
        # (예: "AI 기술로 더 섬세하게 세탁하세요"의 근거인 "인공지능 코스는 딥러닝 AI 기술을 이용해 의류의 재질을 감지한 후…"가
        #  페이지 아래쪽 각주에 있어 연결되지 않음. 12개 품목에서 '설명 못 찾음' 31건의 주된 원인이었다.)
        # 문구에서 AI 기능 이름을 뽑아 문서 전체에서 같은 이름이 든 문장을 후보에 넣는다. 개별 문구가 아니라 탐색 범위의 문제다.
        feats = _feature_names(claim["text"])
        if feats:
            added = 0
            for t in index.by_kind(tuple({c["kind"] for c in cands} | {"판매페이지", "사양표", "설명서", "공식 자료"})):
                if added >= 4 or (t["doc_id"], t["chunk"]) in seen:
                    continue
                q = t["quote"]
                if not any(f in _norm_feat(q) for f in feats):
                    continue
                if _is_restatement(t, claim["text"]):
                    continue
                # 기능 이름만 되풀이한 줄은 근거가 아니다. 작동 방식이나 조건·한계가 적힌 문장만 넣는다
                if not (_MECH.search(q) or _LEARN_EVID.search(q) or _COND.search(q) or _is_footnote(q)):
                    continue
                t.update(score=0.0, near=False, feat=True)
                cands.append(t); seen.add((t["doc_id"], t["chunk"])); added += 1
        else:
            # 기능 이름이 안 뽑히는 문구("AI 기술로 더 섬세하게 세탁하세요")는 이름으로 묶을 수 없다.
            # 이때는 'AI·인공지능'을 말하면서 작동 방식이나 조건을 적은 문장 가운데 문구와 말이 겹치는 것을 넣는다.
            cb2 = _bigrams(claim["text"])
            if _GENERIC.search(claim["text"]) and cb2:
                added = 0
                for t in index.by_kind(tuple({c["kind"] for c in cands} | {"판매페이지", "사양표", "설명서", "공식 자료"})):
                    if added >= 3 or (t["doc_id"], t["chunk"]) in seen:
                        continue
                    q = t["quote"]
                    if not _GENERIC.search(q):
                        continue
                    if not (_MECH.search(q) or _LEARN_EVID.search(q) or _COND.search(q)):
                        continue
                    if _is_restatement(t, claim["text"]):
                        continue
                    qb = _bigrams(q)
                    if not qb or len(cb2 & qb) / min(len(cb2), len(qb)) < 0.15:
                        continue
                    t.update(score=0.0, near=False, feat=True)
                    cands.append(t); seen.add((t["doc_id"], t["chunk"])); added += 1
        # 짧은 조각(OCR 줄 조각 등)은 근거가 되지 못한다. 각주 표시로 시작하는 줄은 예외
        cands = [c for c in cands if len(re.sub(r"\W", "", c["quote"])) >= 14 or _is_footnote(c["quote"])]
        # 13회차: 끝맺음 없이 잘린 판매 본문 줄(각주 포함)은 다음 줄을 이어 붙여 문장을 완성한다. 라벨러는 줄이 아니라 문장을 읽는다.
        for c in cands:
            if c["kind"] == "판매페이지" and not c.get("spec") and not _ENDS.search(c["quote"].strip()):
                j = _join_fragment(index, c)
                if j and len(j["quote"]) > len(c["quote"]):
                    c["quote"] = j["quote"]
                    c["joined"] = True
        # 판매 본문에서 온 후보는 문장이 끝나야 한다("dToF LiDAR 주행 센서를 적용한" 같은 조각 제외). 각주·사양표·공식 자료는 그대로
        cands = [c for c in cands if c["kind"] != "판매페이지" or c.get("spec") or c.get("adjacent") or c.get("joined") or _is_footnote(c["quote"]) or _ENDS.search(c["quote"].strip())
                 or (_WARN.search(c["quote"]) and c["doc_id"] == src["doc_id"] and abs(c["chunk"] - src["chunk"]) <= 2)  # 어긋남 표지가 든 조각은 문구 바로 옆일 때만(예: "맞춤 청소모드를 수동 설정하면")
                 or (_CONNECT.search(c["quote"].strip()) and _MECH.search(c["quote"]) and len(c["quote"]) >= 20)]
        # 지원 범위만 적은 안내("~모델에 한해 지원", "~기능은 동일")는 작동 방식이 아니므로 근거 후보에서 뺀다
        cands = [c for c in cands if not _SCOPE_ONLY.search(c["quote"])]
        # 문구 자체 후보: 센서·방식 이름과 함께 무엇을 인식·감지하는지 적혀 있을 때만(이름만 있는 문구는 근거가 아니다)
        if (_EVID.search(claim["text"]) and _MECH.search(claim["text"]) and _PERCEIVE.search(claim["text"])) \
                or (_MECH_EN.search(claim["text"]) and _PERCEIVE_EN.search(claim["text"])) or self_describes(claim["text"]):
            cands.append({"doc_id": src["doc_id"], "chunk": src["chunk"], "quote": src["quote"],
                          "kind": "판매페이지(문구 자체)", "score": 0.0, "self": True})
        uniq, qs = [], set()
        for c in cands:  # 같은 문장이 여러 경로로 들어온 경우 하나만 남긴다
            # Quick checks must not mistake a testimonial or promotional thanks
            # for a technical explanation merely because it shares product words.
            # Formal-card generation retains its established review pipeline.
            if exclude_testimonials and _TESTIMONIAL.search(c["quote"]):
                continue
            k = re.sub(r"\s+", "", c["quote"])[:80]
            if k not in qs:
                qs.add(k); uniq.append(c)
        cand_map[claim["id"]] = uniq[:22]  # 13회차: 이어 읽기·내용어 후보가 늘어 18 → 22
        claim["_cond_pool"] = cond_pool
        claim["_condition_context"] = condition_context
        claim["_condition_context_complete"] = True
        claim.setdefault("reviewer_status", None)
        claim.setdefault("reviewer_note", "")

    if llm.available() and claims:
        # 3차 개선: 한 번에 너무 많이 넘기면 응답이 잘려 JSON 파싱에 실패한다(2차 측정에서 문구 11개 제품이 통째로 규칙 판정으로 떨어짐).
        # 6개씩 나눠 부르고, 실패한 묶음은 반으로 쪼개 한 번 더 시도한 뒤에야 규칙 판정으로 넘긴다.
        for group in _batches(claims, 6):
            _link_group(group, cand_map, depth=0)
    else:
        for claim in claims:
            _link_rules(claim, cand_map[claim["id"]])
    for claim in claims:
        claim.pop("_cond_pool", None)
        claim.pop("_condition_context", None)
        claim.pop("_condition_context_complete", None)
    return claims


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _link_group(group: list[dict[str, Any]], cand_map: dict[str, list[dict[str, Any]]], depth: int) -> None:
    try:
        _link_llm_batch(group, cand_map)
    except llm.LLMError as e:
        if len(group) > 1 and depth < 2:
            half = (len(group) + 1) // 2
            _link_group(group[:half], cand_map, depth + 1)
            _link_group(group[half:], cand_map, depth + 1)
            return
        for claim in group:
            _link_rules(claim, cand_map[claim["id"]])
            claim["inference_mode"] = "rules_fallback"
            claim["inference_error"] = "llm_call_failed"
            claim["needs_review"] = True
            claim["note"] = "AI 판정 호출이 실패해 규칙 판정으로 대신했습니다. 검토가 필요합니다. (" + str(e)[:80] + ")"


def _similar(a: str, b: str) -> float:
    sa, sb = set(re.sub(r"\W", "", a)), set(re.sub(r"\W", "", b))
    return len(sa & sb) / max(1, len(sa | sb))


def _bigrams(t: str) -> set[str]:
    t = re.sub(r"\W", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _is_restatement(cand: dict[str, Any], claim_text: str) -> bool:
    """광고 문구를 되풀이한 문장인지. 같은 판매페이지 안의 문장은 더 엄격하게 거른다.
    짧은 쪽 문장의 글자쌍이 긴 쪽에 많이 들어 있으면 같은 말을 반복한 것으로 본다."""
    a, b = _bigrams(cand["quote"]), _bigrams(claim_text)
    if not a or not b:
        return False
    q = cand["quote"].strip()
    # 각주 표시로 시작하거나 조건 표현이 든 문장은 같은 기능을 말해도 되풀이가 아니라 조건·근거 후보다
    if re.match(r"^[※\*†①-⑳]|^\d{1,2}\)", q) or _COND.search(q):
        return False
    contain = len(a & b) / min(len(a), len(b))
    limit = 0.65 if cand.get("kind") == "판매페이지" else 0.85
    if contain >= limit and len(q) >= 1.4 * len(claim_text):
        # 문구로 시작하되 뒤에 센서 종류·수치 같은 설명이 더 붙은 긴 문장은 되풀이가 아니라 설명이다
        extra = re.sub(r"\W", "", q)[len(re.sub(r"\W", "", claim_text)) // 2:]
        if _MECH.search(extra) or re.search(r"\d", extra):
            return False
    return contain >= limit


def _link_llm_batch(claims: list[dict[str, Any]], cand_map: dict[str, list[dict[str, Any]]]) -> None:
    blocks = []
    for claim in claims:
        cands = cand_map[claim["id"]]
        lines = "\n".join(
            f"  [{i}] ({c['kind']}{', 문구에 붙은 번호 각주' if c.get('mark') else ''}{', 문구 바로 옆 문장' if c.get('near') else ''}{', 어긋남 확인 필요' if _WARN.search(c['quote']) else ''}) {c['quote']}"
            for i, c in enumerate(cands)) or "  (후보 없음)"
        blocks.append(f"### {claim['id']}\n주장: \"{claim['text']}\"\n후보:\n{lines}")
    prompt = BATCH_PROMPT.format(blocks="\n\n".join(blocks))
    data = llm.ask_json(prompt, max_tokens=8000)
    if not isinstance(data, dict):
        raise llm.LLMError("묶음 판정 응답 형식이 올바르지 않습니다.")
    # 9회차: 같은 카드를 다시 만들면 판정이 몇 건씩 바뀌었다. 같은 묶음을 한 번 더 판정받아,
    # 둘이 갈리면 라벨 기준과 같은 보수 원칙(근거 없음 > 있음, 조건 아니오 > 예, 어긋남은 검토로)으로 정한다.
    try:
        data2 = llm.ask_json(prompt, max_tokens=8000)
    except Exception:
        data2 = None
    if not isinstance(data2, dict):
        data2 = None
    for claim in claims:
        cands = cand_map[claim["id"]]
        row = data.get(claim["id"]) if isinstance(data.get(claim["id"]), dict) else None
        if row is None:
            _link_rules(claim, cands)
            claim["inference_mode"] = "rules_fallback"
            claim["inference_error"] = "missing_claim"
            claim["needs_review"] = True
            claim["note"] = "AI 판정에서 이 문구가 빠져 규칙 판정으로 대신했습니다. 검토가 필요합니다."
            continue
        row2 = data2.get(claim["id"]) if data2 and isinstance(data2.get(claim["id"]), dict) else None
        if row2 is None:
            _apply(claim, cands, row)
            claim["inference_mode"] = "llm_single_vote"
            claim["needs_review"] = True
            continue
        a, b = copy.deepcopy(claim), copy.deepcopy(claim)
        _apply(a, cands, row)
        _apply(b, cands, row2)
        rank = {"어긋남": 2, "없음": 1, "있음": 0}
        if a["basis_state"] == b["basis_state"]:
            pick = a if (a["conditions_noted"] <= b["conditions_noted"]) else b
            split = a["conditions_noted"] != b["conditions_noted"]
        else:
            pick = a if rank[a["basis_state"]] >= rank[b["basis_state"]] else b
            split = True
        claim.clear()
        claim.update(pick)
        claim["inference_mode"] = "llm_two_votes"
        if split:
            claim["llm_split"] = True
            claim["needs_review"] = True
            claim["note"] = (claim.get("note", "") + " " if claim.get("note") else "") + "두 번 판정한 결과가 갈려 더 보수적인 쪽으로 두었어요."


def _apply(claim: dict[str, Any], cands: list[dict[str, Any]], row: dict[str, Any]) -> None:
    status = row.get("status") if row.get("status") in STATUSES else "자료 부족"
    claim["llm_status"] = status  # 규칙 보정 전 AI 판정. 검토자가 규칙이 바꾼 건을 가려볼 수 있게 남긴다
    ev = []
    raw_indices = row.get("evidence")
    for k in raw_indices if isinstance(raw_indices, list) else []:
        try:
            # JSON numbers must be integer candidate IDs, never Python negative
            # indexes, booleans, numeric strings, or truncated floating points.
            if type(k) is not int or not 0 <= k < len(cands):
                continue
            c = cands[k]
            # 15회차(LG 개발 세트): '연출된 영상·이미지' 안내, 사양표의 지원 여부 행("O 지원 O 지원"), 플랫폼 광고 줄은 작동 설명이 아니다
            if _BOILER.search(c["quote"]) or _SPEC_ROW.search(c["quote"]) or _PLATFORM_AD.search(c["quote"]):
                continue
            if not any((e["doc_id"], e["chunk"]) == (c["doc_id"], c["chunk"]) for e in ev):
                ev.append({"doc_id": c["doc_id"], "chunk": c["chunk"], "quote": c["quote"], "kind": c["kind"]})
        except (ValueError, IndexError, TypeError):
            continue
    if status in ("근거 확인", "조건 확인", "추가 확인 필요") and not ev:
        status = "자료 부족"
        if row.get("status") == "추가 확인 필요":
            claim["needs_review"] = True
            claim["inference_error"] = "ungrounded_conflict"
    basis = row.get("basis") if row.get("basis") in ("learn", "vision", "sensor", "service", "none") else None
    if status == "자료 부족":
        basis = None
    conditions = str(row.get("conditions", "") or "")
    note = str(row.get("note", "") or "")
    # 2차 개선: '근거 확인'과 '조건 확인'의 경계는 라벨 기준(조건·한계 문장이 함께 있는가)으로 기계적으로 맞춘다.
    # 3차 개선: 조건은 인용된 근거 문장 안에 있어야 한다. AI가 적은 조건 설명만으로는 '조건 확인'으로 올리지 않는다.
    # 4차 개선: 근거는 '어떻게' 아는지(센서·카메라·측정·수치·학습 데이터)를 말해야 한다. 기기가 무엇을 하는지만 적은 문장은 근거가 아니다.
    if status in ("근거 확인", "조건 확인") and not _has_mechanism(claim["text"], ev):
        status = "자료 부족"
        note = (note + " " if note else "") + "기기가 무엇으로 어떻게 인식하는지(센서·방식·수치)를 적은 회사 설명을 찾지 못했습니다."
    # 4차 개선: 기능을 특정하지 않은 'AI' 표제("차원이 다른 인공지능 물걸레 로봇청소기")는 대응하는 작동 설명을 정할 수 없다
    if (status in ("근거 확인", "조건 확인") and _GENERIC.search(claim["text"]) and not _FUNC.search(claim["text"])
            and not _MECH.search(claim["text"]) and len(claim["text"]) <= 30 and _CATEGORY.search(claim["text"])):
        status = "자료 부족"
        note = (note + " " if note else "") + "어떤 기능을 말하는지 특정되지 않는 표제 문구입니다."
    # 7회차: AI 판정이 '자료 부족'이어도 문구 자체가 입력→동작을 구체적으로 적었으면 그 문장을 근거로 둔다('추가 확인 필요'는 건드리지 않는다)
    if status == "자료 부족" and row.get("status") != "추가 확인 필요" and self_describes(claim["text"]):
        src = claim.get("source") or {}
        if src.get("quote"):
            status = "근거 확인"
            ev = [{"doc_id": src.get("doc_id"), "chunk": src.get("chunk"), "quote": src["quote"], "kind": "판매페이지(문구 자체)"}]
            note = "문구 자체가 무엇을 감지·입력으로 받아 무엇을 하는지 적고 있어 근거로 보았습니다."
    # 7차 개선: 확인상태를 두 축으로 나눈다. LLM은 뒷받침 설명이 있는지만 판단하고(축 1),
    # 조건·한계가 적혀 있는지는 인용된 근거 문장에서 기계적으로 읽는다(축 2). 상태 이름은 두 축에서 파생한다.
    has_cond = any(_is_cond(e["quote"]) for e in ev)
    # 4회차: 조건·한계는 작동 설명과 다른 각주에 따로 적히는 일이 많다(예: 설명 각주와 "최대용량 6kg 이하에서 동작" 각주).
    # 인용 근거 바로 옆 문장이거나, 문구와 같은 AI 기능 이름을 가진 후보 문장에 조건이 있으면 그 조건도 본다.
    if not has_cond and ev:
        feats = _feature_names(claim["text"])
        for c in cands:
            q = c.get("quote", "")
            if not _is_cond(q) or any((c["doc_id"], c["chunk"]) == (e["doc_id"], e["chunk"]) for e in ev):
                continue
            near = any(c["doc_id"] == e["doc_id"] and abs(c["chunk"] - e["chunk"]) <= 1 for e in ev)
            same = bool(feats) and any(f in _norm_feat(q) for f in feats)
            if same or (near and not _TESTRES.search(q)):  # 9회차: 옆 줄의 시험 결과 안내는 다른 기능일 때가 많다
                ev.append({"doc_id": c["doc_id"], "chunk": c["chunk"], "quote": q, "kind": c["kind"], "cond_only": True})
                has_cond = True
                break
    # 6회차: AI 판정이 '조건 확인'이고 조건 문장을 적었으면, 후보 가운데 그 조건과 말이 겹치는 문장을 찾아 인용한다.
    # 조건은 여전히 회사 원문에 있어야 한다. AI가 적은 설명만으로는 올리지 않는다.
    if not has_cond and ev and row.get("status") == "조건 확인" and conditions.strip():
        cb = _bigrams(conditions)
        best, bs = None, 0.0
        for c in cands:
            q = c.get("quote", "")
            if not _is_cond(q) or any((c["doc_id"], c["chunk"]) == (e["doc_id"], e["chunk"]) for e in ev):
                continue
            qb = _bigrams(q)
            if cb and qb:
                sc = len(cb & qb) / min(len(cb), len(qb))
                if sc > bs:
                    best, bs = c, sc
        if best is not None and bs >= 0.3:
            ev.append({"doc_id": best["doc_id"], "chunk": best["chunk"], "quote": best["quote"], "kind": best["kind"], "cond_only": True})
            has_cond = True
    # 9회차: 아직 조건이 없으면 문서 전체의 조건 문장 가운데 (1) 근거 바로 앞뒤 줄의 조건 각주를, 없으면
    # (2) 근거·문구와 같은 기능 이름(인공지능코스, 레이더센서, AI 절약모드 …)을 가진 문장을 하나 붙인다.
    pool = claim.get("_cond_pool") or []
    if not has_cond and ev and status in ("근거 확인", "조건 확인") and pool:
        base = [e for e in ev if not e.get("cond_only")]
        names = set(_feature_names(claim["text"]))
        for e in base:
            names.update(_feature_names(e["quote"]))
            flat = _norm_feat(e["quote"])
            for m in _FEAT_SUFFIX.finditer(flat):  # "레이더센서", "ai코스", "절전모드" — 접미어 앞 2~4글자까지 붙여 본다
                for k in (2, 3, 4):
                    if m.start() - k >= 0 and re.fullmatch(r"[가-힣a-z]+", flat[m.start() - k:m.start()]):
                        names.add(flat[m.start() - k:m.end()])
            names.update(_norm_feat(w) for w in re.findall(r"\[([^\]]{2,12})\]", e["quote"]))
        names = {n for n in names if len(n) >= 4}
        src = claim.get("source") or {}
        used = {(e["doc_id"], e["chunk"]) for e in ev} | {(src.get("doc_id"), src.get("chunk"))}  # 문구 자신의 줄은 조건이 아니다
        pool = [c for c in pool if _similar(c["quote"], claim["text"]) < 0.5]  # 문구를 그대로 담은 줄("…권장합니다. AI 맞춤쾌적")도 뺀다
        # 근거 바로 앞뒤 각주의 조건을 먼저 본다("* 인공지능코스는 최대용량 6kg 이하에서 동작")
        pick = next((c for c in pool if (c["doc_id"], c["chunk"]) not in used and not _TESTRES.search(c["quote"])
                     and _is_footnote(c["quote"])
                     and any(c["doc_id"] == e["doc_id"] and isinstance(e.get("chunk"), int)
                             and abs(c["chunk"] - e["chunk"]) == 1 for e in base)), None)
        if pick is None and names:  # 없으면 같은 기능 이름을 가진 조건 문장
            pick = next((c for c in pool if (c["doc_id"], c["chunk"]) not in used
                         and any(n in _norm_feat(c["quote"]) for n in names)), None)
        if pick is not None:
            ev.append({"doc_id": pick["doc_id"], "chunk": pick["chunk"], "quote": pick["quote"], "kind": pick["kind"], "cond_only": True})
            has_cond = True
    # 9회차: "AI가 설치 환경에 맞게 진동을 줄여"처럼 AI가 환경·상황에 맞춘다는 문구는, 무엇을 보고 무엇을 바꾸는지 적은 근거가 있어야 한다.
    # 센서·부품 이름만 있는 문장("진동 감지 센서와 2단 댐퍼로")은 그 판단의 근거가 아니다(남은 과대 판정 1건의 원인).
    if status in ("근거 확인", "조건 확인") and _VAGUE_ADAPT.search(claim["text"]):
        if not any((_PROC_V.search(e["quote"]) and _CAUSE.search(e["quote"])) or self_describes(e["quote"])
                   or (_CERT.search(e["quote"]) and _TESTRES.search(e["quote"]))  # 14회차: 인증 문장은 무엇을 쟀는지(시험 내용) 적혀 있을 때만 근거(4차 라벨러 3인)
                   or (_SENSOR_SPEC.search(e["quote"]) and _shares_noun(claim_text_for_vague(claim), e["quote"]))  # 13회차: "진동 감지 센서와 2단 댐퍼로 … 탈수"처럼 센서와 대상(진동)을 함께 든 문장(3차 라벨러 3인 일치)
                   for e in ev if not e.get("cond_only") and not _TESTCOND.search(e["quote"])):
            status, ev, has_cond, basis = "자료 부족", [], False, None
            note = (note + " " if note else "") + "AI가 무엇을 보고 어떻게 맞추는지 적은 회사 설명을 찾지 못했습니다."
    basis_state, _ = axes_from_status(status)
    conditions_noted = has_cond and basis_state == "있음"
    status = status_from(basis_state, conditions_noted)
    if not conditions_noted:
        conditions = ""
    if status == "조건 확인":  # 조건 문장은 인용된 근거 가운데 한계를 적은 문장을 우선 보여 준다
        cond_q = next((e["quote"] for e in ev if _is_cond(e["quote"])), "")
        if cond_q and (not conditions.strip() or not _COND.search(conditions)):
            conditions = cond_q
    # 3차 개선: '학습한다'는 문구는 학습(데이터·이미지·모델)을 말하는 근거가 있어야 한다. 센서 이름만으로는 근거가 아니다
    learn_claim = bool(_LEARN_CLAIM.search(claim["text"]))  # 문구 자체가 학습·습관을 말할 때만
    learn_ok = any(_LEARN_EVID.search(e["quote"]) or (_PROC_V.search(e["quote"]) and _CAUSE.search(e["quote"]))
                   or (_CERT.search(e["quote"]) and _TESTRES.search(e["quote"])) for e in ev)  # 14회차: 인증 사실만 적힌 각주는 근거 아님(4차 과대 5/8의 원인)
    # 13회차: 문구가 무엇을 학습·분석하는지(사용 패턴 등)를 이미 적었고, 본문의 다른 문장이 그 결과 동작(송풍 건조 등)을 적었으면
    # 둘을 이어 읽은 것이 작동 설명이다(3차 라벨러 3인 일치). 문구 혼자만 있는 경우는 여전히 근거가 아니다.
    if not learn_ok and _LEARN_EVID.search(claim["text"]) and any(e.get("kind") != "판매페이지(문구 자체)" and _PROC_V.search(e["quote"]) for e in ev):
        learn_ok = True
    # 7회차: 문구 자체가 무엇을 학습해 무엇이 바뀌는지 적었으면(예: "공간까지 학습해 생활 면적에 맞춰 운전") 그 문장을 근거로 둔다
    if status in ("근거 확인", "조건 확인") and learn_claim and not learn_ok and self_describes(claim["text"]):
        src = claim.get("source") or {}
        if src.get("quote"):
            ev = [{"doc_id": src.get("doc_id"), "chunk": src.get("chunk"), "quote": src["quote"], "kind": "판매페이지(문구 자체)"}] + ev
            learn_ok = True
    if (status in ("근거 확인", "조건 확인") and learn_claim and not learn_ok):
        basis_state, conditions_noted = "없음", False
        status = status_from(basis_state, conditions_noted)
        ev = []
        note = (note + " " if note else "") + "학습·데이터에 대한 회사 설명을 찾지 못해 '자료 부족'으로 두었습니다."
        basis = None
    if basis_state != "있음":
        conditions = ""
    claim.update(status=status, basis_state=basis_state, conditions_noted=conditions_noted,
                 evidence=ev[:3], conditions=conditions, note=_haeyo(note))
    from .condition_grounding import refine_conditions
    refine_conditions(claim, cands)
    # 검토자가 아직 매기지 않은 경우에만 AI 초안 값을 채운다
    if basis and not claim.get("basis_kind"):
        claim["basis_kind"] = basis
        claim["basis_kind_ai"] = basis


# 6차 개선: 사이트 글은 모두 해요체인데 LLM이 쓴 설명(note)만 합니다체로 섞여 있었다.
# 인용한 원문(evidence·conditions)은 그대로 두고, AI가 쓴 설명 문장만 해요체로 바꾼다.
_HAEYO = [
    ("하지 않습니다", "하지 않아요"), ("되지 않습니다", "되지 않아요"), ("지 않습니다", "지 않아요"),
    ("습니다만", "지만"), ("았습니다", "았어요"), ("었습니다", "었어요"), ("였습니다", "였어요"),
    ("겠습니다", "겠어요"), ("됩니다", "돼요"), ("입니다", "이에요"), ("합니다", "해요"),
    ("습니다", "어요"), ("ㅂ니다", "어요"),
]


def claim_text_for_vague(claim: dict[str, Any]) -> str:
    return claim.get("text", "")


def _haeyo(text: str) -> str:
    out = text
    for a, b in _HAEYO:
        out = out.replace(a, b)
    # 받침 없는 말 뒤의 '이에요'는 '예요'로("기능이에요" 유지, "카메라이에요" → "카메라예요")
    out = re.sub(r"([가-힣])이에요", lambda m: m.group(1) + "이에요"
                 if (ord(m.group(1)) - 0xAC00) % 28 else m.group(1) + "예요", out)
    return out


_MECH = re.compile(r"(센서|센싱|컴프레서|압축기|인버터|카메라|LiDAR|라이다|ToF|구조광|레이저|초음파|적외선|자이로|와이파이|Wi-Fi|채널|이미지를 학습|딥러닝|신경망|모델|알고리즘|초당|스캔|측정)")
_WARN = re.compile(r"(수동|직접 설정|설정하면|예정|업데이트를 통해|이 아닌|아닌,|제외한|미지원|지원하지 않|상위 모델|모델이며)")
_COND = re.compile(r"(않을 수|못할 수|못하거나|어려울 수|어렵습니다|달라질 수|다를 수|차이가 (?:있|날|발생)|오인식|미인식|영향을 받|저하|"
                   r"제한(?:이|적|될|되|을 받)|제외|(?<!제)한해|(?<!제)한하여|(?<!제)한합니다|한정|다르게 (?:인식|감지|판단)|(?:이내|내)에서 (?:동작|작동)|시에만|에서만 (?:작동|동작|사용|가능)|일부 모드|이하에서|이상에서|모델에 한|에만 (?:적용|지원)|지원하지 않|사전에 .{0,20}(?:설정|완료|등록)|설정이 필요|필요합니다|활성화해야|켜야|"
                   r"테스트 기준|실험실|자체 (?:실험|테스트)|내부 테스트|기준으로 합니다|권장|주의|이하(?:인|의|는|일)|만 (?:\S+ )?(?:인식|감지|지원|작동|가능)(?:할|될|합니다|됩니다|할 수))")
# 9회차: 어느 제품에나 붙는 안내(연출 이미지, 색상, 구입처, 설치비)는 그 기능의 조건이 아니다
_BOILER = re.compile(r"(연출(?:된|컷)|이해를 돕기|색상 및 스[펙팩]|제품 색상|구입처|취급\s?여부|모니터에 따라|재고\s?상황|생산월|광고적 표현|사전 예고 없이|설치비|자재|철거)")
_TESTRES = re.compile(r"(시험 ?결과|실험 ?결과|시험실|실험실|측정 결과|참조규격|시험 ?조건|시험 ?방법|시험 ?내용|시험 ?항목|실험 ?방법|실험치)")
# 15회차: 사양표의 "O 지원 / X 미지원" 행과 "ThinQ ON과 함께 쓰면 …" 같은 플랫폼 광고 줄(LG 공식몰 공통)
_SPEC_ROW = re.compile(r"(?:\bO\s*지원\b.*){2,}|(?:지원\s*){3,}|(?:O|X|△)\s*(?:지원|미지원)\s*(?:O|X|△)\s*(?:지원|미지원)")
_PLATFORM_AD = re.compile(r"(ThinQ ON과 함께|AI로 연결됩니다|SmartThings와 함께 쓰면|앱을 설치하면 더|가전 구독|케어십|구독하면)")


def _is_cond(q: str) -> bool:
    """조건·한계 문장인가. 어느 제품에나 붙는 안내(연출 이미지·색상·구입처)는 뺀다."""
    return bool(_COND.search(q)) and not _BOILER.search(q)
_FEAT_SUFFIX = re.compile(r"(센서|코스|모드)")
_VAGUE_ADAPT = re.compile(r"(?:AI|인공지능)[이가]?\s?.{0,12}(?:환경|상황)에 맞(?:게|춰|는)")
_PERCEIVE = re.compile(r"(인식|인지|감지|스캔|측정|탐지|식별|파악|구분|분석|맵핑|매핑|지도)")
_LEARN_EVID = re.compile(r"(학습|데이터|이미지|딥러닝|신경망|모델|알고리즘|사물 인식|객체 인식|종의? 사물|종을 인식|장애물을? 인식|"
                         r"사용\s?패턴|사용\s?이력|사용\s?기록|선호|패턴을 분석|패턴을 기반|피드백)")
# 3회차: 제어 절차 문장의 '원인 → 동작' 연결어. 이것과 _PROCESS가 함께 있으면 작동 방식을 적은 것으로 본다
_CAUSE = re.compile(r"(하면|되면|후에는|하기 전에|도달하면|감지하면|감지하여|분석하여|분석해|센싱하여|기반으로|바탕으로|을 통해|를 통해|에 따라|에 맞춰|에 맞게|후에?\s|이후|뒤에?\s|(?<=[가-힣])지면|때는|때에는|때\s|시간에는|시간대에는|시점이 되면|(?:유지|도달|감지|인식|측정|운전|작동|동작)\s?시(?:에)?\s)")
# 6회차: 시험·측정 조건만 적은 각주
# 11회차: 이전 라벨 148건 재채점에서 찾은 과대 판정 — "이 기능은 앱에서 수동으로 활성화해야 합니다" 같은
# 활성화·설정 안내문만 인용하고 근거로 인정했다. 활성화 안내는 조건이지 작동 방식이 아니다(코드북 v1·v2 공통).
_ACTIVATE = re.compile(r"(활성화해야|활성화가 필요|수동으로 (?:활성화|설정|켜)|설정해야 (?:합니다|해요)|켜야 (?:합니다|해요)|세팅 필요|설정이 필요합니다)")
_TESTCOND = re.compile(r"(실험치|시험 결과|시험결과|자사 (?:실험|시험|테스트)|테스트 기준|측정 기준|시험 기준|시험부하|시험 부하|부하,|코스 기준|"
                       r"설정 기준|기준으로 측정|부착하여 .{0,12}측정|측정 조건|시험 조건)")
# 4회차: 절차 문장의 동작 동사. 끝맺음 위치와 상관없이 원인 연결어와 함께 있으면 작동 방식으로 본다
_PROC_V = re.compile(r"(전환|작동|동작|감지|도달|분석|판단|조절|제어|설정|측정|인식|학습|시작|종료|선택|건조|센싱|유지|바꾸|바뀌|최소화|줄여|줄이|낮춰|높여|절전모드|절약모드|알림|알려|제거)")


_MECH_EN = re.compile(r"\b(camera|sensor|LiDAR|ToF|laser|infrared|ultrasonic|algorithm|neural|deep learning|trained)\b", re.I)
_PERCEIVE_EN = re.compile(r"\b(detect\w*|recogni[sz]\w*|identif\w*|sens(?:e|es|ing)|scan\w*|map(?:s|ping)?|measur\w*)\b", re.I)
_SENSOR_SPEC = re.compile(r"(센서|LiDAR|라이다|ToF|카메라|레이저|적외선|초음파)", re.I)
_STEP = re.compile(r"^(?:[A-Za-z가-힣 ]{0,12}\s)?STEP\s*\d", re.I)  # 13회차: "AI 절약 모드 STEP1."처럼 기능 이름이 앞에 붙은 단계도
_ENDS = re.compile(r"((다|요|죠|음|함|됨|니다|세요|습니다|입니다)[\.\!\?\)\*\s]*$|[\.\!\?][\)\*\s]*$)")
_CONNECT = re.compile(r"(하고|이고|되고|하며|되며|하여|되어|해서|면서|지만|라서|해|하면|되면|않고)[,\s]*$")  # 줄 병합이 덜 된 설명 문장(센서·방식이 들어 있을 때만 인정)
_MECH_NAME = re.compile(r"(LiDAR|라이다|dToF|ToF|구조광|초음파|적외선|자이로|RGB|와이파이|Wi-Fi|레이저|3D 센서|AIVI|VLM)", re.I)
_SCOPE_ONLY = re.compile(r"(모델에 한해|모델과 동일|기능은 동일|모델에서만 지원|모델 전용|모델에 해당)")
_LEARN_CLAIM = re.compile(r"(학습|습관|패턴|성장형|진화하는|스스로 배|배우|understand|learn)", re.I)

# 10회차: 전 세트(로봇청소기) 교차 확인에서 과대 판정 2건의 원인 — 인식 기능 주장에 "인식되면 ~한다"는 결과 절차를,
# "최대 2회 반복" 같은 횟수 숫자를 작동 설명으로 받아 주었다. 인식이 계기인 절차와 세는 숫자는 따로 거른다.
_PERC_CAUSE = re.compile(r"(인식되면|인식하면|인식하여|인식해|감지되면|감지하면|감지하여|감지해|감지 시)")
_MEAS_PHYS = re.compile(r"\d[\d,\.]*\s*(?:mm|cm|m\b|㎝|㎜|%|Pa|°|도|℃|ml|㎖|㎡|분|시간|dB|kg|W\b)")
_MEAS = re.compile(r"\d[\d,\.]*\s*(?:mm|cm|m\b|㎝|㎜|종|가지|개|장|%|Pa|회|°|도|ml|㎖|㎡)")
_CATEGORY = re.compile(r"(청소기|공기청정기|제습기|가습기|세탁기|건조기|냉장고|에어컨|제품|가전)$|(청소기|청정기|가전)\s*$")
_GENERIC = re.compile(r"((?<![A-Za-z])AI(?![A-Za-z])|인공지능)")
_FUNC = re.compile(r"(인식|인지|감지|판단|분석|학습|구분|파악|분류|스캔|회피|탐지|식별|예측|조절|측정|맵핑|매핑|청정|절약|절전|주행|추천|맞춤|대화|음성)")


_TARGET = re.compile(r"(먼지|미세먼지|오염|얼룩|액체|물기|장애물|사물|물체|가구|가전|케이블|양말|반려동물|배설물|카펫|러그|바닥|문턱|계단|벽|공간|방|"
                     r"밝기|조도|온도|습도|냄새|가스|유증기|공기질|사람|부재|소음|탁도|색도|오수)")


# 7회차: 놓친 근거 10건 중 4건은 문구 자체가 입력→동작을 구체적으로 적은 경우였다(라벨 기준 v2: 판매 본문 안의 작동 설명은 근거).
# 다만 "카펫을 인식하여 회피"처럼 무엇으로 아는지 없는 문장은 이전 라벨(1~3차)에서 자료 부족이었으므로,
# 감지 대상·입력이 구체적이거나(센서·레이더·무게·사용 패턴), 계기가 분명하거나(요청하면·다다르면), 점검 대상 목록이 있을 때만 인정한다.
# 문구 자체가 '무엇을 입력으로 무엇을 하는지'를 구체적으로 말하는가(라벨 기준 v2: 판매 본문 안의 작동 설명은 근거)
_SELF_SENSE = re.compile(r"(감지하여|감지해|감지하면|감지 시|분석하여|분석해|인식하여|인식해|인식하면|측정하여|측정해|센싱하여|센싱해|학습하여|학습해)")
_SELF_EVENT = re.compile(r"(요청하면|말하면|다다르면|도달하면|가까워지면|높아지면|낮아지면|떨어지면|이상이면|이하이면)")
# 13회차: '사용 패턴을 분석해'는 무엇을 어떻게 학습하는지가 없어 라벨러가 근거로 보지 않았다(시험 세트 과대 1건). 물리 입력만 남긴다.
_SELF_SRC = re.compile(r"(센서|레이더|카메라|라이다|LiDAR|무게|오염도|냉매|습도|온도|움직임|재실|위치|공간|면적)")
_SELF_ACT = re.compile(r"(제안|선정|선택|안내|알려|알림|메시지|연출|전환|조절|제어|설정해|설정합|맞춰|투입|켜|켤|끄|멈추|멈춥|작동|동작|운전|발송|보내)")
_SELF_DEPEND = re.compile(r"(습도|온도|무게|오염도|운전 시간|부하)에 따라(?:서)?\s?.{0,12}(건조|조절|전환|설정|운전|세탁)")
_DIAG = re.compile(r"[가-힣A-Za-z]{2,}(?:부터|와|과|,)\s?.{0,24}(?:까지|등).{0,12}(?:진단|점검)")
_ATTR = re.compile(r"(무게|종류|오염도|옷감|재질|부하|사용\s?패턴|운전 시간|건조도)")
_INPUT_FIT = re.compile(r"(?:무게|종류|오염도|옷감|재질|부하|사용\s?패턴|운전 시간|건조도)[^.]{0,24}?에 맞(?:는|게|춰|춘)[^.]{0,16}(?:코스|모션|시간|세탁|건조|투입|운전)")


def self_describes(t: str) -> bool:
    # 1) 무엇으로(센서·레이더) 또는 무엇을(무게·온도·사용 패턴) 감지·분석해 무엇을 하는지
    for m in _SELF_SENSE.finditer(t):
        win = t[max(0, m.start() - 24):m.start()]
        # 13회차: '온도 사용 패턴을 분석해'처럼 입력이 패턴·이력·습관(학습 자료)이면 문구만으로는 근거가 아니다(3차 라벨러 3인 일치).
        if re.search(r"(패턴|이력|습관|데이터)", win):
            continue
        if _SELF_SRC.search(win) and _SELF_ACT.search(t[m.end():]):
            return True
    # 2) 어떤 일이 생기면(요청하면·다다르면) 무엇을 하는지
    m = _SELF_EVENT.search(t)
    if m and _SELF_ACT.search(t[m.end():]):
        return True
    # 2-1) 측정값에 따라 무엇을 하는지("습도에 따라서 알아서 내부를 건조")
    if _SELF_DEPEND.search(t):
        return True
    # 3) 점검 대상 목록("냉매량부터 실내외기 모터와 센서까지 진단")
    if _DIAG.search(t):
        return True
    # 4) 판단에 쓰는 입력 항목 두 가지 이상과 그에 맞춰 정하는 것("무게, 종류, 오염도에 맞는 코스")
    m = _INPUT_FIT.search(t)
    if m and len({a.replace(" ", "") for a in _ATTR.findall(m.group(0))}) >= 2:
        return True
    return False


_SIZE = re.compile(r"(?:PM\s?(\d+(?:\.\d+)?))|(?:(\d+(?:\.\d+)?)\s?(?:㎛|μm|um|마이크로미터))")


def _sizes(t: str) -> set[str]:
    """문구·문장에 적힌 입자 크기(PM1.0, 1.0㎛ → '1.0')."""
    out = set()
    for a, b in _SIZE.findall(t):
        v = a or b
        try:
            out.add(f"{float(v):g}")
        except ValueError:
            pass
    return out


_CERT = re.compile(r"(?:AI\+?|인공지능)\s?(?:인증|성능\s?인증)|인증\s*(?:내용|항목|명)\s*[:：]?.{0,40}(?:AI|인공지능)")
_NOUN_STOP = {"AI", "인공지능", "환경", "상황", "설치", "효과", "효과적", "스스로", "제품", "기능", "사용", "사용자", "자동", "최적", "다양", "편리", "똑똑", "알아서"}


def _content_nouns(text: str) -> set[str]:
    """문장의 내용어(2글자 이상 한글 낱말에서 일반어·조사 꼬리를 뺀 것)."""
    out = set()
    for w in re.findall(r"[가-힣]{2,}", text):
        w = _strip_particle(w)
        if len(w) < 2 or w in _NOUN_STOP or any(w.startswith(sw) for sw in ("환경", "상황", "스스로", "효과", "알아서", "가능", "제공", "위해", "통해", "따라", "있습", "합니", "됩니")):
            continue
        out.add(w[:3] if len(w) > 3 else w)
    return out


def _names_feature(claim_text: str, q: str) -> bool:
    """후보 문장이 문구의 AI 기능 이름(AI 절약모드, AI 맞춤건조 …)을 들고 있는가."""
    names = _feature_names(claim_text)
    if not names:
        return False
    nq = _norm_feat(q)
    return any(n in nq for n in names)


def _shares_noun(claim_text: str, q: str) -> bool:
    """문구와 후보가 같은 내용어(진동·소음·냉매 …)를 공유하는가. 'AI·환경·상황' 같은 일반어는 빼고 본다."""
    words = {_strip_particle(w) for w in re.findall(r"[가-힣]{2,}", claim_text)}
    words = {w for w in words if w not in _NOUN_STOP and not any(w.startswith(sw) for sw in ("환경", "상황", "스스로", "효과", "알아서"))}
    nq = q.replace(" ", "")
    return any(w in nq for w in words)


def _has_mechanism(claim_text: str, ev: list[dict[str, Any]]) -> bool:
    """근거가 '어떻게' 또는 '무엇을' 아는지 말하는가. 센서·방식·수치·학습 데이터가 있거나,
    근거 문장이 인식·감지와 함께 주장에 없는 인식 대상을 새로 밝히면 인정한다."""
    # 6회차: 시험 조건 각주("5kg 부하, 표준코스 기준의 자사 실험치", "가속도계를 부착해 측정")는 작동 방식이 아니다(과대 판정 원인)
    # 13회차: 시험 각주라도 그 기능 이름을 들어 시험했으면("AI 절약모드 켜짐 유/무에 따른 소비전력량 비교") 그 기능을 다룬 시험이므로
    # 근거다(코드북: 시험기관 문장은 그 시험이 그 기능을 다룬 경우 근거). 기능 이름 없이 부하·온도 조건만 적은 각주는 전처럼 뺀다.
    if any((_TESTCOND.search(e["quote"]) or _TESTRES.search(e["quote"])) and _names_feature(claim_text, e["quote"]) for e in ev):
        return True
    ev = [e for e in ev if not _TESTCOND.search(e["quote"]) and not (_ACTIVATE.search(e["quote"]) and not _MECH.search(e["quote"]))]
    if any(self_describes(e["quote"]) for e in ev):
        return True
    if not ev and not (_MECH.search(claim_text) and _PERCEIVE.search(claim_text)):
        return False
    texts = [claim_text] + [e["quote"] for e in ev]
    if any(_MECH.search(t) or _LEARN_EVID.search(t) or _MECH_EN.search(t) for t in texts):
        return True
    # 3회차: 수치는 근거 문장에만 있고 문구에 없는 것일 때만 작동 설명으로 본다.
    # 문구 자신의 "최대 60%"나, 근거가 그 숫자를 되풀이한 것은 '어떻게'가 아니다(과대 판정 2건의 원인).
    # 10회차: 세는 숫자(2회, 5개)는 작동 방식이 아니므로, 물리량 단위가 있는 수치만 새 수치로 본다
    claim_meas = {re.sub(r"\s", "", m) for m in _MEAS_PHYS.findall(claim_text)}
    for e in ev:
        ev_meas = {re.sub(r"\s", "", m) for m in _MEAS_PHYS.findall(e["quote"])}
        if ev_meas - claim_meas:
            return True
    # 3회차: 제어 절차(무엇을 근거로 언제 무엇으로 바뀌는지)를 적은 문장은 센서·학습 단어가 없어도 작동 방식이다.
    # 라벨 기준 v2와 같은 기준이다. 예: "희망온도에 도달하면 절전모드로 자동 전환됩니다"
    claim_tg = set(_TARGET.findall(claim_text))
    for e in ev:
        q = e["quote"]
        if not (_PROC_V.search(q) and _CAUSE.search(q)):
            continue
        # 10회차: 주장이 인식·감지 기능이고 절차의 계기도 그 인식이면("얼룩이 인식되면 청소 시작"),
        # 무엇으로 아는지나 새 인식 대상 없이 주장을 절차로 되풀이한 것이므로 근거가 아니다
        if _PERCEIVE.search(claim_text) and _PERC_CAUSE.search(q) \
                and not (_MECH.search(q) or _LEARN_EVID.search(q) or (set(_TARGET.findall(q)) - claim_tg)):
            continue
        return True
    # 5차 개선: 판매 본문이 아닌 회사 자료(사양표·설명서·공식 자료)가 같은 인식·감지 동작을 설명하면 근거로 본다
    # (예: 문구 "밝기를 감지하여 취침 모드" ↔ 사양표 "방 안이 다시 밝아지면 자동 모드로 돌아갑니다")
    if any(e["kind"] in ("사양표", "설명서", "공식 자료", "공공DB") and _PERCEIVE.search(claim_text + " " + e["quote"]) for e in ev):
        return True
    # 6차 개선: 센서 이름이 없어도 '무엇을 어떻게 가려내는지'(마른/젖은, 색도, 상태·유형 구분)나
    # 처리 위치(기기 안에서 처리, 네트워크 없이)를 밝힌 문장은 작동 방식 설명으로 본다.
    for t in texts:
        if _DISCRIM.search(t) and _STATEWORD.search(t):
            return True
        if _ONDEVICE.search(t):
            return True
    # 근거 문장이 인식·감지와 함께 주장에 없는 인식 대상을 새로 밝히는 경우
    claim_targets = set(_TARGET.findall(claim_text))
    for e in ev:
        if _PERCEIVE.search(e["quote"]) and (set(_TARGET.findall(e["quote"])) - claim_targets):
            return True
    return False


_DISCRIM = re.compile(r"(식별|구분|분류|판별|분석|측정|감지|인식|예측)")
_STATEWORD = re.compile(r"(마른|젖은|건조한|액체|고체|상태(?:인지|인지를|를|가|로)|유형|종류|색도|탁도|밝기|조도|농도|크기|높이|너비|두께|무게|오염도|청결|'?좋음'?|나쁨)")
_ONDEVICE = re.compile(r"(네트워크 연결 없이|인터넷 없이|오프라인|기기 (?:안|내)에서|온디바이스|on-device|서버로 보내지 않)", re.I)


_MARK = re.compile(r"(?:(?<=[가-힣\)\.])(\d{1,2})(?=[\s,\.]|$))|([¹²³⁴⁵⁶⁷⁸⁹⁰]+)|(\*+)(?=[\s,\.:]|$)")
_SUP = str.maketrans("¹²³⁴⁵⁶⁷⁸⁹⁰", "1234567890")
_NUMTOK = re.compile(r"\d[\d,\.]*\s*(?:개|종|가지|mm|cm|%|Pa|pa|°C|℃|RPM|rpm|분|시간|㎡)")


def _numbered_footnotes(index: Any, claim_text: str, src: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """문구(와 바로 앞뒤 줄)에 붙은 각주 표시를 따라가 해당 각주를 돌려준다.
    - 번호 각주("회피1", "유형¹⁹" → "19. 제조사 내부 테스트 기준…", "2) 드리미 랩 자체 실험 결과…")
    - 별표 각주("240개 이상의 장애물*" → "*240개 이상의 장애물: 사내 실험실 테스트를 기준으로 합니다.")
    한 줄에 각주가 여러 개 붙어 있으면 해당 번호 부분만 잘라 낸다. 한계·방식·수치가 없는 각주(교환·반품 안내 등)는 뺀다."""
    texts = [claim_text]
    if src is not None:
        texts += [n["quote"] for n in index.neighbors(src["doc_id"], src["chunk"], span=2)]
    nums: set[str] = set()
    toks: set[str] = set()
    stars = False
    for t in texts:
        for m in _MARK.finditer(t):
            if m.group(1):
                nums.add(m.group(1))
            elif m.group(2):
                nums.add(m.group(2).translate(_SUP))
            elif m.group(3):
                stars = True
        toks.update(re.sub(r"\s", "", x) for x in _NUMTOK.findall(t))
    if not nums and not (stars and toks):
        return []
    out: list[dict[str, Any]] = []
    for d, i, t, kind in getattr(index, "entries", []):
        q = t.strip()
        seg = ""
        for n in nums:
            m = re.search(rf"(?:^|(?<=[\s※\*\[]))(?:{n}[\.\)\]]|{n.translate(str.maketrans('1234567890', '¹²³⁴⁵⁶⁷⁸⁹⁰'))})\s*(?!\d)", q)
            if m:
                rest = q[m.start():]
                nxt = re.search(r"\s(?:\d{1,2}[\.\)\]]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+)\s*(?!\d)\S", rest[2:])
                seg = rest[: nxt.start() + 2] if nxt else rest
                break
        if not seg and stars and toks and q.startswith("*"):
            if any(tok in re.sub(r"\s", "", q.split(":")[0]) for tok in toks):
                seg = q
        if seg and (_COND.search(seg) or _MECH.search(seg) or _MEAS.search(seg)) and len(seg) >= 8 \
                and not any(o["quote"] == seg.strip() for o in out):
            out.append({"doc_id": d, "chunk": i, "quote": seg.strip(), "kind": kind, "score": 0.0, "foot": True})
        if len(out) >= 6:
            break
    return out

def _is_footnote(q: str) -> bool:
    return bool(re.match(r"^[※\*†①-⑳]|^\d{1,2}\)", q.strip()))
# 작동 절차를 적은 문장(언제 무엇으로 바뀌는지). 센서·학습 단어가 없어도 라벨러 3인은 작동 방식으로 인정했다.
# 예: "파워냉방 모드 작동 후 희망온도에 도달하면 절전모드로 자동 전환됩니다", "운전 시간에 따라 10~45분까지 자동 설정됩니다"
_PROCESS = re.compile(r"(전환|작동|동작|감지|도달|분석|판단|조절|제어|설정|측정|인식|학습|시작|종료|선택)"
                      r"[^.\n]{0,40}(됩니다|합니다|해요|돼요|줍니다|드립니다|있습니다|이에요|예요)")

_EVID = re.compile(r"(센서|카메라|라이다|LiDAR|ToF|구조광|레이저|초음파|적외선|자이로|와이파이|Wi-Fi|신호|채널|학습|딥러닝|신경망|모터|프로세서|칩|알고리즘|dB|%|mm|cm|분|시간|회|단계|모델|방식|구조|측정|시험|인증)")


def _link_rules(claim: dict[str, Any], cands: list[dict[str, Any]]) -> None:
    """같은 기능의 설명을 고른 뒤 LLM 경로와 같은 근거 검사를 적용한다."""
    pool = [c for c in cands if (c["kind"] in ("설명서", "사양표", "공공DB", "공식 자료") or c.get("near") or c.get("self") or c.get("adjacent") or c.get("joined"))
            and not _BOILER.search(c["quote"]) and not _TESTCOND.search(c["quote"]) and not _SPEC_ROW.search(c["quote"]) and not _PLATFORM_AD.search(c["quote"])]
    def _mech(q: str) -> bool:
        return bool(_MECH.search(q) or _PROC_V.search(q) or _SENSOR_SPEC.search(q) or _LEARN_EVID.search(q) or self_describes(q))
    def related(c: dict[str, Any]) -> bool:
        q = c["quote"]
        named = _feature_names(claim["text"])
        other = _feature_names(q)
        if named and other and not _names_feature(claim["text"], q):
            return False
        return bool(_shares_noun(claim["text"], q) or _names_feature(claim["text"], q)
                    or c.get("self") or c.get("joined")
                    or (not other and (c.get("near") or c.get("adjacent"))))
    ev = [c for c in pool if related(c) and _mech(c["quote"])
          and _has_mechanism(claim["text"], [c])][:3]
    # No fallback to an unrelated mechanism or to a condition-only sentence.
    _apply(claim, cands, {"status": "근거 확인" if ev else "자료 부족",
                        "evidence": [cands.index(c) for c in ev], "conditions": "",
                        "note": "규칙 기반 판정입니다. 원문 검토가 필요합니다."})
    claim.pop("llm_status", None)
    claim["inference_mode"] = "rules"


_FEAT_TOKEN = re.compile(r"(?:(?<![A-Za-z])AI(?![A-Za-z])|인공지능)\s*([A-Za-z가-힣][A-Za-z가-힣0-9]{0,9})(?:\s*([가-힣]{2,6}))?")
_PARTICLE = re.compile(r"(은|는|이|가|을|를|의|에|에서|로|으로|와|과|도|만|까지|부터|이나|나|라는|이라는|께서)$")
_FEAT_STOP = {"기술", "기능", "제품", "가전", "모델", "정보", "카드", "브랜드", "시대", "세상", "시스템", "탑재", "적용", "지원"}


_FEAT_TAIL = re.compile(r"^(절약|절전|쾌적|건조|세탁|냉방|진단|바람|수면|청정|탈수)")


def _norm_feat(text: str) -> str:
    """기능 이름 비교용 정규화. 'AI 세탁'과 'AI세탁', '인공지능 코스'와 '인공지능코스'를 같게 본다."""
    return re.sub(r"\s+", "", text).replace("인공지능", "AI").lower()


def _strip_particle(w: str) -> str:
    prev = None
    while w and w != prev:
        prev = w
        w = _PARTICLE.sub("", w)
    return w


def _feature_names(text: str) -> list[str]:
    """문구에 적힌 AI 기능 이름을 뽑는다(AI세탁, 인공지능 코스, AI 절약 모드, AI DD, AI바람 …).

    조사를 떼고, 'AI 기술'처럼 기능을 가리키지 않는 일반어는 뺀다.
    이름을 못 뽑으면 빈 목록이고, 그때는 이 경로를 쓰지 않는다."""
    out: list[str] = []
    for m in _FEAT_TOKEN.finditer(text):
        head = _strip_particle(m.group(1).strip())
        if not head or head in _FEAT_STOP or len(head) < 1:
            continue
        for cand in (head + " " + _strip_particle((m.group(2) or "").strip()), head):
            cand = cand.strip()
            if not cand:
                continue
            name = _norm_feat("AI" + cand)
            if len(name) >= 4 and name not in out:
                out.append(name)
        # 7회차: 'AI 에너지 절약'과 'AI 절약모드'처럼 수식어가 끼어든 이름도 같은 기능으로 묶는다(뒤 단어가 기능어일 때만)
        tail = _strip_particle((m.group(2) or "").strip())
        if tail and _FEAT_TAIL.match(tail):
            name = _norm_feat("AI" + tail)
            if name not in out:
                out.append(name)
    return out[:5]
