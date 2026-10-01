"""AI 정보카드 — 웹 서비스 진입점."""
import os
import csv
import io
import json
from pathlib import Path
from typing import Any

import threading

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db
from .pipeline import ingest, llm
from .pipeline.run import build_card
from .schema import (CARD_ITEMS, COVERAGE_ITEMS, ITEM_LABEL, ITEM_QUESTION, effective_status,
                     summarize)

APP_DIR = Path(__file__).resolve().parent
if os.getenv("PUBLIC_DEPLOY") == "1" and not config.REVIEW_TOKEN:
    # 공개 서버에서 검토자 화면이 열려 있으면 누구나 LLM 크레딧을 쓸 수 있다.
    raise SystemExit("공개 배포에는 REVIEW_TOKEN 설정이 필요합니다.")

app = FastAPI(title="AI 정보카드")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

STATUS_CLASS = {"근거 확인": "s1", "조건 확인": "s2", "자료 부족": "s3", "추가 확인 필요": "s4"}
STATUS_MARK = {"근거 확인": "■", "조건 확인": "◪", "자료 부족": "□", "추가 확인 필요": "▲"}

templates.env.globals.update(
    STATUSES=config.STATUSES, STATUS_HELP=config.STATUS_HELP, STATUS_CLASS=STATUS_CLASS,
    STATUS_MARK=STATUS_MARK, CARD_ITEMS=CARD_ITEMS, COVERAGE_ITEMS=COVERAGE_ITEMS,
    ITEM_LABEL=ITEM_LABEL, ITEM_QUESTION=ITEM_QUESTION, eff=effective_status, summarize=summarize,
    provider=llm.provider, llm_available=llm.available,
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# ---------- 브라우저 '담기' 지원 ----------
# 쇼핑몰은 서버의 자동 접속을 막는 경우가 많다(403). 그래서 소비자·검토자가 브라우저에서 보고 있는
# 페이지 본문을 그대로 이 서비스로 보내는 통로를 둔다. 로컬(127.0.0.1)로 보내는 요청을 브라우저가
# 허용하도록 CORS와 Private Network Access 헤더를 붙인다.
@app.middleware("http")
async def _cors_for_capture(request: Request, call_next):
    if request.url.path.startswith("/api/ingest"):
        if request.method == "OPTIONS":
            resp = Response(status_code=204)
        else:
            resp = await call_next(request)
        origin = request.headers.get("origin", "*")
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        return resp
    return await call_next(request)


_build_lock = threading.Lock()


def _ocr_then_build(pid: int, image_urls: list[str], name: str, url: str) -> None:
    """상세 이미지가 있으면 먼저 글자를 읽어 '판매페이지(이미지)' 문서로 붙인 뒤 카드를 만든다."""
    if image_urls:
        with _build_lock:
            try:
                ing = ingest.from_image_urls(f"상세 이미지 ({name})", image_urls)
                if ing.chunks:
                    db.add_document(pid, "판매페이지", ing.title, url, ing.text, ing.chunks)
                db.set_product_status(pid, "draft", "카드 생성 중 — " + " / ".join(ing.notes)[:300])
            except Exception as e:
                db.set_product_status(pid, "draft", f"이미지 읽기 실패: {e}"[:400])
    if not db.list_documents(pid):
        db.set_product_status(pid, "draft", "카드 생성 실패: 읽을 수 있는 글자가 없습니다.")
        return
    _build_in_background(pid)


def _build_in_background(pid: int) -> None:
    with _build_lock:   # LLM 호출이 몰리지 않게 한 번에 하나씩
        try:
            build_card(pid)
        except Exception as e:
            db.set_product_status(pid, "draft", f"카드 생성 실패: {e}"[:400])


# ---------- 공통 ----------
def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("request", request)
    ctx.setdefault("path", request.url.path)
    ctx.setdefault("is_reviewer", is_reviewer(request))
    return templates.TemplateResponse(request, name, ctx)


def is_reviewer(request: Request) -> bool:
    if not config.REVIEW_TOKEN:
        return True
    tok = request.query_params.get("token") or request.cookies.get("aicard_token")
    return tok == config.REVIEW_TOKEN


def require_reviewer(request: Request) -> None:
    if not config.REVIEW_TOKEN:
        return
    tok = request.query_params.get("token") or request.cookies.get("aicard_token")
    if tok != config.REVIEW_TOKEN:
        raise HTTPException(403, "검토자 토큰이 필요합니다. 주소 뒤에 ?token=... 을 붙여 주세요.")


def card_view(pid: int) -> tuple[dict[str, Any], dict[str, Any], dict[int, dict[str, Any]]]:
    product = db.get_product(pid)
    card = db.get_card(pid)
    if not product or not card:
        raise HTTPException(404, "카드가 없습니다.")
    docs = {d["id"]: d for d in db.list_documents(pid)}
    return product, card, docs


# ---------- 소비자 화면 ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    approved = db.list_products("approved")
    cards = []
    for p in approved:
        c = db.get_card(p["id"])
        if c:
            cards.append({"product": p, "card": c, "sum": summarize(c)})
    featured = cards[0] if cards else None
    docs = {d["id"]: d for d in db.list_documents(featured["product"]["id"])} if featured else {}
    return render(request, "index.html", cards=cards, featured=featured, docs=docs)


@app.get("/card/{pid}", response_class=HTMLResponse)
def card_page(request: Request, pid: int, preview: int = 0):
    product, card, docs = card_view(pid)
    if product["status"] != "approved" and not preview:
        raise HTTPException(404, "아직 검토가 끝나지 않은 카드입니다.")
    if preview:
        require_reviewer(request)
    return render(request, "card.html", product=product, card=card, docs=docs, sum=summarize(card),
                  preview=bool(preview))


@app.get("/compare", response_class=HTMLResponse)
def compare(request: Request, a: int | None = None, b: int | None = None):
    approved = db.list_products("approved")
    pair = []
    for pid in (a, b):
        if pid:
            try:
                product, card, docs = card_view(pid)
                if product["status"] == "approved":
                    pair.append({"product": product, "card": card, "docs": docs, "sum": summarize(card)})
            except HTTPException:
                pass
    return render(request, "compare.html", approved=approved, pair=pair, a=a, b=b)


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return render(request, "about.html")


@app.get("/api/card/{pid}.json")
def card_json(pid: int):
    product, card, _ = card_view(pid)
    if product["status"] != "approved":
        raise HTTPException(404)
    return JSONResponse(card, media_type="application/json; charset=utf-8")


@app.post("/api/ingest")
async def api_ingest(request: Request, background: BackgroundTasks):
    """브라우저에서 보낸 페이지 본문으로 제품을 등록하고 카드 생성을 백그라운드로 시작한다.
    JSON: {name, maker, model, category, url, title, sales_text, fine_text}
      sales_text = 페이지 본문(AI 표시 문구를 찾는 곳)
      fine_text  = 각주·작은 글씨·사양표(근거와 조건을 찾는 곳)
    """
    require_reviewer(request)
    data = await request.json()
    name = (data.get("name") or data.get("title") or "이름 없는 제품").strip()[:120]
    pid = db.create_product(name, (data.get("maker") or "").strip()[:60],
                            (data.get("model") or "").strip()[:60],
                            (data.get("category") or "로봇청소기").strip()[:30])
    url = (data.get("url") or "").strip()[:500]
    added = 0
    for kind, key, title in (("판매페이지", "sales_text", "판매페이지 본문"),
                             ("사양표", "fine_text", "판매페이지 각주·사양")):
        text = (data.get(key) or "").strip()
        if text:
            ing = ingest.from_text(title, text)
            if ing.chunks:
                db.add_document(pid, kind, f"{title} ({name})", url, ing.text, ing.chunks)
                added += 1
    image_urls = [u for u in (data.get("image_urls") or []) if isinstance(u, str) and u.startswith("http")]
    if not added and not image_urls:
        db.delete_product(pid)
        raise HTTPException(400, "본문 글자를 받지 못했습니다.")
    db.set_product_status(pid, "draft", "카드 생성 중")
    background.add_task(_ocr_then_build, pid, image_urls, name, url)
    base = str(request.base_url).rstrip("/")
    return {"id": pid, "review_url": f"{base}/review/{pid}", "status": "building"}


@app.get("/capture/receive", response_class=HTMLResponse)
def capture_receive(request: Request, _=Depends(require_reviewer)):
    return render(request, "capture_receive.html")


@app.get("/capture", response_class=HTMLResponse)
def capture_help(request: Request, _=Depends(require_reviewer)):
    base = str(request.base_url).rstrip("/")
    return render(request, "capture.html", base=base, bookmarklet=_bookmarklet(base))


def _bookmarklet(base: str) -> str:
    """쇼핑몰 페이지에서 실행되는 북마클릿.
    보안 정책(CSP)이 엄격한 쇼핑몰은 다른 주소로 직접 보내는 요청(fetch)을 막는다. 그래서 정보카드의
    수신 창을 새로 열고, 창끼리 메시지(postMessage)로 본문을 넘긴다. 수신 창은 정보카드 서비스와 같은
    주소라서 자기 서버로 저장할 수 있다."""
    js = (
        "(()=>{"
        "const L=document.body.innerText.split('\\n').map(s=>s.trim()).filter(s=>s.length>1);"
        "const J=/쿠폰|장바구니|구매하기|로그인|혜택|무이자|배송|포인트|리뷰|평점|이벤트|공유하기|위시리스트|비교하기|결제|구독|슬라이드|별점|좋아요|^[0-9][0-9,]*$|원 ~$|상담 예약|더보기|자세히 보기|신청/;"
        "const F=/^\\*|^※|^-|기준|측정|조건|환경에 따라|다를 수|않을 수|않습니다|한정|유의|주의|인증|시험|평가|필요합니다|설정이 필요/;"
        "const K=L.filter(s=>!J.test(s));const S=[],P=[];K.forEach(s=>(F.test(s)?P:S).push(s));"
        "const D={type:'aicard-capture',url:location.href,title:document.title,sales_text:S.join('\\n'),fine_text:P.join('\\n')};"
        f"const B='{base}';const w=window.open(B+'/capture/receive','aicard_receive');"
        "if(!w){alert('팝업이 막혔어요. 이 사이트의 팝업을 허용해 주세요.');return;}"
        "const h=e=>{if(e.origin===B&&e.data==='aicard-ready'){w.postMessage(D,B);window.removeEventListener('message',h);}};"
        "window.addEventListener('message',h);"
        "})()"
    )
    return "javascript:" + js


# ---------- 등록 ----------
@app.get("/new", response_class=HTMLResponse)
def new_form(request: Request, _=Depends(require_reviewer)):
    return render(request, "new.html", error=None)


@app.post("/new")
async def new_submit(request: Request,
                     name: str = Form(...), maker: str = Form(""), model: str = Form(""),
                     category: str = Form("로봇청소기"),
                     sales_urls: str = Form(""), manual_urls: str = Form(""),
                     sales_text: str = Form(""), manual_text: str = Form(""),
                     manual_pdf: UploadFile | None = File(None),
                     _=Depends(require_reviewer)):
    pid = db.create_product(name.strip(), maker.strip(), model.strip(), category.strip())
    errors = _attach_documents(pid, sales_urls, manual_urls, sales_text, manual_text,
                               await manual_pdf.read() if manual_pdf and manual_pdf.filename else None,
                               manual_pdf.filename if manual_pdf else "")
    if not db.list_documents(pid):
        db.delete_product(pid)
        return render(request, "new.html",
                      error="읽을 수 있는 문서가 없습니다. " + " / ".join(errors))
    try:
        build_card(pid)
    except Exception as e:  # 파이프라인 오류는 검토 화면에서 보여준다
        db.set_product_status(pid, "draft", f"카드 생성 실패: {e}")
    url = f"/review/{pid}"
    if errors:
        url += "?warn=" + "|".join(errors)[:500]
    return RedirectResponse(url, status_code=303)


def _attach_documents(pid: int, sales_urls: str, manual_urls: str, sales_text: str,
                      manual_text: str, pdf: bytes | None, pdf_name: str) -> list[str]:
    errors: list[str] = []

    def add(kind: str, ing: ingest.Ingested, url: str = "") -> None:
        if ing.chunks:
            db.add_document(pid, kind, ing.title, url, ing.text, ing.chunks)
        else:
            errors.append(f"{kind} '{ing.title}'에서 글자를 찾지 못했습니다.")
        errors.extend(ing.notes)

    for u in _lines(sales_urls):
        try:
            add("판매페이지", ingest.from_url(u), u)
        except Exception as e:
            errors.append(f"판매페이지 {u[:50]} 읽기 실패: {e}")
    for u in _lines(manual_urls):
        try:
            add("설명서", ingest.from_url(u), u)
        except Exception as e:
            errors.append(f"설명서 {u[:50]} 읽기 실패: {e}")
    if sales_text.strip():
        add("판매페이지", ingest.from_text("판매페이지(붙여넣기)", sales_text))
    if manual_text.strip():
        add("설명서", ingest.from_text("설명서(붙여넣기)", manual_text))
    if pdf:
        try:
            add("설명서", ingest.from_pdf(pdf_name or "설명서 PDF", pdf))
        except Exception as e:
            errors.append(f"PDF 읽기 실패: {e}")
    return errors


def _lines(s: str) -> list[str]:
    return [l.strip() for l in s.splitlines() if l.strip()]


# ---------- 검토자 ----------
@app.get("/review", response_class=HTMLResponse)
def review_list(request: Request, _=Depends(require_reviewer)):
    products = db.list_products()
    rows = []
    for p in products:
        c = db.get_card(p["id"])
        rows.append({"product": p, "sum": summarize(c) if c else None, "meta": db.get_card_meta(p["id"])})
    resp = render(request, "review_list.html", rows=rows)
    if config.REVIEW_TOKEN and request.query_params.get("token"):
        resp.set_cookie("aicard_token", config.REVIEW_TOKEN, httponly=True, samesite="lax")
    return resp


@app.get("/review/{pid}", response_class=HTMLResponse)
def review_card(request: Request, pid: int, warn: str = "", _=Depends(require_reviewer)):
    product = db.get_product(pid)
    if not product:
        raise HTTPException(404)
    card = db.get_card(pid)
    docs = {d["id"]: d for d in db.list_documents(pid)}
    return render(request, "review_card.html", product=product, card=card, docs=docs,
                  meta=db.get_card_meta(pid), warn=[w for w in warn.split("|") if w],
                  sum=summarize(card) if card else None)


@app.post("/review/{pid}/claim")
def review_claim(pid: int, claim_id: str = Form(...), reviewer_status: str = Form(""),
                 reviewer_note: str = Form(""), _=Depends(require_reviewer)):
    product, card, _ = card_view(pid)
    for c in card["claims"]:
        if c["id"] == claim_id:
            c["reviewer_status"] = reviewer_status if reviewer_status in config.STATUSES else None
            c["reviewer_note"] = reviewer_note.strip()
    db.save_card(pid, card, db.get_card_meta(pid))
    return RedirectResponse(f"/review/{pid}#claim-{claim_id}", status_code=303)


@app.post("/review/{pid}/claim/delete")
def review_claim_delete(pid: int, claim_id: str = Form(...), _=Depends(require_reviewer)):
    product, card, _ = card_view(pid)
    card["claims"] = [c for c in card["claims"] if c["id"] != claim_id]
    db.save_card(pid, card, db.get_card_meta(pid))
    return RedirectResponse(f"/review/{pid}", status_code=303)


@app.post("/review/{pid}/item")
def review_item(pid: int, key: str = Form(...), status: str = Form(...), summary: str = Form(""),
                _=Depends(require_reviewer)):
    product, card, _ = card_view(pid)
    if key in card["items"] and status in config.STATUSES:
        card["items"][key]["status"] = status
        card["items"][key]["summary"] = summary.strip()
    db.save_card(pid, card, db.get_card_meta(pid))
    return RedirectResponse(f"/review/{pid}#item-{key}", status_code=303)


@app.post("/review/{pid}/pubdb")
async def review_pubdb(request: Request, pid: int, _=Depends(require_reviewer)):
    product, card, _ = card_view(pid)
    form = await request.form()
    pdb = card.setdefault("public_db", {})
    for key in ("kc", "rra", "energy", "recall"):
        entry = pdb.setdefault(key, {})
        found = form.get(f"{key}_found", "")
        entry["found"] = True if found == "yes" else False if found == "no" else None
        for f in ("number", "grade", "detail", "url"):
            if f"{key}_{f}" in form:
                entry[f] = str(form.get(f"{key}_{f}", "")).strip()
    db.save_card(pid, card, db.get_card_meta(pid))
    return RedirectResponse(f"/review/{pid}#pubdb", status_code=303)


@app.post("/review/{pid}/status")
def review_status(pid: int, status: str = Form(...), note: str = Form(""), _=Depends(require_reviewer)):
    if status not in ("approved", "held", "pending"):
        raise HTTPException(400)
    db.set_product_status(pid, status, note.strip())
    return RedirectResponse(f"/review/{pid}", status_code=303)


@app.post("/review/{pid}/rebuild")
def review_rebuild(pid: int, background: BackgroundTasks, _=Depends(require_reviewer)):
    # LLM 호출이 여러 번이라 1~3분 걸린다. 화면을 붙잡지 않도록 백그라운드에서 만든다.
    product = db.get_product(pid)
    if not product:
        raise HTTPException(404)
    if product["status"] != "approved":
        db.set_product_status(pid, "draft", "카드 생성 중")
    background.add_task(_build_in_background, pid)
    return RedirectResponse(f"/review/{pid}", status_code=303)


@app.post("/review/{pid}/doc")
async def review_add_doc(pid: int, kind: str = Form("설명서"), url: str = Form(""), text: str = Form(""),
                         pdf: UploadFile | None = File(None), _=Depends(require_reviewer)):
    errors = _attach_documents(
        pid, url if kind == "판매페이지" else "", url if kind != "판매페이지" else "",
        text if kind == "판매페이지" else "", text if kind != "판매페이지" else "",
        await pdf.read() if pdf and pdf.filename else None, pdf.filename if pdf else "")
    return RedirectResponse(f"/review/{pid}?warn=" + "|".join(errors)[:500], status_code=303)


@app.post("/review/{pid}/delete")
def review_delete(pid: int, _=Depends(require_reviewer)):
    db.delete_product(pid)
    return RedirectResponse("/review", status_code=303)


# ---------- 평가용 내보내기 ----------
@app.get("/export/claims.csv")
def export_claims(_=Depends(require_reviewer)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["product_id", "product", "claim_id", "claim_text", "ai_status", "reviewer_status",
                "gold_status", "gold_evidence_quote", "labeler", "note"])
    for p in db.list_products():
        c = db.get_card(p["id"])
        if not c:
            continue
        for cl in c["claims"]:
            w.writerow([p["id"], p["name"], cl["id"], cl["text"], cl.get("status", ""),
                        cl.get("reviewer_status") or "", "", "", "", ""])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=claims_labels.csv"})
