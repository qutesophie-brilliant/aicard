"""AI 표시 문구(주장) 추출. 판매페이지·사양표에서 'AI' 관련 표현이 들어간 문장을 뽑는다."""
import json
import re
from typing import Any

from . import llm
from ..schema import ITEM_KEYS

AI_PATTERN = re.compile(
    r"(AI|A\.I\.|인공지능|딥러닝|머신러닝|신경망|학습(?:해|하여|한|합니다|된)|스스로|자동(?:으로)?\s*(?:인식|판단|감지|학습|조절|분석)|"
    r"인식(?:해|하여|합니다|하고)|감지(?:해|하여|합니다)|분석(?:해|하여|합니다)|맞춤|추천|음성\s*(?:인식|명령)|비전|"
    r"알고리즘|스마트\s*(?:감지|인식|진단|케어)|LiDAR|라이다|3D\s*센서|ToF|Gemini|GPT|LLM|생성형)",
    re.I)

PROMPT = """아래는 한 제품의 판매페이지·사양표 문장들입니다. 각 문장 앞의 [번호]는 청크 번호입니다.

이 가운데 'AI' 또는 그에 준하는 기능(인공지능·학습·자동 인식·판단·분석·맞춤 등)을 소비자에게 표시·광고하는 문장을 모두 고르세요.
- 단순 사양 나열(크기·무게·색상)이나 가격·배송 안내는 제외합니다.
- 같은 내용을 반복하는 문장은 대표 문장 하나만 고릅니다.
- 각 주장을 7항목 중 하나로 분류합니다: {items}
  (functions: AI 기능·목적 / conditions: 작동 조건·한계 / data: 데이터 수집·처리·학습 /
   optout: 기능 해제·대체모드 / safety: 안전장치·업데이트·지원 / remedy: 문의·환불·보상 / evidence: 근거 자체를 제시하는 문장)
- 최대 {max_claims}개.

출력(JSON 배열): [{{"chunk": 번호, "text": "주장 문장 그대로", "item": "functions", "label": "6자 이내 짧은 기능명"}}]

문장들:
{body}
"""


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{c['chunk']}] {c['quote']}" for c in chunks)


def extract_claims(source_chunks: list[dict[str, Any]], max_claims: int = 14) -> list[dict[str, Any]]:
    """source_chunks: [{doc_id, chunk, quote, kind}] 판매페이지 계열만."""
    if not source_chunks:
        return []
    if llm.available():
        # 후보를 먼저 규칙으로 좁혀 토큰을 아낀다(AI 표현이 전혀 없는 문장은 뺌)
        cand = [c for c in source_chunks if AI_PATTERN.search(c["quote"])] or source_chunks
        cand = cand[:160]
        try:
            data = llm.ask_json(PROMPT.format(items=", ".join(ITEM_KEYS), max_claims=max_claims,
                                              body=_format_chunks(cand)))
        except llm.LLMError:
            return _rules_extract(source_chunks, max_claims)
        by_idx = {(c["doc_id"], c["chunk"]): c for c in source_chunks}
        # 청크 번호가 문서마다 겹칠 수 있어, 번호→실제 청크 매핑은 후보 목록 순서로 푼다
        idx_map: dict[int, dict[str, Any]] = {}
        for c in cand:
            idx_map.setdefault(c["chunk"], c)
        out = []
        for i, row in enumerate(data if isinstance(data, list) else [], 1):
            try:
                src = idx_map.get(int(row.get("chunk", -1)))
            except (TypeError, ValueError):
                src = None
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            if src is None:  # 번호가 틀렸으면 문장으로 되찾는다
                src = next((c for c in source_chunks if text[:15] in c["quote"]), source_chunks[0])
            item = row.get("item") if row.get("item") in ITEM_KEYS else "functions"
            out.append({"id": f"c{i}", "text": text, "item": item,
                        "label": str(row.get("label", ""))[:12],
                        "source": {"doc_id": src["doc_id"], "chunk": src["chunk"], "quote": src["quote"]}})
        return out
    return _rules_extract(source_chunks, max_claims)


def _rules_extract(source_chunks: list[dict[str, Any]], max_claims: int) -> list[dict[str, Any]]:
    out = []
    seen: set[str] = set()
    for c in source_chunks:
        q = c["quote"]
        if not AI_PATTERN.search(q):
            continue
        key = re.sub(r"\W", "", q)[:30]
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
        label = (AI_PATTERN.search(q).group(0)[:8])
        out.append({"id": f"c{len(out)+1}", "text": q[:160], "item": item, "label": label,
                    "source": {"doc_id": c["doc_id"], "chunk": c["chunk"], "quote": q}})
        if len(out) >= max_claims:
            break
    return out
