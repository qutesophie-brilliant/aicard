"""권익 5항목(조건·데이터·해제·안전·피해구제) 누락 탐지.

주장 단위가 아니라 문서 전체를 훑어 '이 항목에 해당하는 정보가 공개돼 있는가'를 본다.
"""
import re
from typing import Any

from . import llm
from .retrieve import ChunkIndex
from ..config import STATUSES
from ..schema import COVERAGE_ITEMS, ITEM_LABEL, ITEM_QUESTION

QUERIES = {
    "conditions": "작동 조건 환경 제한 한계 권장 사용 않는 경우 성능 저하 주의 주의사항 느려질 어두운 수 있습니다",
    "data": "개인정보 데이터 수집 저장 서버 클라우드 전송 학습 이용 카메라 영상 음성 지도 처리 위치 보관 기간 제3자 제공",
    "optout": "AI 기능 해제 끄기 OFF 비활성화 수동 모드 대체 일반 모드 설정에서",
    "safety": "오작동 안전장치 보호 기능 자동 정지 소프트웨어 업데이트 펌웨어 보안 패치 지원 기간 보증",
    "remedy": "고객센터 문의 전화 A/S 서비스센터 환불 교환 보상 피해 구제 보증 보증기간 수리 상담",
}

PROMPT = """소비자 정보카드의 한 항목을 채우려 합니다.

항목: {label}
소비자가 알고 싶은 것: {question}

제품의 공개자료에서 찾은 관련 문장들(앞의 [k]는 번호):
{cands}

판정:
- status: "근거 확인"(항목에 답이 되는 구체적 정보가 있음) / "조건 확인"(정보가 있고 조건·범위도 명시) /
  "자료 부족"(관련 문장이 없거나 일반적 문구뿐) / "추가 확인 필요"(문장끼리 어긋남)
- summary: 소비자에게 보여줄 2문장 이내 요약. 문서에 있는 사실만, 평가·권고 표현 없이. 자료 부족이면
  "공개자료에서 확인되지 않았습니다." 형태로.
- evidence: 근거 번호(최대 3개)

출력(JSON): {{"status":"...", "summary":"...", "evidence":[k,...]}}
"""


BATCH_PROMPT = """소비자 정보카드의 권익 항목 다섯 개를 채우려 합니다. 항목마다 제품 공개자료에서 찾은 관련 문장들이 있습니다.

항목별로 판정하세요.
- status: "근거 확인"(항목에 답이 되는 구체적 정보가 있음) / "조건 확인"(정보가 있고 조건·범위도 명시) /
  "자료 부족"(관련 문장이 없거나 일반적 문구뿐) / "추가 확인 필요"(문장끼리 어긋남)
- summary: 소비자에게 보여줄 2문장 이내 요약. 문서에 있는 사실만, 평가·권고 표현 없이, "~입니다/~습니다"체로.
  자료 부족이면 "공개자료에서 확인되지 않았습니다."
- evidence: 그 항목 후보 중 근거 번호(최대 3개)
- 제품 일반 구매의 보증·환불과 구독·렌탈·케어 서비스 계약의 해지·환불은 서로 다른 범위입니다.
  금액·기간·비율을 요약할 때 계약명과 적용 대상을 함께 적고, 서로 다른 제도의 조건을 합치지 마세요.
  적용 대상을 확인할 수 없으면 "추가 확인 필요"로 남기세요.

{blocks}

출력(JSON 객체, 항목 키를 키로): {{"conditions": {{"status":"...", "summary":"...", "evidence":[0]}}, "data": {{...}}, "optout": {{...}}, "safety": {{...}}, "remedy": {{...}}}}
"""


def fill_coverage(index: ChunkIndex) -> dict[str, dict[str, Any]]:
    cand_map = {key: index.search(QUERIES[key], k=8, prefer_kinds=("설명서", "사양표", "공공DB"))
                for key in COVERAGE_ITEMS}
    if llm.available():
        try:
            return _fill_llm_batch(cand_map)
        except llm.LLMError:
            items = {key: _fill_rules(key, cand_map[key]) for key in COVERAGE_ITEMS}
            for it in items.values():
                if it["status"] != "자료 부족":
                    it["status"] = "추가 확인 필요"
            return items
    return {key: _fill_rules(key, cand_map[key]) for key in COVERAGE_ITEMS}


def _fill_llm_batch(cand_map: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    blocks = []
    for key in COVERAGE_ITEMS:
        lines = "\n".join(f"  [{i}] ({c['kind']}) {c['quote']}" for i, c in enumerate(cand_map[key])) or "  (후보 없음)"
        blocks.append(f"### {key} — {ITEM_LABEL[key]}\n소비자가 알고 싶은 것: {ITEM_QUESTION[key]}\n후보:\n{lines}")
    data = llm.ask_json(BATCH_PROMPT.format(blocks="\n\n".join(blocks)), max_tokens=4000)
    if not isinstance(data, dict):
        raise llm.LLMError("묶음 판정 응답 형식이 올바르지 않습니다.")
    out = {}
    for key in COVERAGE_ITEMS:
        row = data.get(key)
        if not isinstance(row, dict):
            out[key] = _fill_rules(key, cand_map[key])
            continue
        cands = cand_map[key]
        status = row.get("status") if row.get("status") in STATUSES else "자료 부족"
        ev = _pack(cands, row.get("evidence"))
        if not ev:
            status = "자료 부족"
        summary = str(row.get("summary", "")).strip() or "공개자료에서 확인되지 않았습니다."
        if not ev:
            summary = "공개자료에서 확인되지 않았습니다."
        item = {"status": status, "summary": summary, "evidence": ev}
        out[key] = _guard_remedy_scope(item, cands) if key == "remedy" else item
    return out


def _pack(cands: list[dict[str, Any]], ks: list[Any]) -> list[dict[str, Any]]:
    out = []
    if not isinstance(ks, list):
        return out
    seen = set()
    for k in ks:
        # JSON booleans, negative indices and coerced floats/strings must not
        # silently select a different quotation.
        if type(k) is not int or not 0 <= k < len(cands) or k in seen:
            continue
        seen.add(k)
        c = cands[k]
        out.append({"doc_id": c["doc_id"], "chunk": c["chunk"], "quote": c["quote"], "kind": c["kind"]})
    return out[:3]


_CONTRACT_SCOPES = (
    re.compile(r"AI\s*올인원(?:\s*2\.0)?", re.I),
    re.compile(r"AI\s*스마트\s*요금제", re.I),
    re.compile(r"구독\s*케어"),
    re.compile(r"렌탈|렌털"),
)
_FINANCIAL_POLICY = re.compile(r"환불|반환|위약금|해약금|파손료|분실료")
_UNSCOPED_CANCELLATION = re.compile(r"보증\s*기간.{0,35}해지.{0,25}(?:전액|환불)|파손료.{0,15}분실료", re.S)


def _guard_remedy_scope(item, candidates):
    """Flag unqualified contract refunds; never assert product-price refunds.

    This guards new drafts only. Historical studies and stored human approvals
    are not regenerated or rewritten by this function.
    """
    if not item['evidence'] or item['status'] == '자료 부족':
        return item
    summary = item['summary']
    if not _FINANCIAL_POLICY.search(summary):
        return item
    source = '\n'.join(c['quote'] for c in candidates)
    scopes = [pattern for pattern in _CONTRACT_SCOPES if pattern.search(source)]
    # Two different named plans require an explicit comparison, not a blended
    # two-sentence summary. 구독케어 can be part of one AI 스마트 contract.
    multiple_plans = sum(bool(p.search(source)) for p in (_CONTRACT_SCOPES[0], _CONTRACT_SCOPES[1], _CONTRACT_SCOPES[3])) > 1
    quantified = re.search(r'전액|전부|\d+\s*%|\d+\s*원|파손료|분실료|위약금|해약금', summary)
    contract_named = re.search(r'구독|케어|렌탈|렌털', summary)
    missing_scope = scopes and not all(p.search(summary) for p in scopes) and (quantified or not contract_named)
    unqualified = not scopes and _UNSCOPED_CANCELLATION.search(summary)
    if multiple_plans or missing_scope or unqualified:
        return {**item, 'status': '추가 확인 필요',
                'summary': '환불·해지 안내의 적용 대상을 추가로 확인해야 합니다. 제품 구매와 구독·케어 계약의 조건을 구분해 원문을 확인해 주십시오.'}
    return item


_KEYWORDS = {k: re.compile("|".join(re.escape(w) for w in v.split())) for k, v in QUERIES.items()}


def _fill_rules(key: str, cands: list[dict[str, Any]]) -> dict[str, Any]:
    hits = [c for c in cands if len(_KEYWORDS[key].findall(c["quote"])) >= 2]
    if not hits:
        return {"status": "자료 부족", "summary": "공개자료에서 확인되지 않았습니다.", "evidence": []}
    ev = [{k2: c[k2] for k2 in ("doc_id", "chunk", "quote", "kind")} for c in hits[:3]]
    item = {"status": "근거 확인", "summary": hits[0]["quote"][:120], "evidence": ev}
    return _guard_remedy_scope(item, cands) if key == "remedy" else item
