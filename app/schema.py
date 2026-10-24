"""정보카드 JSON 구조.

card = {
  "product": {"name","maker","model","category"},
  "claims": [  # AI 표시 문구(주장) 단위
     {"id":"c1", "text":"AI가 장애물을 인식해 피합니다", "item":"functions",
      "source": {"doc_id":3, "chunk":12, "quote":"..."},
      "basis_state": "있음|없음|어긋남",   # 축 1 — 뒷받침 설명이 있는가
      "conditions_noted": True,            # 축 2 — 작동 조건·한계도 적혀 있는가
      "status": "근거 확인|조건 확인|자료 부족|추가 확인 필요",  # 위 두 축에서 파생(status_from)
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
# 요약문·비교표에 쓰는 짧은 항목 이름
ITEM_SHORT = {"functions": "AI 기능", "evidence": "표시 근거", "conditions": "작동 조건", "data": "수집 데이터",
              "optout": "AI 기능 해제", "safety": "안전장치·업데이트", "remedy": "피해구제"}
# 라벨 윗부분의 항목 띠에 쓰는 더 짧은 이름
ITEM_TILE = {"conditions": "작동 조건", "data": "내 데이터", "optout": "AI 끄기", "safety": "보안·업데이트", "remedy": "문의·보증"}

# 모든 카드가 같은 순서로 답하는 질문 다섯 개. (key, 질문, 묶이는 항목)
QUESTIONS = [
    ("what", "무엇을 ‘AI’라고 하나요?", ["functions", "evidence", "conditions"]),
    ("data", "내 집과 관련된 정보는 어디로 전송되나요?", ["data"]),
    ("optout", "AI 기능을 끌 수 있나요?", ["optout"]),
    ("trouble", "문제가 생기면 어떻게 하나요?", ["safety", "remedy"]),
    ("public", "안전·전파 인증은 확인되나요?", []),
]
QUESTION_TEXT = {k: q for k, q, _ in QUESTIONS}

# 질문 2는 품목에 따라 묻는 말이 달라진다. 로봇청소기·공기청정기는 집 안을 돌아다니거나 집 안 공기를 재지만,
# 웨어러블·TV는 '내 집'이 맞지 않는다. 품목 이름에 아래 조각이 들어 있으면 그 문구를 쓰고, 없으면 기본 문구를 쓴다.
QUESTION_BY_CATEGORY = {
    "data": [
        (("로봇청소기", "청소기", "공기청정기", "에어컨", "제습기", "가습기", "냉풍기"), "내 집과 관련된 정보는 어디로 전송되나요?"),
        (("세탁기", "건조기", "세탁건조기", "식기세척기", "냉장고", "오븐", "인덕션"), "내 사용 기록은 어디로 전송되나요?"),
        (("TV", "티브이", "스피커", "디스플레이", "모니터"), "내 시청·사용 기록은 어디로 전송되나요?"),
        (("워치", "밴드", "이어폰", "반지", "링", "체중계", "혈압계"), "내 몸에서 잰 정보는 어디로 전송되나요?"),
    ],
}
DEFAULT_QUESTION_TEXT = dict(QUESTION_TEXT)


def question_text(key: str, category: str = "") -> str:
    """품목에 맞는 질문 문구. 맞는 품목이 없으면 기본 문구를 돌려준다."""
    for names, text in QUESTION_BY_CATEGORY.get(key, []):
        if any(n in (category or "") for n in names):
            return text
    return DEFAULT_QUESTION_TEXT[key]

# 작동 설명의 종류 — 'AI'라고 적은 문구를 회사가 무엇으로 설명했는가(검토자가 매긴다).
# AI 여부를 판정하는 게 아니라 작동 설명의 내용을 그대로 옮기는 꼬리표다.
BASIS_KINDS = [
    ("learn", "학습·모델로 설명", "학습, 모델, 데이터, 알고리즘 같은 AI 요소를 들어 설명"),
    ("vision", "카메라 인식으로 설명", "카메라·영상 인식으로 설명하되 학습이나 모델 언급은 없음"),
    ("sensor", "센서·규칙으로 설명", "거리·초음파·적외선·LiDAR·신호 같은 센서나 규칙으로 설명"),
    ("service", "외부 서비스로 설명", "음성비서·클라우드 AI·제3자 플랫폼의 이름을 들어 설명하고, 기기 안에서 어떻게 작동하는지는 적혀 있지 않음"),
    ("none", "조건·한계만 적음", "조건·한계·지원 모델 같은 내용만 있고 어떻게 작동하는지는 적혀 있지 않음"),
    ("unknown", "설명 종류 미확정", "작동 설명은 있지만 설명 종류가 아직 분류되지 않음"),
]
BASIS_LABEL = {k: label for k, label, _ in BASIS_KINDS}
BASIS_HELP = {k: h for k, _, h in BASIS_KINDS}


# 항목 4·6의 추가 표시. 검토자가 공개자료를 보고 고르며, 비워 두면 카드에 나오지 않는다.
# 항목 4 — 개인정보 보호법 제28조의8(국외 이전)은 국외 이전 사실을 알리도록 한다.
DATA_SERVERS = [
    ("kr", "국내에서 처리한다고 적혀 있습니다"),
    ("abroad", "국외 서버로 보낸다고 적혀 있습니다"),
    ("device", "기기 안에서만 처리한다고 적혀 있습니다"),
    ("none", "어디서 처리하는지 공개자료에 없습니다"),
]
DATA_SERVER_LABEL = {k: v for k, v in DATA_SERVERS}
# 항목 6 — EU 사이버복원력법 제13조는 지원 기간을 연수로 밝히도록 한다. '제공한다'는 말만으로는 기간을 알 수 없다.
SUPPORT_PERIODS = [
    ("years", "지원 기간이 햇수로 적혀 있습니다"),
    ("vague", "업데이트를 제공한다고만 적혀 있고 기간은 없습니다"),
    ("none", "업데이트·지원에 대한 설명이 공개자료에 없습니다"),
]
SUPPORT_PERIOD_LABEL = {k: v for k, v in SUPPORT_PERIODS}


def basis_counts(card: dict[str, Any], scope: str = "kr") -> dict[str, int]:
    """작동 설명의 종류 집계. scope='kr'은 국내 공개자료 기준, 'global'은 회사의 해외 공식 자료까지 더한 기준."""
    out = {k: 0 for k, _, _ in BASIS_KINDS}
    for c in card.get("claims", []):
        if effective_status(c) not in ("근거 확인", "조건 확인"):
            continue
        k = c.get("basis_kind")
        if scope == "global" and c.get("basis_kind_global"):
            k = c["basis_kind_global"]
        out[k if k in out else "unknown"] += 1
    return out


def global_extra(card: dict[str, Any]) -> list[dict[str, Any]]:
    """국내 자료에는 없고 회사 해외 공식 자료에만 있는 설명이 붙은 문구."""
    return [c for c in card.get("claims", []) if c.get("basis_kind_global") and c.get("basis_kind_global") != c.get("basis_kind")]


def heads_up(card: dict[str, Any], limit: int = 4) -> list[dict[str, str]]:
    """'사기 전에 알아 둘 것' — 표시와 설명이 어긋난 문구, 빠진 권익 정보를 최대 limit개 고른다."""
    out: list[dict[str, str]] = []
    for c in card.get("claims", []):
        if effective_status(c) == "추가 확인 필요":
            ct = (c.get("text") or "").strip()
            ct = ct[:60] + "…" if len(ct) > 60 else ct
            text = f"“{ct}” — 본문과 각주의 적용 범위·조건·지원 시점을 더 확인해야 합니다."
            out.append({"kind": "s4", "text": text, "anchor": f"claim-{c['id']}"})
    bc = basis_counts(card)
    linked = sum(1 for c in card.get("claims", []) if effective_status(c) in ("근거 확인", "조건 확인"))
    if linked and bc["learn"] == 0 and bc["unknown"] == 0 and (bc["sensor"] or bc["vision"] or bc["service"] or bc["none"]):
        how = []
        if bc["sensor"]:
            how.append(f"센서·규칙 {bc['sensor']}개")
        if bc["vision"]:
            how.append(f"카메라 인식 {bc['vision']}개")
        if bc["service"]:
            how.append(f"외부 서비스 {bc['service']}개")
        if bc["none"]:
            how.append(f"조건·한계만 제시 {bc['none']}개")
        text = f"‘작동 설명 있음’ {linked}개의 설명 종류는 " + ", ".join(how) + "입니다. 학습이나 모델을 언급한 설명은 없습니다."
        out.append({"kind": "s3", "text": text, "anchor": "q-what"})
    extra = global_extra(card)
    if extra:
        out.append({"kind": "s3", "text": f"문구 {len(extra)}개는 국내 자료에 없는 설명이 회사의 해외 영문 자료에 있습니다.", "anchor": f"claim-{extra[0]['id']}"})
    items = card.get("items", {})
    if card.get('claims') and items.get("optout", {}).get("status") == "자료 부족":
        out.append({"kind": "s3", "text": "AI 기능을 끄는 방법은 이 카드에 사용한 자료에서 찾지 못했습니다.", "anchor": "q-optout"})
    if items.get("data", {}).get("status") == "자료 부족":
        out.append({"kind": "s3", "text": "데이터 수집·처리 위치에 관한 설명은 이 카드에 사용한 자료에서 찾지 못했습니다.", "anchor": "q-data"})
    return out[:limit]


def topic_particle(word: str) -> str:
    """'은/는' 조사를 고른다."""
    ch = word.strip()[-1:]
    if not ch or not ("가" <= ch <= "힣"):
        return "는"
    return "은" if (ord(ch) - 0xAC00) % 28 else "는"


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


# 확인상태의 두 축. 네 가지 상태 이름은 이 두 축의 조합에서 나온다.
# 축을 나눈 이유: '조건 확인'은 '근거 확인'에 조건·한계 문장이 하나 더 붙은 상태라서
# 네 상태가 서로 배타적이지 않았다. 라벨러와 파이프라인이 같은 문구를 두 이름으로 적을 수 있어
# 4분류 정확도가 실제 오류보다 낮게 나왔다. 화면에 보이는 이름은 그대로 두고 자료구조만 나눈다.
BASIS_STATES = ["있음", "없음", "어긋남"]  # 표시 문구를 뒷받침하는 작동 설명
BASIS_STATE_HELP = {
    "있음": "표시 문구를 뒷받침하는 작동 설명을 공개자료에서 찾았음",
    "없음": "공개자료에서 뒷받침 설명을 찾지 못했음(거짓 표시라는 뜻은 아님)",
    "어긋남": "표시 문구와 회사의 작동 설명이 맞지 않음",
}


def status_from(basis: str, conditions_noted: bool) -> str:
    """두 축에서 확인상태 이름을 만든다."""
    if basis == "어긋남":
        return "추가 확인 필요"
    if basis == "있음":
        return "조건 확인" if conditions_noted else "근거 확인"
    return "자료 부족"


def axes_from_status(status: str) -> tuple[str, bool]:
    """예전 카드처럼 상태 이름만 있는 경우 두 축을 되돌린다."""
    if status == "추가 확인 필요":
        return "어긋남", False
    if status in ("근거 확인", "조건 확인"):
        return "있음", status == "조건 확인"
    return "없음", False


def axes_of(claim: dict[str, Any]) -> tuple[str, bool]:
    """검토자 판정이 있으면 그것을, 없으면 저장된 두 축을, 둘 다 없으면 상태 이름에서 되돌린 값을 쓴다."""
    if claim.get("reviewer_status"):
        return axes_from_status(claim["reviewer_status"])
    if claim.get("basis_state") in BASIS_STATES:
        return claim["basis_state"], bool(claim.get("conditions_noted"))
    return axes_from_status(claim.get("status") or "자료 부족")


def effective_status(claim: dict[str, Any]) -> str:
    """검토자가 상태를 바꿨으면 그 값을, 아니면 AI 판단을 쓴다."""
    basis, cond = axes_of(claim)
    return status_from(basis, cond)


def summarize(card: dict[str, Any]) -> dict[str, Any]:
    counts = {s: 0 for s in STATUSES}
    for c in card.get("claims", []):
        s = effective_status(c)
        counts[s] = counts.get(s, 0) + 1
    covered = sum(1 for k in COVERAGE_ITEMS
                  if card["items"].get(k, {}).get("status") in ("근거 확인", "조건 확인"))
    n = len(card.get("claims", []))
    linked = counts["근거 확인"] + counts["조건 확인"]
    lines: list[str] = []
    if n == 0:
        lines.append(card.get("zero_claim_scope") or "대조한 자료에서 AI 표시 문구를 찾지 못했습니다. 제품에 AI 기능이 없다는 판정은 아닙니다.")
    else:
        lines.append(f"‘AI’라고 표시한 문구 {n}개 중 {linked}개에 회사가 작동 설명을 적어 두었습니다.")
        rest = []
        if counts["자료 부족"]:
            rest.append(f"{counts['자료 부족']}개는 공개자료에서 설명을 찾지 못했습니다")
        if counts["추가 확인 필요"]:
            rest.append(f"{counts['추가 확인 필요']}개는 본문과 각주를 더 확인해야 합니다")
        if rest:
            lines.append(". ".join(rest) + ".")
    missing = [ITEM_SHORT[k] for k in COVERAGE_ITEMS
               if card["items"].get(k, {}).get("status") not in ("근거 확인", "조건 확인")]
    if covered == len(COVERAGE_ITEMS):
        lines.append("소비자 권익 항목 5가지가 모두 공개돼 있습니다.")
    elif covered == 0:
        lines.append("소비자 권익 항목 5가지를 공개자료에서 찾지 못했습니다.")
    else:
        joined = ", ".join(missing)
        lines.append(f"소비자 권익 항목 5가지 중 {covered}가지가 공개돼 있습니다. "
                     f"{joined}{topic_particle(missing[-1])} 찾지 못했습니다.")
    return {
        "n_claims": n,
        "counts": counts,
        "linked": linked,
        "items_covered": covered,
        "items_total": len(COVERAGE_ITEMS),
        "lines": lines,
        "short": lines[0],
    }
