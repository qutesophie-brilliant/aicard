"""정보카드 JSON 구조.

card = {
  "product": {"name","maker","model","category"},
  "claims": [  # AI 표시 문구(주장) 단위
     {"id":"c1", "text":"AI가 장애물을 인식해 피합니다", "item":"functions",
      "source": {"doc_id":3, "chunk":12, "quote":"..."},
      "status": "근거 확인|조건 확인|자료 부족|추가 확인 필요",
      "evidence": [{"doc_id":4,"chunk":7,"quote":"..."}],
      "conditions": "조명이 어두운 환경에서는 인식률이 낮아질 수 있음",
      "note": "AI 판단 메모(검토자용)",
      "reviewer_status": null, "reviewer_note": ""}
  ],
  "items": {  # 7항목 요약. functions/evidence 는 claims 로부터 계산
     "conditions": {"status": "...", "summary": "...", "evidence":[...]},
     "data": {...}, "optout": {...}, "safety": {...}, "remedy": {...}
  },
  "easy": {  # 쉬운 말 버전
     "headline": "...", "items": {"functions":"...", ...}
  },
  "public_db": {  # 공공 DB 교차확인(수동 입력 또는 API)
     "kc": {"found": null, "number":"", "url":""},
     "rra": {"found": null, "number":"", "url":""},
     "energy": {"found": null, "grade":"", "url":""},
     "recall": {"found": null, "detail":"", "url":""}
  },
  "checked_at": "YYYY-MM-DD", "sources": [{"doc_id":..,"kind":..,"title":..,"url":..}]
}
"""
from typing import Any

from .config import CARD_ITEMS, STATUSES

ITEM_KEYS = [k for k, _, _ in CARD_ITEMS]
ITEM_LABEL = {k: label for k, label, _ in CARD_ITEMS}
ITEM_QUESTION = {k: q for k, _, q in CARD_ITEMS}
# claims 로 채워지는 항목
CLAIM_DERIVED = {"functions", "evidence"}
# 문서 전체를 훑어 채우는 항목
COVERAGE_ITEMS = [k for k in ITEM_KEYS if k not in CLAIM_DERIVED]


def empty_public_db() -> dict[str, Any]:
    return {
        "kc": {"found": None, "number": "", "url": ""},
        "rra": {"found": None, "number": "", "url": ""},
        "energy": {"found": None, "grade": "", "url": ""},
        "recall": {"found": None, "detail": "", "url": ""},
    }


def new_card(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "product": {k: product.get(k, "") for k in ("name", "maker", "model", "category")},
        "claims": [],
        "items": {k: {"status": "자료 부족", "summary": "", "evidence": []} for k in COVERAGE_ITEMS},
        "easy": {"headline": "", "items": {}},
        "public_db": empty_public_db(),
        "checked_at": "",
        "sources": [],
    }


def effective_status(claim: dict[str, Any]) -> str:
    """검토자가 상태를 바꿨으면 그 값을, 아니면 AI 판단을 쓴다."""
    return claim.get("reviewer_status") or claim.get("status") or "자료 부족"


def summarize(card: dict[str, Any]) -> dict[str, Any]:
    counts = {s: 0 for s in STATUSES}
    for c in card.get("claims", []):
        s = effective_status(c)
        counts[s] = counts.get(s, 0) + 1
    covered = sum(1 for k in COVERAGE_ITEMS
                  if card["items"].get(k, {}).get("status") in ("근거 확인", "조건 확인"))
    return {
        "n_claims": len(card.get("claims", [])),
        "counts": counts,
        "items_covered": covered,
        "items_total": len(COVERAGE_ITEMS),
    }
