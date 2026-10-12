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
                for k in ("basis_kind", "basis_kind_global"):
                    if p.get(k):
                        c[k] = p[k]

    meta = {"provider": llm.provider(), "seconds": round(time.time() - t0, 1),
            "n_docs": len(docs), "n_chunks": sum(len(d["chunks"]) for d in docs),
            "n_claims": len(claims)}
    db.save_card(product_id, card, meta)
    if product["status"] == "draft":
        db.set_product_status(product_id, "pending", "카드 생성됨, 검토 대기")
    return card


def build_card_dry(product_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """저장 없이 AI 초안만 만든다. 추출 1회 + 연결 1회 = LLM 호출 2회(권익 항목·쉬운 말은 규칙)."""
    product = db.get_product(product_id)
    docs = db.list_documents(product_id)
    if not product or not docs:
        raise ValueError("제품이나 문서가 없습니다.")
    t0 = time.time()
    index = ChunkIndex(docs)
    card = new_card(product)
    source_chunks = index.by_kind(CLAIM_SOURCE_KINDS) or index.by_kind(tuple(set(d["kind"] for d in docs)))
    claims = extract.extract_claims(source_chunks)
    claims = link.link_claims(claims, index)
    card["claims"] = claims
    with llm.rules_only():
        card["items"] = coverage.fill_coverage(index)
    card["checked_at"] = date.today().isoformat()
    meta = {"provider": llm.provider(), "seconds": round(time.time() - t0, 1), "n_claims": len(claims)}
    return card, meta


def quick_check(name: str, url: str, sales_text: str, fine_text: str = "", use_llm: bool = False) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """저장하지 않는 즉석 확인. 기본은 LLM 없이 규칙 판정만(use_llm=True면 문구 추출·근거 연결에 LLM을 쓴다). (카드, 문서 사전)을 돌려준다."""
    import contextlib
    from . import ingest
    docs: list[dict[str, Any]] = []
    for did, kind, title, text in ((1, "판매페이지", "판매페이지 본문", sales_text), (2, "사양표", "판매페이지 각주·사양", fine_text)):
        text = (text or "").strip()
        if not text:
            continue
        ing = ingest.from_text(title, text)
        docs.append({"id": did, "product_id": 0, "kind": kind, "title": f"{title} ({name})", "url": url,
                     "text": ing.text, "chunks": ing.chunks})
    if not docs:
        raise ValueError("본문 글자를 받지 못했습니다.")
    with (contextlib.nullcontext() if use_llm else llm.rules_only()):
        index = ChunkIndex(docs)
        card = new_card({"name": name, "maker": "", "model": "", "category": "즉석 확인"})
        card["sources"] = [{"doc_id": d["id"], "kind": d["kind"], "title": d["title"], "url": d["url"],
                            "n_chunks": len(d["chunks"])} for d in docs]
        source_chunks = index.by_kind(CLAIM_SOURCE_KINDS) or index.by_kind(tuple(set(d["kind"] for d in docs)))
        claims = extract.extract_claims(source_chunks)
        claims = link.link_claims(claims, index)
        card["claims"] = claims
        with llm.rules_only():
            card["items"] = coverage.fill_coverage(index)
            card["easy"] = easy.make_easy(card)
        card["checked_at"] = date.today().isoformat()
    card["quick_llm"] = bool(use_llm)
    return card, {d["id"]: d for d in docs}


def split_fine_print(text: str) -> tuple[str, str]:
    """서버가 직접 받은 페이지 본문을 '본문'과 '각주·작은 글씨'로 나눈다(북마클릿과 같은 규칙)."""
    import re
    junk = re.compile(r"쿠폰|장바구니|구매하기|로그인|혜택|무이자|배송|포인트|리뷰|평점|이벤트|공유하기|위시리스트|비교하기|결제|구독|별점|좋아요|^[0-9][0-9,]*$|원 ~$|상담 예약|더보기|자세히 보기")
    fine = re.compile(r"^\*|^※|^-|기준|측정|조건|환경에 따라|다를 수|않을 수|않습니다|한정|유의|주의|인증|시험|평가|필요합니다|설정이 필요")
    sales, foot = [], []
    for line in (l.strip() for l in text.splitlines()):
        if len(line) < 2 or junk.search(line):
            continue
        (foot if fine.search(line) else sales).append(line)
    return "\n".join(sales), "\n".join(foot)
