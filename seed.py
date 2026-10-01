"""샘플 제품(교육용 가상 제품) 2건을 DB에 넣는다.

  python seed.py            # 손으로 검수한 카드 그대로 넣고 승인 상태로 공개
  python seed.py --rebuild  # 문서만 넣고 파이프라인으로 카드를 새로 생성(검토 대기)
  python seed.py --reset    # 기존 샘플 제품을 지우고 다시 넣기
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db  # noqa: E402
from app.pipeline import ingest  # noqa: E402
from app.pipeline.run import build_card  # noqa: E402
from app.schema import new_card  # noqa: E402

SAMPLE = Path(__file__).resolve().parent / "data" / "samples" / "sample_products.json"


def find_chunk(chunks: list[str], quote: str) -> int:
    key = quote.strip()[:40]
    for i, c in enumerate(chunks):
        if key in c or c[:40] in quote:
            return i
    raise SystemExit(f"샘플 quote 를 문서에서 찾지 못했습니다: {quote[:50]}")


def main() -> None:
    rebuild = "--rebuild" in sys.argv
    reset = "--reset" in sys.argv
    db.init_db()
    if reset:
        for p in db.list_products():
            if p["is_sample"]:
                db.delete_product(p["id"])
                print("삭제:", p["name"])
    existing = {p["name"] for p in db.list_products()}
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for sp in data["products"]:
        if sp["name"] in existing:
            print("이미 있음:", sp["name"])
            continue
        pid = db.create_product(sp["name"], sp["maker"], sp["model"], sp["category"], is_sample=True)
        doc_ids = []
        docs = []
        for d in sp["documents"]:
            ing = ingest.from_text(d["title"], d["text"])
            did = db.add_document(pid, d["kind"], d["title"], d.get("url", ""), ing.text, ing.chunks)
            doc_ids.append(did)
            docs.append({"id": did, "kind": d["kind"], "title": d["title"], "url": d.get("url", ""),
                         "chunks": ing.chunks})
        if rebuild:
            build_card(pid)
            print("파이프라인 생성:", sp["name"])
            continue

        card = new_card(db.get_product(pid))
        card["sources"] = [{"doc_id": d["id"], "kind": d["kind"], "title": d["title"], "url": d["url"],
                            "n_chunks": len(d["chunks"])} for d in docs]
        sales = docs[0]

        def resolve(ev: dict) -> dict:
            d = docs[ev["doc"]]
            idx = find_chunk(d["chunks"], ev["quote"])
            return {"doc_id": d["id"], "chunk": idx, "quote": d["chunks"][idx], "kind": d["kind"]}

        for i, c in enumerate(sp["card"]["claims"], 1):
            idx = find_chunk(sales["chunks"], c["text"])
            card["claims"].append({
                "id": f"c{i}", "text": c["text"], "item": c["item"], "label": c["label"],
                "source": {"doc_id": sales["id"], "chunk": idx, "quote": sales["chunks"][idx]},
                "status": c["status"], "evidence": [resolve(e) for e in c["evidence"]],
                "conditions": c.get("conditions", ""), "note": c.get("note", ""),
                "reviewer_status": None, "reviewer_note": "",
            })
        for k, it in sp["card"]["items"].items():
            card["items"][k] = {"status": it["status"], "summary": it["summary"],
                                "evidence": [resolve(e) for e in it.get("evidence", [])]}
        card["easy"] = sp["card"]["easy"]
        card["public_db"] = sp["card"]["public_db"]
        card["checked_at"] = date.today().isoformat()
        db.save_card(pid, card, {"provider": "sample(검수 완료)", "seconds": 0, "n_docs": len(docs),
                                 "n_chunks": sum(len(d["chunks"]) for d in docs),
                                 "n_claims": len(card["claims"])})
        db.set_product_status(pid, "approved", "교육용 가상 제품 샘플. 검수 완료.")
        print("샘플 등록·승인:", sp["name"])


if __name__ == "__main__":
    main()
