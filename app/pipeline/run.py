"""파이프라인 오케스트레이션: 문서 → 주장 → 근거 연결 → 항목 채우기 → 쉬운 말 → 카드 저장."""
import time
from datetime import date
from typing import Any

from .. import db
from ..schema import new_card
from . import coverage, easy, extract, link, llm
from .retrieve import ChunkIndex

# AI 표시 문구는 판매 본문에서만 찾는다. 각주·사양표·설명서는 근거와 조건을 찾는 곳이다.
CLAIM_SOURCE_KINDS = ("판매페이지",)


def build_card(product_id: int, keep_reviewer_edits: bool = True) -> dict[str, Any]:
    product = db.get_product(product_id)
    if not product:
        raise ValueError(f"제품 {product_id} 없음")
    docs = db.list_documents(product_id)
    if not docs:
        raise ValueError("문서가 없습니다. 판매페이지나 설명서를 먼저 추가하세요.")

    t0 = time.time()
    old = db.get_card(product_id) if keep_reviewer_edits else None
    index = ChunkIndex(docs)
    card = new_card(product)
    card["sources"] = [{"doc_id": d["id"], "kind": d["kind"], "title": d["title"], "url": d["url"],
                        "n_chunks": len(d["chunks"])} for d in docs]

    source_chunks = index.by_kind(CLAIM_SOURCE_KINDS) or index.by_kind(tuple(set(d["kind"] for d in docs)))
    claims = extract.extract_claims(source_chunks)
    claims = link.link_claims(claims, index)
    card["claims"] = claims
    card["items"] = coverage.fill_coverage(index)
    card["easy"] = easy.make_easy(card)
    card["checked_at"] = date.today().isoformat()

    if old:
        # 공공 DB 입력과 검토자 수정은 재생성해도 보존한다
        card["public_db"] = old.get("public_db", card["public_db"])
        prev = {c["text"]: c for c in old.get("claims", [])}
        for c in card["claims"]:
            p = prev.get(c["text"])
            if p:
                c["reviewer_status"] = p.get("reviewer_status")
                c["reviewer_note"] = p.get("reviewer_note", "")

    meta = {"provider": llm.provider(), "seconds": round(time.time() - t0, 1),
            "n_docs": len(docs), "n_chunks": sum(len(d["chunks"]) for d in docs),
            "n_claims": len(claims)}
    db.save_card(product_id, card, meta)
    if product["status"] == "draft":
        db.set_product_status(product_id, "pending", "카드 생성됨, 검토 대기")
    return card
