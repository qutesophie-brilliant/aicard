"""1차 검토 보완(2026-10-01): 판정을 바꾼 문구와 권익 항목의 근거 문장, 조건 문장, 쉬운 말 문장을 고친다.

  python eval/review_round1_evidence.py --db data/aicard.sqlite3 [--dry]

근거는 수집한 원문 조각(document id, chunk 번호)을 직접 지정한다. LLM을 부르지 않는다.
"""
import argparse
import csv
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("review_round1", HERE / "review_round1.py")
R1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R1)
REASON = {(pid, cid): why for pid, rows in R1.CLAIMS.items() for cid, _, why in rows}

# 문구: (product_id, claim_id) -> 근거 조각 목록, 조건 문장, 메모
CLAIMS = {
    (4, "c2"): ([(6, 26), (6, 27), (7, 21)],
                "액체의 양이나 크기가 너무 작거나(15ml 또는 7cm x 7cm 이하) 바닥 무늬, 어두운 바닥, 조도, 반사광 등에 따라 오인식하거나 인식하지 못할 수 있습니다.",
                "RGB 카메라와 적외선 LED 광으로 액체를 감지한다는 방식과 인식 한계가 함께 적혀 있습니다."),
    (4, "c4"): ([(7, 30), (7, 20)],
                "판매 문구는 액체와 고체 상태의 오염구역을 인식한다고 하지만, 각주는 오염 구역을 '액체가 아닌 고체 상태'로 정의합니다.",
                "판매 문구와 각주의 정의가 서로 맞지 않습니다."),
    (4, "c8"): ([(7, 40), (7, 39), (7, 61)],
                "맵 구조가 서로 비슷하거나 센서가 감지할 물체가 적으면 위치를 식별하지 못하거나 올바른 맵을 불러오지 못할 수 있습니다.",
                "dToF LiDAR 센서가 초당 10번 회전하며 반경 8m를 스캔한다는 방식이 각주에 있습니다."),
    (5, "c3"): ([(8, 6)], "",
                "RGB 카메라와 센서로 상황과 장애물을 인식한다는 방식이 본문에 있습니다."),
    (6, "c2"): ([(10, 7), (11, 23), (11, 24)],
                "너비 3cm, 높이 3cm 물체까지 인식한다는 것은 제조사 내부 테스트 기준이며, 인식 정확성은 환경에 따라 달라질 수 있습니다.",
                "3중 구조광, RGB 카메라, 측면 구조광으로 장애물을 인식한다는 방식이 적혀 있습니다."),
    (6, "c3"): ([(10, 7), (10, 10)], "",
                "측면 구조광으로 벽과 가구 쪽의 얇고 낮은 물체를 감지한다는 방식이 적혀 있습니다."),
    (6, "c4"): ([], "", "'습관까지 파악하는 인공지능'을 뒷받침하는 설명을 찾지 못했습니다."),
    (7, "c2"): ([(12, 8)], "숫자 옆 별표(*)가 가리키는 시험 조건은 페이지에서 찾지 못했습니다.",
                "듀얼 레이저 센서로 장애물 높이를 인식한다는 방식이 문장에 있습니다."),
    (7, "c6"): ([(12, 29), (12, 30)],
                "카펫 관리는 '사용 환경별 맞춤 청소모드를 수동 설정하면' 전환된다고 적혀 있습니다.",
                "'스마트 AI' 표시와 수동 설정이 필요하다는 설명이 서로 맞지 않습니다."),
    (8, "c5"): ([(18, 113), (18, 120)],
                "이 기능은 OTA 업데이트를 통해 제공될 예정이며, 실제 성능은 환경과 반려동물의 활동량에 따라 달라질 수 있습니다.",
                "구매 시점에 쓸 수 있는 기능처럼 표시했지만, 각주에는 업데이트로 제공될 예정이라고 적혀 있습니다."),
    (9, "c3"): ([(16, 5)], "",
                "24개 센서와 듀얼 레이저 LiDAR 센서가 초당 7회 스캔한다는 방식이 문장에 있습니다."),
    (11, "c2"): ([(22, 20), (22, 22)],
                 "내부 시험 기준이며 실사용 환경에 따라 차이가 날 수 있습니다.",
                 "dToF LiDAR 센서가 초당 10번 회전하며 반경 8m를 스캔한다는 방식이 각주에 있습니다."),
    (11, "c7"): ([(21, 26), (21, 27), (22, 17), (22, 18)],
                 "SmartThings 기능을 쓰려면 앱 설치와 연동, 무선 네트워크 연결이 필요합니다.",
                 "와이파이 채널 상태 정보로 집에 사람이 없는지 감지한다는 방식이 적혀 있습니다."),
    (13, "c3"): ([], "", "'intelligent sensing and enhanced computing power'만으로는 작동 방식을 알 수 없습니다."),
}

# 권익 항목: (product_id, key) -> 근거 조각 목록, (요약을 바꿀 때만) 새 요약
ITEMS = {
    (4, "conditions"): ([(7, 37), (7, 21), (7, 19), (7, 29)], None),
    (4, "optout"): ([], None),
    (4, "safety"): ([(7, 6), (7, 46), (7, 47), (7, 49)], None),
    (5, "conditions"): ([], None),
    (5, "data"): ([(9, 8), (9, 10)], None),
    (6, "safety"): ([], None),
    (7, "conditions"): ([(12, 30)], None),
    (8, "conditions"): ([(18, 111), (18, 118), (18, 113)],
                        "장애물 280종 이상 인식은 자사 내부 실험실 데이터 기준입니다. 털 길이가 3mm를 넘는 카펫은 앱에서 '카펫 지능형 청소 모드'를 켜야 합니다. 반려동물 케어 기능은 OTA 업데이트로 제공될 예정입니다."),
    (9, "conditions"): ([(17, 4), (17, 6), (17, 7), (17, 3)], None),
    (9, "data"): ([(17, 0)], None),
    (9, "safety"): ([(17, 2)], None),
    (11, "data"): ([(22, 37)], None),
    (11, "safety"): ([(22, 8), (22, 10), (22, 34)], None),
}

# 공통 문구 정리
SUMMARY_REPLACE = {"공개자료에서 확인되지 않았습니다.": "공개자료에서 찾지 못했습니다."}

NOT_FOUND = "회사가 공개한 자료에서 찾지 못했어요."
EASY = {
    4: ("AI로 사물과 액체를 알아본다고 적혀 있어요. 대부분 조건이 함께 적혀 있지만, 서로 맞지 않는 설명도 하나 있어요.", {
        "functions": "카메라로 물건과 액체를 알아보고 피한다고 해요. 집 구조를 알아보고 방 이름도 붙여 준대요.",
        "evidence": "8개 문구 중 6개는 조건이 함께 적혀 있어요. 1개는 근거를 찾지 못했고, 1개는 설명이 서로 맞지 않아요.",
        "conditions": "물건 크기, 바닥 무늬, 밝기에 따라 잘못 알아볼 수 있어요. 이 기능들은 이 모델에만 있어요.",
        "data": "카메라는 물건을 알아보고 집을 비춰 보는 데 써요. 녹화한 영상은 24시간 뒤에 지운대요.",
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "보안 인증을 여러 개 받았다고 해요. 업데이트를 언제까지 해 주는지는 나와 있지 않아요.",
        "remedy": "1년 동안 품질을 보증해요. 모터는 10년 동안 무료로 고쳐 줘요. 문의는 1588-3366이에요.",
    }),
    5: ("AI로 얼룩과 위험한 곳을 알아본다고 적혀 있어요. 근거가 있는 문구는 일부예요.", {
        "functions": "카메라와 센서로 얼룩과 장애물을 알아보고 피한다고 해요.",
        "evidence": "5개 문구 중 2개만 근거가 있어요. 나머지 3개는 근거를 찾지 못했어요. 설명 일부가 그림으로만 되어 있어요.",
        "conditions": "AI 기능이 언제 잘 안 되는지는 " + NOT_FOUND,
        "data": "LG Shield로 정보를 지킨다고 해요. 어떤 정보를 어디서 쓰는지는 나와 있지 않아요.",
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "고장에 대비한 장치와 업데이트 기간은 " + NOT_FOUND,
        "remedy": "고장 나면 서비스센터에서 확인을 받아야 해요. 문의는 1544-7777이에요.",
    }),
    6: ("AI로 장애물과 반려동물을 알아본다고 적혀 있어요. 근거를 찾지 못한 문구도 여럿 있어요.", {
        "functions": "빛과 카메라로 장애물을 알아보고 피한다고 해요. 반려동물도 알아본대요.",
        "evidence": "6개 문구 중 3개는 근거나 조건이 있어요. 나머지 3개는 근거를 찾지 못했어요.",
        "conditions": "집 환경과 사용 조건에 따라 성능이 달라질 수 있어요.",
        "data": "카메라 정보는 기기 안에서 처리한대요. 반려동물 사진 찍기는 직접 켜야 해요.",
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "고장에 대비한 장치와 업데이트 기간은 " + NOT_FOUND,
        "remedy": "문의와 환불 방법은 " + NOT_FOUND,
    }),
    7: ("AI로 장애물과 카펫을 알아본다고 적혀 있어요. 카펫 기능은 직접 설정해야 한대요.", {
        "functions": "레이저로 장애물 높이를 알아보고 문턱을 넘는다고 해요. 먼지와 얼룩도 알아본대요.",
        "evidence": "4개 문구 중 1개만 근거가 있어요. 카펫 기능은 'AI'라고 했지만 직접 설정해야 한다고 적혀 있어요.",
        "conditions": "카펫 기능은 청소 방식을 직접 정해 두어야 바뀌어요. 숫자 옆 별표의 설명은 찾지 못했어요.",
        "data": "어떤 정보를 모으는지는 " + NOT_FOUND,
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "고장에 대비한 장치와 업데이트 기간은 " + NOT_FOUND,
        "remedy": "제품 문의는 1544-1201로 하면 돼요.",
    }),
    8: ("AI로 장애물과 반려동물을 알아본다고 적혀 있어요. 반려동물 기능은 나중에 업데이트로 나올 예정이에요.", {
        "functions": "카메라로 장애물 280가지를 알아본다고 해요. 카펫과 반려동물도 알아본대요.",
        "evidence": "4개 문구 중 2개는 조건이 함께 적혀 있어요. 반려동물 기능은 지금 바로 쓸 수 있는지 더 확인해야 해요.",
        "conditions": "장애물 숫자는 회사 실험실에서 잰 결과예요. 털이 긴 카펫은 앱에서 카펫 모드를 켜야 해요.",
        "data": "어떤 정보를 모으는지는 " + NOT_FOUND,
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "고장에 대비한 장치와 업데이트 기간은 " + NOT_FOUND,
        "remedy": "공식 경로로 사고 구매 증빙이 있어야 보증과 수리를 받을 수 있어요.",
    }),
    9: ("AI로 얼룩을 찾아 청소한다고 적혀 있어요. 사진을 저장하지 않는다고 밝혀요.", {
        "functions": "초록 불빛으로 얼룩을 비추고, 센서로 장애물을 피한다고 해요.",
        "evidence": "2개 문구 중 1개는 근거가 있어요. 얼룩을 분석한다는 문구는 근거를 찾지 못했어요.",
        "conditions": "앱을 쓰려면 와이파이와 지원되는 스마트폰이 필요해요. 나라마다 다를 수 있어요.",
        "data": "AI는 청소기 안에서만 작동하고, 클라우드를 쓰거나 사진을 저장하지 않는다고 해요.",
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "보안 시험을 통과했다고 해요. 업데이트를 언제까지 해 주는지는 나와 있지 않아요.",
        "remedy": "2년 동안 품질을 보증해요. 평일 낮에 전화로 물어볼 수 있어요.",
    }),
    10: ("AI 기능을 내세우지 않는 제품이에요. AI라고 표시한 문구를 찾지 못했어요.", {
        "functions": "AI라고 표시한 기능을 찾지 못했어요.",
        "evidence": "확인할 AI 표시 문구가 없어요.",
        "conditions": "어두운 곳에서도 움직인대요. 음성 명령은 나라와 언어에 따라 쓸 수 없을 수 있어요.",
        "data": "어떤 정보를 모으는지는 " + NOT_FOUND,
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "고장에 대비한 장치와 업데이트 기간은 " + NOT_FOUND,
        "remedy": "교환과 반품은 070-8015-1154로 물어볼 수 있어요.",
    }),
    11: ("이름에 'AI'가 붙어 있지만, 카메라로 물건을 알아보는 기능은 적혀 있지 않아요.", {
        "functions": "센서로 바닥과 카펫을 구분하고, 집에 사람이 없을 때를 알아챈다고 해요. 빅스비로 말해서 움직일 수도 있어요.",
        "evidence": "5개 문구 중 4개는 조건이 함께 적혀 있어요. 1개는 근거를 찾지 못했어요.",
        "conditions": "물건 모양이나 환경에 따라 잘못 알아볼 수 있어요. 카펫이 없으면 보통 방식으로 청소해요.",
        "data": "중요한 정보는 따로 된 보안칩에 저장한대요.",
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "보안 인증을 받았다고 해요. 업데이트를 언제까지 해 주는지는 나와 있지 않아요.",
        "remedy": "1년 동안 품질을 보증해요. 모터는 10년 동안 무료로 고쳐 주지만, 교환이나 환불에는 해당하지 않아요.",
    }),
    13: ("AI로 얼룩을 알아보고 다시 닦는다고 적혀 있어요. 설명이 영어로만 되어 있어요.", {
        "functions": "얼룩을 알아채고 다시 닦는다고 해요. 집과 사용자를 이해한다고도 적혀 있어요.",
        "evidence": "3개 문구 중 1개만 근거가 있어요. 나머지 2개는 근거를 찾지 못했어요.",
        "conditions": "AI 기능이 언제 잘 안 되는지는 " + NOT_FOUND,
        "data": "어떤 정보를 모으는지는 " + NOT_FOUND,
        "optout": "AI 기능을 끄는 방법은 " + NOT_FOUND,
        "safety": "고장에 대비한 장치와 업데이트 기간은 " + NOT_FOUND,
        "remedy": "문의와 환불 방법은 " + NOT_FOUND,
    }),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    docs = {}
    for did, pid, kind, cj in con.execute("select id, product_id, kind, chunks_json from documents"):
        docs[did] = (pid, kind, json.loads(cj))

    def ev(pid, refs):
        out = []
        for did, ch in refs:
            dpid, kind, chunks = docs[did]
            assert dpid == pid, f"문서 {did}는 제품 {pid}의 자료가 아님"
            out.append({"doc_id": did, "chunk": ch, "quote": chunks[ch], "kind": kind})
        return out

    pids = sorted({p for p, _ in CLAIMS} | {p for p, _ in ITEMS} | set(EASY))
    n = 0
    for pid in pids:
        card = json.loads(con.execute("select card_json from cards where product_id=?", (pid,)).fetchone()[0])
        by = {c["id"]: c for c in card["claims"]}
        for c in card["claims"]:  # 검토 사유 문장을 최신본으로 맞춘다
            if REASON.get((pid, c["id"])):
                c["reviewer_note"] = REASON[(pid, c["id"])]
        for (p, cid), (refs, cond, note) in CLAIMS.items():
            if p != pid:
                continue
            c = by[cid]
            c["evidence"], c["conditions"], c["note"] = ev(pid, refs), cond, note
            n += 1
        for (p, key), (refs, summ) in ITEMS.items():
            if p != pid:
                continue
            it = card["items"][key]
            it["evidence"] = ev(pid, refs)
            if summ:
                it["summary"] = summ
            n += 1
        for it in card["items"].values():
            if it.get("summary") in SUMMARY_REPLACE:
                it["summary"] = SUMMARY_REPLACE[it["summary"]]
        if pid in EASY:
            head, items = EASY[pid]
            card["easy"] = {"headline": head, "items": items}
        if not a.dry:
            con.execute("update cards set card_json=? where product_id=?", (json.dumps(card, ensure_ascii=False), pid))
    if not a.dry:
        con.commit()
        labels = HERE / "review_round1_labels.csv"
        if labels.exists():
            with labels.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            for r in rows:
                why = REASON.get((int(r["product_id"]), r["claim_id"]))
                if why is not None:
                    r["note"] = why
            with labels.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
    print(f"{'점검만' if a.dry else '반영 완료'}: 근거 수정 {n}곳, 쉬운 말 {len(EASY)}장")


if __name__ == "__main__":
    main()
