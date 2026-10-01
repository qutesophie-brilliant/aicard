"""쉬운 말 버전. 고령자·저문해 소비자용. 짧은 문장, 한자어·외래어 최소화."""
from typing import Any

from . import llm
from ..schema import COVERAGE_ITEMS, ITEM_LABEL, effective_status

PROMPT = """아래 정보카드 내용을 '쉬운 말'로 바꿔 주세요. 읽는 사람은 70대 어르신이거나 글 읽기가 어려운 분입니다.

규칙:
- 한 문장은 25자 안팎. 한 항목은 두 문장까지.
- 어려운 말은 풀어 씁니다. 예) '비활성화' → '끄기', '펌웨어 업데이트' → '기계 속 프로그램 고치기', '데이터' → '정보'.
- 사실만 씁니다. "좋다/나쁘다/조심하라" 같은 평가·권고는 쓰지 않습니다.
- 자료가 없는 항목은 "회사가 올린 자료에는 이 내용이 없어요."로 씁니다.
- headline: 이 제품의 AI 표시를 한 줄로 설명. 예) "장애물을 알아보고 피한다고 적혀 있어요. 근거 자료도 있어요."

입력:
제품: {name}
AI 표시 문구와 확인상태:
{claims}
항목별 요약:
{items}

출력(JSON): {{"headline":"...", "items": {{"functions":"...", "evidence":"...", "conditions":"...", "data":"...", "optout":"...", "safety":"...", "remedy":"..."}}}}
"""

_STATUS_EASY = {
    "근거 확인": "근거 자료가 있어요",
    "조건 확인": "근거와 조건이 함께 적혀 있어요",
    "자료 부족": "회사가 올린 자료에는 근거가 없어요",
    "추가 확인 필요": "자료끼리 달라서 사람이 다시 봐야 해요",
}


def make_easy(card: dict[str, Any]) -> dict[str, Any]:
    if llm.available():
        claims = "\n".join(f"- {c['text']} → {effective_status(c)}" for c in card["claims"][:10])
        items = "\n".join(f"- {ITEM_LABEL[k]}: [{card['items'][k]['status']}] {card['items'][k]['summary']}"
                          for k in COVERAGE_ITEMS)
        try:
            data = llm.ask_json(PROMPT.format(name=card["product"]["name"], claims=claims or "(없음)", items=items),
                                max_tokens=1200)
        except llm.LLMError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("items"), dict):
            return {"headline": str(data.get("headline", "")), "items": {k: str(v) for k, v in data["items"].items()}}
    return _rules_easy(card)


def _rules_easy(card: dict[str, Any]) -> dict[str, Any]:
    claims = card["claims"]
    n_ok = sum(1 for c in claims if effective_status(c) in ("근거 확인", "조건 확인"))
    headline = (f"AI 기능이라고 적힌 문구가 {len(claims)}개 있어요. 그중 {n_ok}개는 근거 자료가 있어요."
                if claims else "AI 기능이라고 적힌 문구를 찾지 못했어요.")
    items = {
        "functions": "회사가 AI라고 적은 기능: " + ", ".join(c.get("label") or c["text"][:12] for c in claims[:4]) if claims else "AI라고 적은 기능이 없어요.",
        "evidence": f"{len(claims)}개 중 {n_ok}개는 근거 자료가 있어요." if claims else "",
    }
    for k in COVERAGE_ITEMS:
        it = card["items"][k]
        items[k] = it["summary"] if it["status"] != "자료 부족" else "회사가 올린 자료에는 이 내용이 없어요."
    return {"headline": headline, "items": items}
