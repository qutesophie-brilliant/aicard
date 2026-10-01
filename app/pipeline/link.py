"""주장 ↔ 근거 연결과 4단계 확인상태 판정.

LLM 호출은 주장 전체를 한 번에 묶어서 보낸다(카드 한 장당 1회). 무료 등급 한도(하루 요청 수)를
아끼고 속도를 높이기 위해서다. 묶음 호출이 실패하면 주장별 규칙 판정으로 대신한다.
"""
import json
import re
from typing import Any

from . import llm
from .retrieve import ChunkIndex
from ..config import STATUSES

BATCH_PROMPT = """제품 판매페이지의 AI 표시 문구(주장)들과, 각 주장마다 설명서·사양표·각주에서 찾은 후보 문장들입니다.

각 주장에 대해 다음을 판정하세요.
- status: 다음 네 가지 중 하나
  * "근거 확인": 주장을 뒷받침하는 구체적 근거 문장(작동 방식·센서·측정 방법·시험 기관 등)이 후보에 있음
  * "조건 확인": 근거가 있고, 적용 조건이나 한계(환경·크기·지원 모델·사전 설정·인식 실패 사례)도 후보에 적혀 있음
  * "자료 부족": 후보 문장이 주장을 반복하거나 광고 문구일 뿐, 구체적 근거가 없음
  * "추가 확인 필요": 후보 문장들이 주장과 서로 어긋나거나 수치·조건이 다름
- evidence: 근거로 쓴 후보 번호 목록(최대 3개). '자료 부족'이면 빈 배열.
- conditions: 확인된 작동 조건·한계를 소비자가 읽을 한 문장으로. 없으면 "".
- note: 판정 이유 한 문장(검토자용). 우열·위법 표현 금지.

주의: 주장 자체를 다시 적은 문장(같은 광고 문구)은 근거가 아닙니다. 공개자료로 확인되지 않으면 '자료 부족'으로 둡니다.

{blocks}

출력(JSON 객체, 주장 id를 키로): {{"c1": {{"status":"...", "evidence":[0,2], "conditions":"...", "note":"..."}}, "c2": {{...}}}}
"""


def link_claims(claims: list[dict[str, Any]], index: ChunkIndex) -> list[dict[str, Any]]:
    cand_map: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        src = claim["source"]
        cands = index.search(claim["text"], k=6, exclude=(src["doc_id"], src["chunk"]),
                             prefer_kinds=("설명서", "사양표", "공공DB"))
        # 같은 판매페이지에서 주장 문구를 그대로 되풀이한 문장은 근거 후보에서 뺀다
        cand_map[claim["id"]] = [c for c in cands if not _is_restatement(c, claim["text"])]
        claim.setdefault("reviewer_status", None)
        claim.setdefault("reviewer_note", "")

    if llm.available() and claims:
        try:
            _link_llm_batch(claims, cand_map)
            return claims
        except llm.LLMError as e:
            for claim in claims:
                _link_rules(claim, cand_map[claim["id"]])
                claim["note"] = "AI 판정 호출이 실패해 규칙 판정으로 대신했습니다. 검토가 필요합니다. (" + str(e)[:80] + ")"
                if claim["status"] in ("근거 확인", "조건 확인"):
                    claim["status"] = "추가 확인 필요"
            return claims
    for claim in claims:
        _link_rules(claim, cand_map[claim["id"]])
    return claims


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
    contain = len(a & b) / min(len(a), len(b))
    limit = 0.5 if cand.get("kind") == "판매페이지" else 0.8
    return contain >= limit


def _link_llm_batch(claims: list[dict[str, Any]], cand_map: dict[str, list[dict[str, Any]]]) -> None:
    blocks = []
    for claim in claims:
        cands = cand_map[claim["id"]]
        lines = "\n".join(f"  [{i}] ({c['kind']}) {c['quote']}" for i, c in enumerate(cands)) or "  (후보 없음)"
        blocks.append(f"### {claim['id']}\n주장: \"{claim['text']}\"\n후보:\n{lines}")
    data = llm.ask_json(BATCH_PROMPT.format(blocks="\n\n".join(blocks)), max_tokens=6000)
    if not isinstance(data, dict):
        raise llm.LLMError("묶음 판정 응답 형식이 올바르지 않습니다.")
    for claim in claims:
        cands = cand_map[claim["id"]]
        row = data.get(claim["id"]) if isinstance(data.get(claim["id"]), dict) else None
        if row is None:
            _link_rules(claim, cands)
            claim["note"] = "AI 판정에서 이 문구가 빠져 규칙 판정으로 대신했습니다. 검토가 필요합니다."
            if claim["status"] in ("근거 확인", "조건 확인"):
                claim["status"] = "추가 확인 필요"
            continue
        _apply(claim, cands, row)


def _apply(claim: dict[str, Any], cands: list[dict[str, Any]], row: dict[str, Any]) -> None:
    status = row.get("status") if row.get("status") in STATUSES else "자료 부족"
    ev = []
    for k in row.get("evidence") or []:
        try:
            c = cands[int(k)]
            ev.append({"doc_id": c["doc_id"], "chunk": c["chunk"], "quote": c["quote"], "kind": c["kind"]})
        except (ValueError, IndexError, TypeError):
            continue
    if status in ("근거 확인", "조건 확인") and not ev:
        status = "자료 부족"
    claim.update(status=status, evidence=ev[:3], conditions=str(row.get("conditions", "") or ""),
                 note=str(row.get("note", "") or ""))


_COND = re.compile(r"(경우|환경|조건|제한|한계|않을 수|어려울 수|저하|권장|주의|제외|한해|한합니다|필요합니다)")
_EVID = re.compile(r"(센서|카메라|라이다|LiDAR|ToF|모터|프로세서|칩|알고리즘|dB|%|mm|cm|분|시간|회|단계|모델|방식|구조|측정|시험|인증)")


def _link_rules(claim: dict[str, Any], cands: list[dict[str, Any]]) -> None:
    """키 없는 데모 모드. 설명서·사양표 문장 중 수치·부품 표현이 있으면 근거로 본다."""
    ev = [c for c in cands if c["kind"] in ("설명서", "사양표", "공공DB") and _EVID.search(c["quote"])][:3]
    cond = next((c["quote"] for c in cands if _COND.search(c["quote"])), "")
    if ev and cond:
        status = "조건 확인"
    elif ev:
        status = "근거 확인"
    else:
        status = "자료 부족"
    claim.update(status=status,
                 evidence=[{k: c[k] for k in ("doc_id", "chunk", "quote", "kind")} for c in ev],
                 conditions=cond[:140],
                 note="규칙 기반 판정입니다. LLM 판정이 켜지면 문맥 판정으로 바뀝니다.")
