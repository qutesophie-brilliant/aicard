"""공공 DB 교차확인 1차(2026-10-01 조회) 결과를 카드에 넣는다.

  python eval/public_db_round1.py --db data/aicard.sqlite3

조회한 곳
- 전파 적합성평가: 국립전파연구원 적합성평가 현황 검색(모델명·인증번호 조회 후 상세 화면 확인)
- KC 안전인증: 제품안전정보센터(safetykorea.kr) 인증정보 검색(인증번호 조회)
- 리콜: 제품안전정보센터 리콜정보 검색(브랜드명·'로봇청소기' 조회)
- 에너지소비효율: 한국에너지공단 효율관리제도 대상 품목 목록
- 보안 점검(참고): 한국인터넷진흥원·한국소비자원 로봇청소기 보안 점검 발표(2025-09-02)
인증번호를 판매처 상품정보 고시에서 얻은 경우에도, 번호는 위 공공 DB에서 다시 확인한 것만 넣었다.
"""
import argparse
import json
import sqlite3

RRA = "https://www.rra.go.kr/ko/license/A_b_popup.do?app_no="
KC_URL = "https://www.safetykorea.kr/release/itemSearch"
RECALL_URL = "https://www.safetykorea.kr/recall/recallBoard"
ENERGY_URL = "https://eep.energy.or.kr/certification/certi_intro.aspx"
KISA_NEWS = "https://www.etnews.com/20250902000305"
DAY = "2026-10-01 조회"

ENERGY = {"found": False, "grade": "",
          "detail": f"효율등급 표시 대상 품목 목록에 로봇청소기가 따로 없어요. {DAY}",
          "url": ENERGY_URL}


def rra(num, app_no, detail):
    return {"found": True, "number": num, "detail": f"{detail} {DAY}", "url": RRA + app_no}


def kc(num, detail):
    return {"found": True, "number": num, "detail": f"{detail} {DAY}", "url": KC_URL}


KC_NONE = {"found": None, "number": "",
           "detail": "판매페이지와 상품정보 고시에서 인증번호를 찾지 못해 조회하지 못했어요.", "url": ""}


def recall(note="", url2=""):
    d = {"found": False, "detail": f"제품안전정보센터 리콜 검색에서 이 브랜드의 로봇청소기 리콜을 찾지 못했어요. {DAY}",
         "url": RECALL_URL}
    if note:
        d["detail"] += " " + note
        d["url2"], d["link_label2"] = url2 or KISA_NEWS, "2025년 보안 점검 보도 보기"
    return d


DATA = {
    4: {"rra": rra("R-R-SEC-VR9800", "202517210000178044", "대표 모델 VR90F01SAG의 파생모델로 등록(2025-05-16)."),
        "kc": KC_NONE, "energy": ENERGY, "recall": recall()},
    11: {"rra": rra("R-R-SEC-VR9800", "202517210000178044",
                    "울트라 모델(VR90F01SAG)과 같은 인증번호의 파생모델로 등록(2025-05-16)."),
         "kc": KC_NONE, "energy": ENERGY,
         "recall": recall("참고: 2025년 9월 KISA·한국소비자원 보안 점검에서 같은 이름의 '비스포크 AI 스팀'이 개인정보 관리 '양호' 평가를 받았어요. 점검 모델의 정확한 모델명은 확인하지 못했어요.")},
    5: {"rra": rra("R-R-LGE-NRVC", "202617210000142236",
                   "본체 모델 N95T로 등록(파생모델 N95H·N95W, 2026-04-09). 판매 모델명 N95THO의 본체로 보여요."),
        "kc": KC_NONE, "energy": ENERGY, "recall": recall()},
    6: {"rra": rra("R-R-BJR-RRE0VES", "202517210000423757", "본체 모델 RRE0VES로 등록(2025-12-05)."),
        "kc": kc("HU073271-25028", "충전 스테이션(EWFD55HRR) 전기충전기로 인증."),
        "energy": ENERGY,
        "recall": recall("참고: 2025년 9월 KISA·한국소비자원 보안 점검에서 같은 브랜드의 S9 맥스V 울트라(이 모델과 다름)가 개인정보 관리 '양호' 평가를 받았어요.")},
    7: {"rra": rra("R-R-TjD-RLX95CE", "202517210000251843", "본체 모델 RLX95CE로 등록(2025-07-14)."),
        "kc": kc("HU073400-26027", "충전기(RCXE0808)로 인증."),
        "energy": ENERGY,
        "recall": recall("참고: 2025년 9월 KISA·한국소비자원 보안 점검에서 같은 브랜드의 X50 울트라(이 모델과 다름)가 개인정보 관리 '미흡' 평가를 받았어요.")},
    8: {"rra": rra("R-R-xk2-RLP54HE", "202617210000036832", "본체 모델 RLP54HE로 등록(2026-01-23)."),
        "kc": kc("HU073809-26008", "스테이션(RCPE0270) 전기충전기로 인증."),
        "energy": ENERGY, "recall": recall()},
    9: {"rra": rra("R-R-DkL-RB05-A", "202517210000424543", "판매페이지에 적힌 번호와 같아요(2025-12-05 등록)."),
        "kc": kc("HU073098-25010", "판매페이지에 적힌 번호와 같아요. 충전기(RB05-A)로 인증."),
        "energy": ENERGY, "recall": recall()},
    10: {"rra": rra("R-R-sRb-D106", "202417210000150820", "공식 사양의 모델명 D106으로 등록(2024-05-07)."),
         "kc": KC_NONE, "energy": ENERGY, "recall": recall()},
    13: {"rra": rra("R-R-Eh5-DEX95", "202517210000388962",
                    "해외 판매처에 표기된 모델 코드 DEX95로 조회했어요(2025-11-07 등록)."),
         "kc": KC_NONE, "energy": ENERGY,
         "recall": recall("참고: 2025년 9월 KISA·한국소비자원 보안 점검에서 같은 브랜드의 디봇 X8 프로 옴니(이 모델과 다름)에 보안 취약점이 지적됐어요.")},
    12: {"rra": {"found": None, "number": "", "detail": "", "url": ""}, "kc": KC_NONE, "energy": ENERGY,
         "recall": recall("참고: 2025년 9월 KISA·한국소비자원 보안 점검에서 '코드제로 로보킹 AI 올인원'이 개인정보 관리 '양호' 평가를 받았어요.")},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    for pid, pdb in DATA.items():
        row = con.execute("select card_json from cards where product_id=?", (pid,)).fetchone()
        card = json.loads(row[0])
        card["public_db"] = pdb
        con.execute("update cards set card_json=? where product_id=?", (json.dumps(card, ensure_ascii=False), pid))
    con.commit()
    print(f"공공 DB 결과 반영: {len(DATA)}장")


if __name__ == "__main__":
    main()
