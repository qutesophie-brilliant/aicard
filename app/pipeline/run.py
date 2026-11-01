"""파이프라인 오케스트레이션: 문서 → 주장 → 근거 연결 → 항목 채우기 → 쉬운 말 → 카드 저장."""
import time
import re
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
        _carry_review(old, card)

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


def quick_check(name: str, url: str, sales_text: str, fine_text: str = "", use_llm: bool = False, source_capture: dict | None = None) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """저장하지 않는 즉석 확인. 기본은 LLM 없이 규칙 판정만(use_llm=True면 문구 추출·근거 연결에 LLM을 쓴다). (카드, 문서 사전)을 돌려준다."""
    import contextlib
    from . import ingest, quick_text
    docs: list[dict[str, Any]] = []
    for did, kind, title, text in ((1, "판매페이지", "판매페이지 본문", sales_text), (2, "사양표", "판매페이지 각주·사양", fine_text)):
        text = (text or "").strip()
        if not text:
            continue
        ing = ingest.from_text(title, text)
        docs.append({"id": did, "product_id": 0, "kind": kind, "title": f"{title} ({name})", "url": url,
                     "text": ing.text, "chunks": ing.chunks if use_llm else quick_text.chunks(ing.text)})
    if source_capture:
        from .source_capture import section_documents
        docs = section_documents(source_capture, 10000)
        if not use_llm:
            # OCR line merging for the formal pipeline can join a preceding
            # test footnote to the next feature heading. Quick previews retain
            # the original OCR boundaries and join only explicit continuations.
            for doc in docs:
                if doc.get('source_type') == 'image_ocr' and doc.get('diagram_text'):
                    doc['text'] = doc['diagram_text']
                # HTML quotations need the same sentence-safe boundaries:
                # syllables in words such as 소음 are not sentence endings.
                doc['chunks'] = quick_text.chunks(doc['text'], visual_lines=doc.get('source_type') in ('image_ocr', 'upload_ocr'))
    if not docs:
        raise ValueError("본문 글자를 받지 못했습니다.")
    with (contextlib.nullcontext() if use_llm else llm.rules_only()):
        index = ChunkIndex(docs)
        if index.bm25 is None:
            raise ValueError("입력한 자료에서 분석할 수 있는 글자를 찾지 못했습니다. 상품 설명이 보이는 캡처·PDF를 올리거나 설명 글을 붙여 넣어 주세요.")
        card = new_card({"name": name, "maker": "", "model": "", "category": "즉석 확인"})
        card["sources"] = [{"doc_id": d["id"], "kind": d["kind"], "title": d["title"], "url": d["url"],
                            "n_chunks": len(d["chunks"])} for d in docs]
        source_chunks = index.by_kind(CLAIM_SOURCE_KINDS) or index.by_kind(tuple(set(d["kind"] for d in docs)))
        if not use_llm:
            compact_name = re.sub(r"\s+", "", name)
            source_chunks = [c for c in source_chunks if not (
                re.sub(r"\s+", "", c["quote"]) in compact_name
                and not extract.VERBISH.search(c["quote"]))]
        # Rule-only previews examine every acquired source chunk. A display
        # count of 14 must not silently discard additional distinct claims.
        claims = extract.extract_claims(source_chunks) if use_llm else extract._rules_extract(source_chunks, max(1, len(source_chunks)), complete_quotes=True)
        uploaded = bool(source_capture and source_capture.get('coverage', {}).get('scope') == 'uploaded_documents')
        if uploaded:
            from . import quick_diagrams
            claims = quick_diagrams.link_within_units(claims, docs)
        else:
            # A separately recovered overview is not proof that another feature
            # (e.g. a mechanical blade) explains the AI mentioned in that overview.
            # Keep the overview's own explanation, rather than borrowing details.
            intro_docs = {d['id']: d for d in docs if not use_llm and d.get('feature_scope') == '제품 소개'}
            linked = []
            if not intro_docs:
                linked = link.link_claims(claims, index, exclude_testimonials=True)
            else:
                for claim in claims:
                    intro_doc = intro_docs.get(claim.get('source', {}).get('doc_id'))
                    claim_index = ChunkIndex([intro_doc]) if intro_doc is not None else index
                    linked.extend(link.link_claims([claim], claim_index, exclude_testimonials=True))
            claims = linked
        # Quick-check joins nearby fragments. Do not count a repeated slogan plus
        # table labels as an additional related explanation.
        for claim in claims:
            phrase = re.sub(r"\s+", "", claim["text"])
            original = claim.get("evidence", [])
            def distinct(e):
                if _other_quick_feature(claim['text'], e['quote']):
                    return False
                q = re.sub(r"\s+", "", e["quote"])
                if phrase not in q or "문구 자체" in e.get("kind", ""):
                    return True
                remainder = q.replace(phrase, "")
                return bool(link.self_describes(remainder) or link._is_cond(remainder))
            related = [e for e in original if distinct(e)]
            claim["evidence"] = _dedupe_quick_evidence(related, docs)
            if not use_llm and _other_quick_feature(claim['text'], claim.get('conditions', '')):
                claim.update(conditions_noted=False, conditions='')
                claim['condition_evidence'] = []
            if original and not claim["evidence"]:
                claim.update(status="자료 부족", basis_state="없음", conditions_noted=False, conditions="")
        if uploaded:
            claims = quick_diagrams.connect(claims, docs)
        from . import quick_temperature
        claims = quick_temperature.connect(claims, docs)
        if not use_llm:
            from . import quick_english, quick_controls
            claims = quick_english.connect(claims, docs)
            claims = quick_controls.connect(claims, docs)
        card["claims"] = claims
        card['quick_has_intelligent_wording'] = any('지능형' in c['text'] for c in claims)
        with llm.rules_only():
            from . import quick_coverage
            card["items"] = quick_coverage.fill(index, product_name=name)
            quick_temperature.include_conditions(card)
            card["easy"] = easy.make_easy(card)
        card["checked_at"] = date.today().isoformat()
    if source_capture:
        card['source_coverage'] = source_capture['coverage']
        card['image_sources'] = source_capture.get('images', [])
    card["quick_llm"] = bool(use_llm)
    return card, {d["id"]: d for d in docs}


def _other_quick_feature(claim_text, quote):
    """Keep named container/scan features out of a temperature-mode preview.

    Long retailer images put container tests, AI temperature control and barcode
    storage next to one another. Shared words such as '김치' and '온도' are not
    enough to connect their evidence. Formal cards are not processed here.
    """
    # Explicitly different mechanisms must not borrow one another's tests or
    # conditions simply because they occur on the same long product page.
    families = (
        r'내비게이션|네비게이션|장애물|매핑|카메라|비전|위치\s*(?:추적|파악)',
        r'흡입력|싸이클론|사이클론|원심력',
        r'정온|온도|냉각|냉장',
        r'냄새\s*케어|냄새저감|메틸메르캅탄|탈취',
        r'터보\s*샷|터보\s*워시',
        r'스팀|살균',
        r'마이크|소음\s*제거|noise|microphone|beamforming',
    )
    claimed = {i for i, pattern in enumerate(families) if re.search(pattern, claim_text, re.I)}
    quoted = {i for i, pattern in enumerate(families) if re.search(pattern, quote, re.I)}
    if claimed and quoted and claimed.isdisjoint(quoted):
        return True
    if re.search(r'평생\s*보증|제품\s*교환|구입가\s*환급', quote) and not re.search(r'보증|교환|환급', claim_text):
        return True
    if not re.search(r'정온|온도.{0,16}(?:유지|조절)|사용\s*패턴', claim_text, re.I):
        return False
    for feature in (r'김치\s*통', r'스캔\s*킵'):
        if re.search(feature, quote) and not re.search(feature, claim_text):
            return True
    return False


def _dedupe_quick_evidence(evidence, docs):
    """Collapse repeated non-condition excerpts from the same source page."""
    urls={d['id']:d.get('url','') for d in docs}
    norm=lambda text:re.sub(r'\s+','',text)
    kept=[]
    for i,e in enumerate(evidence):
        q=norm(e['quote'])
        same_page=lambda other:(other['doc_id']==e['doc_id'] or bool(urls.get(e['doc_id'])) and urls.get(other['doc_id'])==urls.get(e['doc_id']))
        if any(same_page(other) and norm(other['quote'])==q for other in kept):
            continue
        if not link._is_cond(e['quote']) and any(
            j!=i and same_page(other) and not link._is_cond(other['quote'])
            and q in norm(other['quote']) and q!=norm(other['quote'])
            for j,other in enumerate(evidence)):
            continue
        kept.append(e)
    return kept


def _norm_claim(t: str) -> str:
    import re
    return re.sub(r"[\s\W_]+", "", (t or "")).lower()


def _carry_review(old: dict[str, Any], card: dict[str, Any]) -> None:
    """재생성해도 검토자가 손댄 것은 남긴다: 문구별 판정·메모·근거 종류·검토자 인용, 항목별 수정.
    문구는 같은 글자가 아니어도(띄어쓰기·기호 차이, 85% 이상 비슷하면) 같은 문구로 본다."""
    import difflib
    old_claims = [c for c in old.get("claims", []) if c.get("reviewer_status") or c.get("reviewer_note")
                  or c.get("basis_kind") or any(e.get("kind") == "판매페이지(검토자 확인)" for e in c.get("evidence") or [])]
    used: set[int] = set()
    for c in card["claims"]:
        key = _norm_claim(c["text"])
        best, best_r = None, 0.0
        for i, p in enumerate(old_claims):
            if i in used:
                continue
            r = 1.0 if _norm_claim(p["text"]) == key else difflib.SequenceMatcher(None, key, _norm_claim(p["text"])).ratio()
            if r > best_r:
                best, best_r = i, r
        if best is None or best_r < 0.85:
            continue
        used.add(best)
        p = old_claims[best]
        c["reviewer_status"] = p.get("reviewer_status")
        c["reviewer_note"] = p.get("reviewer_note", "")
        for k in ("basis_kind", "basis_kind_global"):
            if p.get(k):
                c[k] = p[k]
        rq = [e for e in p.get("evidence") or [] if e.get("kind") == "판매페이지(검토자 확인)"]
        if rq:
            c["evidence"] = rq + [e for e in (c.get("evidence") or []) if e.get("kind") != "판매페이지(검토자 확인)"]
    # 검토자가 고친 권익 항목은 그대로 둔다(review_item 이 reviewed 표시를 남긴다)
    for key, it in (old.get("items") or {}).items():
        if isinstance(it, dict) and it.get("reviewed") and key in card.get("items", {}):
            card["items"][key] = it
    # 검토자가 지운 문구는 다시 살리지 않는다
    deleted = {_norm_claim(t) for t in old.get("deleted_claims") or []}
    if deleted:
        card["claims"] = [c for c in card["claims"] if _norm_claim(c["text"]) not in deleted]
        card["deleted_claims"] = old.get("deleted_claims")


def split_fine_print(text: str) -> tuple[str, str]:
    """서버가 직접 받은 페이지 본문을 '본문'과 '각주·작은 글씨'로 나눈다(북마클릿과 같은 규칙)."""
    import re
    junk = re.compile(r"쿠폰|장바구니|구매하기|로그인|혜택|무이자|배송|포인트|리뷰|평점|이벤트|공유하기|위시리스트|비교하기|결제|구독|별점|좋아요|^[0-9][0-9,]*$|원 ~$|상담 예약|더보기|자세히 보기")
    # 각주 표시(*, ※, 번호)나 조건·한계를 말하는 구절만 각주로 본다. '인증'·'시험'·'조건' 같은 낱말 하나로는 넘기지 않는다
    # (본문의 기능 설명 문장이 각주로 밀려 문구 추출에서 빠지던 문제, 2026-10-13).
    fine = re.compile(r"^\*|^※|^-|^\d\)|^주\d|^\(\d+\)|환경에 따라|다를 수|않을 수|없을 수|않습니다|한정|유의|주의 사항|주의사항|필요합니다|설정이 필요|기준으로 측정|자체 시험|시험 결과|시험 기준|인증번호|인증 번호|실제 사용 환경|모델에 따라")
    sales, foot = [], []
    marker = re.compile(r"^\*|^※|^-|^\d\)|^주\d|^\(\d+\)")
    for line in (l.strip() for l in text.splitlines()):
        # A condition or a coupon at the end of a paragraph must not remove an
        # earlier function sentence. Explicitly marked footnotes retain their
        # whole scope, including following sentences within the same line.
        parts = [line] if marker.search(line) else re.split(r"(?<=[.!?。])\s+(?=\S)", line)
        for part in parts:
            if len(part) < 2 or junk.search(part):
                continue
            (foot if fine.search(part) else sales).append(part)
    return "\n".join(sales), "\n".join(foot)
