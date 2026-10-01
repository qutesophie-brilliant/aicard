"""가설 1(기술) 검증: 사람이 정답을 매긴 주장과 AI 판정을 비교해 정확도를 계산한다.

사용법
  1) 검토자 화면에서 '라벨링용 CSV 내려받기' → claims_labels.csv
  2) gold_status(정답 확인상태)와 gold_evidence_quote(정답 근거 문장 일부)를 채운다. labeler 에 이름.
  3) python eval/evaluate.py claims_labels.csv [--out eval/result.md]

산출
  - 확인상태 정확도(4분류), 2분류 정확도(근거 있음/없음), 혼동행렬
  - 근거 연결 정확도: 정답 근거 문장이 AI 근거 인용에 포함되는 비율(근거 있음 정답에 한함)
  - 근거 없는 공개 판단 수: 정답이 '자료 부족'인데 AI가 '근거 확인/조건 확인'으로 둔 건수(목표 0)
"""
import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.config import STATUSES  # noqa: E402

POSITIVE = {"근거 확인", "조건 확인"}


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="")
    ap.add_argument("--use-reviewer", action="store_true",
                    help="AI 판정 대신 검토자 최종 판정을 평가(사람+AI 협업 결과 측정)")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csv, encoding="utf-8-sig")))
    rows = [r for r in rows if r.get("gold_status", "").strip() in STATUSES]
    if not rows:
        sys.exit("gold_status 가 채워진 행이 없습니다.")

    cards = {}
    conf = defaultdict(Counter)
    n = ok4 = ok2 = 0
    ev_total = ev_hit = 0
    unsupported_public = 0
    per_product = defaultdict(lambda: [0, 0])
    for r in rows:
        pid = int(r["product_id"])
        card = cards.setdefault(pid, db.get_card(pid) or {"claims": []})
        claim = next((c for c in card["claims"] if c["id"] == r["claim_id"]), None)
        if not claim:
            continue
        pred = (claim.get("reviewer_status") or claim.get("status")) if args.use_reviewer else claim.get("status")
        gold = r["gold_status"].strip()
        n += 1
        conf[gold][pred] += 1
        if pred == gold:
            ok4 += 1
        if (pred in POSITIVE) == (gold in POSITIVE):
            ok2 += 1
        per_product[r["product"]][1] += 1
        per_product[r["product"]][0] += int(pred == gold)
        if gold in POSITIVE:
            ev_total += 1
            gq = norm(r.get("gold_evidence_quote", ""))[:30]
            if gq and any(gq in norm(e.get("quote", "")) for e in claim.get("evidence", [])):
                ev_hit += 1
        if gold == "자료 부족" and pred in POSITIVE:
            unsupported_public += 1

    lines = [
        f"# 근거 연결 정확도 평가 ({'검토자 최종' if args.use_reviewer else 'AI 단독'})",
        "",
        f"- 평가 주장 수: {n}",
        f"- 확인상태 정확도(4분류): {ok4}/{n} = {100*ok4/n:.1f}%",
        f"- 근거 유무 정확도(2분류): {ok2}/{n} = {100*ok2/n:.1f}%",
        f"- 근거 문장 연결 정확도: {ev_hit}/{ev_total} = {100*ev_hit/max(1,ev_total):.1f}%  (목표 80% 이상)",
        f"- 근거 없는 공개 판단: {unsupported_public}건  (목표 0건)",
        "",
        "## 혼동행렬 (행: 정답, 열: 판정)",
        "| 정답 \\ 판정 | " + " | ".join(STATUSES) + " |",
        "|---|" + "---|" * len(STATUSES),
    ]
    for g in STATUSES:
        lines.append(f"| {g} | " + " | ".join(str(conf[g][p]) for p in STATUSES) + " |")
    lines += ["", "## 제품별", "| 제품 | 정확 | 전체 |", "|---|---|---|"]
    for name, (a, b) in per_product.items():
        lines.append(f"| {name} | {a} | {b} |")
    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print("\n저장:", args.out)


if __name__ == "__main__":
    main()
