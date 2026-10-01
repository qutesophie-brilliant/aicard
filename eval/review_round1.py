"""1차 검토(2026-10-01): AI 초안에 대한 검토 제안과 적용.

  python eval/review_round1.py --report        # 검토 제안표(마크다운) 출력
  python eval/review_round1.py --apply         # 카드에 반영 + eval/review_round1_labels.csv 저장

판단 기준은 eval/labeling_guide.md 를 따른다.
- set     : 확인상태를 바꾸거나 그대로 확정한다(검토자 판정으로 기록).
- exclude : AI 표시 문구가 아니거나 다른 문구의 조각·중복이라 카드에서 뺀다.
           (추출 오류로 기록해 평가에 쓴다.)
"""
import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.schema import ITEM_LABEL  # noqa: E402

K, J, B, C, Q = "keep", "근거 확인", "자료 부족", "조건 확인", "추가 확인 필요"
X = "exclude"

# (claim_id, 결정, 사유). 결정이 K면 AI 판정을 그대로 확정한다.
CLAIMS: dict[int, list[tuple[str, str, str]]] = {
    4: [  # 삼성 Bespoke AI 스팀 울트라
        ("c1", X, "KSA AI+ 인증 사실을 적은 문장임. 기능 표시 문구로 보지 않고 근거 자료로만 활용."),
        ("c2", C, "각주에 액체 인식 조건(15ml 또는 7cm×7cm 이하, 바닥 무늬·조도 등)이, 본문에 RGB 카메라+적외선 LED 방식 설명이 있음. AI 초안은 '오염 구역' 정의와 혼동해 잘못 분류함."),
        ("c3", K, "RGB 카메라 방식과 지원 모델·크기 한계는 확인됨. 다만 '121종' 수치의 목록과 시험 근거는 공개자료에 없음."),
        ("c4", Q, "판매 문구는 '액체와 고체 상태의 오염구역'을 인식한다고 하나 각주는 오염 구역을 '액체가 아닌, 고체 상태'로 정의함. 두 설명이 서로 맞지 않음."),
        ("c5", K, ""),
        ("c6", K, ""),
        ("c7", K, ""),
        ("c8", C, "각주의 dToF LiDAR 방식(초당 10회, 반경 8m, 360° 스캔)과 맵 식별 실패 조건을 AI 초안이 근거로 잡지 못함."),
        ("c9", K, ""),
    ],
    5: [  # LG 홈봇 AI 로니
        ("c1", K, "얼룩 인식의 작동 방식을 설명한 부분이 이미지여서 이번에 수집한 텍스트에는 들어오지 않음."),
        ("c2", K, "c1과 같음."),
        ("c3", J, "본문에 RGB 카메라와 센서로 상황과 장애물을 인식한다는 작동 방식 설명이 있음."),
        ("c4", X, "c3 문장에서 잘려 나온 중복 조각."),
        ("c5", K, ""),
        ("c6", X, "c5 문장에서 잘려 나온 중복 조각."),
        ("c7", K, "사양 비교표(라이다·카메라·3D 센서·낭떠러지 3cm)를 근거로 봄."),
        ("c8", X, "ThinQ 플랫폼 홍보 문구임. 이 제품의 AI 기능을 표시한 문장으로 보기 어려움."),
    ],
    6: [  # 로보락 S10 MaxV Ultra
        ("c1", X, "앱에서 물 분사량·진동을 수동으로 조절하는 기능이라 AI 표시에 해당하지 않음."),
        ("c2", C, "본문에 3중 구조광·RGB 카메라·측면 구조광 방식이 나오고, 각주에 너비·높이 3cm 물체까지 인식하며 인식 정확성은 환경에 따라 다르다는 조건이 붙어 있음."),
        ("c3", J, "측면 구조광으로 얇고 낮은 물체를 감지한다는 방식 설명이 문장 안에 들어 있음."),
        ("c4", B, "'습관까지 파악하는 인공지능'을 뒷받침하는 근거는 없음. AI 초안이 연결한 3cm 물체 근거는 다른 기능에 대한 것임."),
        ("c5", K, ""),
        ("c6", K, ""),
        ("c7", K, ""),
    ],
    7: [  # 드리미 X60 Ultra
        ("c1", X, "센서를 수납하는 구조에 대한 설명이라 AI 표시로 보지 않음."),
        ("c2", J, "듀얼 레이저 센서로 장애물 높이를 인식한다는 방식이 문장에 나와 있음. 수치(*)의 시험 조건 각주는 페이지에서 찾지 못함. AI 초안이 연결한 근거('매트 등…')는 이 문구와 관계없음."),
        ("c3", K, ""),
        ("c4", K, ""),
        ("c5", X, "스테이션 관리 기능이라 AI 표시로 보지 않음."),
        ("c6", Q, "'스마트 AI를 통한 맞춤형 카펫 관리'라고 표시한 바로 아래에 '사용 환경별 맞춤 청소모드를 수동 설정하면' 전환된다는 설명이 붙어 있음. AI 표시와 실제 설명이 서로 맞지 않음."),
        ("c7", X, "c6 문장에서 잘려 나온 중복 조각."),
    ],
    8: [  # 모바 P70 Pro Ultra
        ("c1", K, ""),
        ("c2", X, "c3와 내용이 겹치는 영문 소제목."),
        ("c3", K, ""),
        ("c4", K, ""),
        ("c5", Q, "각주에 '본 기능은 OTA 업데이트를 통해 제공될 예정'이라고 적혀 있음. 구매 시점에 쓸 수 있는 기능처럼 표시한 문구와 맞지 않음."),
        ("c6", X, "OCR 줄바꿈 때문에 c5 문장에서 잘려 나온 조각."),
        ("c7", X, "OCR 줄바꿈 때문에 다른 문장에서 잘려 나온 조각."),
    ],
    9: [  # 다이슨 스팟앤스크럽 Ai
        ("c1", X, "제품명이라 기능 표시 문구로 보지 않음."),
        ("c2", K, "각주 [6] 본문을 수집하지 못해 검토 때 원문 확인이 필요함."),
        ("c3", J, "24개 센서·듀얼 레이저 LiDAR·초당 7회 스캔이라는 방식이 문장에 나와 있음."),
        ("c4", X, "앱에서 맞춤 설정하는 기능이라 AI 표시에 해당하지 않음."),
    ],
    10: [  # 샤오미 S20 (대조군)
        ("c1", X, "'스마트', '맞춤 청소'만으로는 AI 표시로 보기 어려움. 대조군에서 나온 추출 오탐으로 기록함."),
        ("c2", X, "외부 스피커와 연동하는 음성 제어 기능으로 AI 표시는 없음. 대조군 추출 오탐으로 기록함."),
    ],
    11: [  # 삼성 Bespoke AI 스팀 (VR70)
        ("c1", X, "c2와 겹치는 주행 센서 소제목. AI 초안이 연결한 근거(장애물 감지 dToF 14cm)는 다른 센서에 대한 것임."),
        ("c2", C, "각주에 dToF LiDAR 방식(초당 10회, 반경 8m, 360°)과 '실사용 환경에 따라 차이가 날 수 있다'는 조건이 있음."),
        ("c3", X, "범퍼 센서가 물리적으로 닿아서 감지하는 방식이라 AI 표시가 아님."),
        ("c4", K, ""),
        ("c5", X, "주행 센서를 설명한 문장으로 AI 표시는 아님."),
        ("c6", X, "보안 기술에 대한 문장이라 권익 항목에서 따로 다룸."),
        ("c7", C, "와이파이 채널 상태 정보로 부재를 감지한다는 방식이 설명되어 있고, SmartThings 앱 설치·연동과 무선 네트워크 연결이 필요하다는 조건도 붙어 있음."),
        ("c8", X, "c7 문장에서 잘려 나온 중복 조각."),
        ("c9", K, ""),
        ("c10", X, "빅스비 브랜드를 소개하는 문장의 조각."),
        ("c11", K, ""),
    ],
    12: [  # LG 코드제로 AI 로보킹 올인원 — 수집 한계로 카드 전체 보류
        ("c1", X, "제품명이라 기능 표시 문구로 보지 않음."),
        ("c2", X, "ThinQ 플랫폼 홍보 문구."),
    ],
    13: [  # 에코백스 디봇 T90 프로 옴니
        ("c1", K, "AIVI 3D 4.0이 어떻게 작동하는지에 대한 설명이 없음."),
        ("c2", K, ""),
        ("c3", B, "'intelligent sensing and enhanced computing power'만으로는 작동 방식을 알 수 없음. 구역 직접 설정은 조건에 해당하지만 근거가 없어 자료 부족으로 판정함."),
    ],
}

# 권익 항목: (key, 상태, 소비자용 요약). 요약이 None이면 AI 요약을 그대로 둔다.
ITEMS: dict[int, list[tuple[str, str, str | None]]] = {
    4: [
        ("conditions", C, "장애물·액체·사람 인식은 크기, 바닥 무늬, 조도, 사물 모양 등에 따라 잘못 인식하거나 인식하지 못할 수 있다고 적혀 있습니다. AI 사물·액체 인식과 공간 인식은 이 모델에서만 지원합니다."),
        ("optout", B, "AI 기능을 끄는 방법과 끈 뒤에 어떻게 동작하는지는 공개자료에서 찾지 못했습니다."),
        ("safety", J, "UL 사물인터넷 보안 다이아몬드 등급, KISA 보안 인증, ETSI EN 303 645 인증을 받았고, Knox 보안칩을 적용했다고 밝힙니다. 소프트웨어 업데이트를 언제까지 제공하는지는 나와 있지 않습니다."),
    ],
    5: [
        ("conditions", B, "AI 기능이 어떤 조건에서 작동하고 어떤 한계가 있는지는 수집한 공개자료에서 찾지 못했습니다. 상세 설명 일부가 이미지여서 추가 확인이 필요합니다."),
        ("data", J, "LG Shield로 데이터 보안을 관리하고 개인정보보호위원회 PbD 인증을 받았다고 밝힙니다. 다만 어떤 데이터를 어디서 처리하는지는 나와 있지 않습니다."),
    ],
    6: [
        ("safety", B, "오작동에 대비한 안전장치와 소프트웨어 업데이트 제공 기간은 공개자료에서 찾지 못했습니다."),
    ],
    7: [
        ("conditions", C, "카펫 관리 기능은 사용 환경별 청소모드를 수동으로 설정해 두어야 자동으로 전환된다고 적혀 있습니다. 수치 옆 별표(*)가 가리키는 시험 조건은 페이지에서 찾지 못했습니다."),
    ],
    8: [
        ("conditions", C, "장애물 280종 이상 인식은 자사 내부 실험실 데이터 기준입니다. 털 길이가 3mm를 넘는 카펫은 앱에서 '카펫 지능형 청소 모드'를 켜야 합니다. 반려동물 케어 기능은 OTA 업데이트로 제공될 예정입니다."),
    ],
    9: [
        ("conditions", C, "앱 기능을 쓰려면 Wi-Fi와 iOS 16 이상 또는 Android 9 이상, 블루투스 4.0을 지원하는 기기가 필요합니다. 조건은 국가마다 다를 수 있습니다."),
        ("data", J, "AI 시스템은 로봇 내부의 처리 환경에서만 작동하고, 클라우드에 의존하거나 로봇에 이미지를 저장하지 않는다고 밝힙니다."),
        ("safety", J, "TUV Rheinland의 무선 장비 보안 요구사항(EN18031-1·2) 시험을 통과했다고 밝힙니다. 소프트웨어 업데이트를 언제까지 제공하는지는 나와 있지 않습니다."),
    ],
    11: [
        ("data", J, "암호화 키 같은 중요 데이터는 Knox Vault 보안칩에 따로 격리해 저장한다고 밝힙니다. 이 모델에는 카메라 인식 기능이 표시되어 있지 않습니다."),
        ("safety", J, "UL 사물인터넷 보안 다이아몬드 등급(VR70F00* 포함)을 받았고 Knox 보안 기술을 적용했다고 밝힙니다. 소프트웨어 업데이트를 언제까지 제공하는지는 나와 있지 않습니다."),
    ],
}

HOLD = {12: "수집 한계. 상세 설명이 화면에 보일 때만 그려지는 방식이라 본문 대부분을 받아 오지 못함. 크롬 '담기'로 다시 수집해야 함."}
NAMES = {}


def load(db_path: Path):
    con = sqlite3.connect(db_path)
    cards = {}
    for pid, name, js in con.execute(
            "select p.id, p.name, c.card_json from products p join cards c on c.product_id=p.id"):
        cards[pid] = json.loads(js)
        NAMES[pid] = name
    return con, cards


def report(cards) -> str:
    out = ["# 1차 검토 제안 (2026-10-01)", "",
           "AI 초안의 판정을 사람이 다시 본 결과다. '유지'는 AI 판정을 그대로 확정한다는 뜻이고, '제외'는 AI 표시 문구가 아니거나 다른 문구의 조각이라 카드에서 뺀다는 뜻이다.", ""]
    tot = {"유지": 0, "변경": 0, "제외": 0}
    for pid in sorted(CLAIMS):
        card = cards[pid]
        by = {c["id"]: c for c in card["claims"]}
        out.append(f"## {NAMES[pid]}" + (" (카드 보류)" if pid in HOLD else ""))
        if pid in HOLD:
            out += ["", f"> {HOLD[pid]}"]
        out.append("")
        out.append("| 문구 | AI 판정 | 제안 | 사유 |")
        out.append("|---|---|---|---|")
        for cid, dec, why in CLAIMS[pid]:
            c = by.get(cid)
            if not c:
                continue
            if dec == X:
                tag = "**제외**"; tot["제외"] += 1
            elif dec == K or dec == c["status"]:
                tag = "유지"; tot["유지"] += 1
            else:
                tag = f"**{dec}**"; tot["변경"] += 1
            txt = c["text"].replace("|", "/")
            if len(txt) > 70:
                txt = txt[:70].rstrip() + "…"
            out.append(f"| “{txt}” | {c['status']} | {tag} | {why} |")
        for key, st, summ in ITEMS.get(pid, []):
            old = card["items"][key]["status"]
            tag = "유지(요약만 수정)" if st == old else f"**{st}**(요약 수정)"
            out.append(f"| {ITEM_LABEL.get(key, key)} | {old} | {tag} | 요약: {summ or ''} |")
        out.append("")
    out.insert(4, f"표시 문구 {sum(tot.values())}건 가운데 유지 {tot['유지']}건, 상태 변경 {tot['변경']}건, 제외 {tot['제외']}건이다.")
    out.insert(5, "")
    return "\n".join(out)


def apply(con, cards, labels_path: Path) -> None:
    rows = []
    for pid, decisions in CLAIMS.items():
        card = cards[pid]
        by = {c["id"]: c for c in card["claims"]}
        keep_ids = []
        for cid, dec, why in decisions:
            c = by.get(cid)
            if not c:
                continue
            gold = "" if dec == X else (c["status"] if dec == K else dec)
            rows.append({"product_id": pid, "product": NAMES[pid], "claim_id": cid, "claim_text": c["text"],
                         "ai_status": c["status"], "decision": "exclude" if dec == X else "include",
                         "gold_status": gold,
                         "gold_evidence_quote": (c["evidence"][0]["quote"][:30] if gold in (J, C) and c.get("evidence") and dec == K else ""),
                         "labeler": "1차 검토(Claude 제안, 소민 확인)", "note": why})
            if dec == X:
                continue
            if dec != K:
                c["reviewer_status"] = dec
            c["reviewer_note"] = why
            keep_ids.append(cid)
        # 결정 목록에 없는 문구는 그대로 둔다
        card["claims"] = [c for c in card["claims"] if c["id"] in keep_ids or c["id"] not in {d[0] for d in decisions}]
        for key, st, summ in ITEMS.get(pid, []):
            card["items"][key]["status"] = st
            if summ:
                card["items"][key]["summary"] = summ
        con.execute("update cards set card_json=? where product_id=?", (json.dumps(card, ensure_ascii=False), pid))
        if pid in HOLD:
            con.execute("update products set status='held', reviewer_note=? where id=?", (HOLD[pid], pid))
        else:
            con.execute("update products set reviewer_note=? where id=?", ("1차 검토 반영(2026-10-01), 승인 대기 중.", pid))
    con.commit()
    with open(labels_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"반영 완료. 라벨 {len(rows)}건 저장: {labels_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "aicard.sqlite3"))
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    con, cards = load(Path(a.db))
    if a.report or not a.apply:
        print(report(cards))
    if a.apply:
        apply(con, cards, ROOT / "eval" / "review_round1_labels.csv")
