"""AI 정보카드 — 웹 서비스 진입점."""
from datetime import date, timedelta
import os
import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import threading
import httpx

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import config, db
from .pipeline import ingest, llm, ocr
from .pipeline.run import build_card, quick_check, split_fine_print
from .schema import (BASIS_HELP, BASIS_KINDS, BASIS_LABEL, BASIS_STATE_HELP, BASIS_STATES, CARD_ITEMS, COVERAGE_ITEMS,
                     DATA_SERVER_LABEL, DATA_SERVERS, ITEM_LABEL, ITEM_QUESTION, ITEM_SHORT, ITEM_TILE, QUESTIONS,
                     QUESTION_TEXT, SUPPORT_PERIOD_LABEL, SUPPORT_PERIODS, axes_of, basis_counts, question_text,
                     effective_status, global_extra, heads_up, status_from, summarize)

APP_DIR = Path(__file__).resolve().parent
if os.getenv("PUBLIC_DEPLOY") == "1" and not config.REVIEW_TOKEN:
    # 공개 서버에서 검토자 화면이 열려 있으면 누구나 LLM 크레딧을 쓸 수 있다.
    raise SystemExit("공개 배포에는 REVIEW_TOKEN 설정이 필요합니다.")

app = FastAPI(title="AI 정보카드")
db.init_db()  # 새 표(요청 등)가 있으면 바로 만든다
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

STATUS_CLASS = {"근거 확인": "s1", "조건 확인": "s2", "자료 부족": "s3", "추가 확인 필요": "s4"}
STATUS_MARK = {"근거 확인": "✓", "조건 확인": "✓", "자료 부족": "–", "추가 확인 필요": "!"}
# 라벨에서 쓰는 짧은 상태 이름. 소비자에게는 세 가지로 보인다: 근거 있음 / 근거 못 찾음 / 설명 어긋남
STATUS_SHORT = {"근거 확인": "작동 설명 있음", "조건 확인": "작동 설명 있음", "자료 부족": "작동 설명 없음", "추가 확인 필요": "추가 확인 필요"}

templates.env.globals.update(
    STATUSES=config.STATUSES, STATUS_HELP=config.STATUS_HELP, STATUS_CLASS=STATUS_CLASS,
    STATUS_MARK=STATUS_MARK, CARD_ITEMS=CARD_ITEMS, COVERAGE_ITEMS=COVERAGE_ITEMS,
    ITEM_LABEL=ITEM_LABEL, ITEM_QUESTION=ITEM_QUESTION, ITEM_SHORT=ITEM_SHORT, ITEM_TILE=ITEM_TILE,
    QUESTIONS=QUESTIONS, QUESTION_TEXT=QUESTION_TEXT, STATUS_SHORT=STATUS_SHORT, qtext=question_text,
    eff=effective_status, summarize=summarize, heads_up=heads_up, axes=axes_of,
    BASIS_STATES=BASIS_STATES, BASIS_STATE_HELP=BASIS_STATE_HELP,
    DATA_SERVERS=DATA_SERVERS, DATA_SERVER_LABEL=DATA_SERVER_LABEL,
    SUPPORT_PERIODS=SUPPORT_PERIODS, SUPPORT_PERIOD_LABEL=SUPPORT_PERIOD_LABEL,
    BASIS_KINDS=BASIS_KINDS, BASIS_LABEL=BASIS_LABEL, BASIS_HELP=BASIS_HELP, basis_counts=basis_counts, global_extra=global_extra,
    provider=llm.provider, llm_available=llm.available,
    CHECK_MAX_FILES=config.CHECK_MAX_FILES, CHECK_MAX_FILE_MB=config.CHECK_MAX_FILE_MB,
    CHECK_OCR_AI=config.CHECK_OCR_LLM and llm.available(), CHECK_CONNECT_AI=config.CHECK_LLM and llm.available(),
)


def _haeyo(v: str) -> str:
    """소비자용 요약의 문체와 자료 확인 범위를 정돈한다. 원문 인용에는 적용하지 않는다."""
    text = v or ""
    if text.strip() == "공개자료에서 확인되지 않았습니다.":
        return "이 카드에 사용한 자료에서는 확인하지 못했습니다."
    endings = {"아니에요": "아닙니다", "이에요": "입니다", "예요": "입니다",
               "했어요": "했습니다", "못했어요": "못했습니다", "않았어요": "않았습니다",
               "있어요": "있습니다", "없어요": "없습니다", "않아요": "않습니다",
               "해요": "합니다", "돼요": "됩니다", "줘요": "줍니다", "두었어요": "두었습니다",
               "읽었어요": "읽었습니다", "찾았어요": "찾았습니다", "달라져요": "달라집니다",
               "알아봐요": "인식합니다", "없었어요": "없었습니다", "많아요": "많습니다"}
    for old, new in sorted(endings.items(), key=lambda pair: -len(pair[0])):
        text = re.sub(re.escape(old) + r"(?=[.!?\s()（）]|$)", new, text)
    return text


def kdate(v: str) -> str:
    """2026-10-01 -> 2026. 10. 1."""
    v = (v or "")[:10]
    try:
        y, m, d = v.split("-")
        return f"{int(y)}. {int(m)}. {int(d)}."
    except Exception:
        return v


templates.env.filters["kdate"] = kdate
templates.env.filters["haeyo"] = _haeyo


def wa(word: str) -> str:
    """'와/과' 조사."""
    ch = (word or "").strip()[-1:]
    if not ch or not ("가" <= ch <= "힣"):
        return word + "와" if not ch or ch.isdigit() is False and ch.isalpha() else word + "과"
    return word + ("과" if (ord(ch) - 0xAC00) % 28 else "와")


templates.env.filters["wa"] = wa


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    _start_keep_awake()
    _start_queue_worker()


@app.get("/healthz")
def healthz():
    try:
        q = db.queue_counts()
    except Exception:
        q = {}
    return {"ok": True, "queue": q}


def _start_keep_awake() -> None:
    """무료 호스팅은 15분 동안 요청이 없으면 잠들어 다음 방문자가 30~60초를 기다린다.
    공개 주소가 있으면 10분마다 스스로를 한 번 불러 깨어 있게 한다(KEEP_AWAKE=0이면 끔)."""
    url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL") or ""
    if os.getenv("KEEP_AWAKE", "1") != "1" or not url.startswith("http"):
        return
    import urllib.request

    def loop():
        import time as _t
        while True:
            _t.sleep(600)
            try:
                urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=20).read()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True, name="keep-awake").start()


# ---------- 브라우저 '담기' 지원 ----------
# 쇼핑몰은 서버의 자동 접속을 막는 경우가 많다(403). 그래서 소비자·검토자가 브라우저에서 보고 있는
# 페이지 본문을 그대로 이 서비스로 보내는 통로를 둔다. 로컬(127.0.0.1)로 보내는 요청을 브라우저가
# 허용하도록 CORS와 Private Network Access 헤더를 붙인다.
@app.middleware("http")
async def _count_views(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    p = request.url.path
    if request.method == "GET" and resp.status_code == 200 and (p == "/" or p.startswith(("/card/", "/compare", "/about", "/check"))):
        try:
            db.count_view(p.split("?")[0])
        except Exception:
            pass
    return resp


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
    if image_urls and not any(d.get("title", "").startswith("상세 이미지") for d in db.list_documents(pid)):
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


_queue_wake = threading.Event()


def _enqueue_build(pid: int, image_urls: list[str], name: str, url: str) -> None:
    """카드 생성을 DB 대기열에 넣고 작업 스레드를 깨운다. 요청 처리 스레드를 잡아먹지 않는다."""
    db.enqueue_build(pid, image_urls, name, url)
    _queue_wake.set()


def _queue_worker() -> None:
    while True:
        job = None
        try:
            job = db.next_build()
        except Exception:
            pass
        if not job:
            _queue_wake.wait(30)
            _queue_wake.clear()
            continue
        try:
            _ocr_then_build(job["product_id"], job["image_urls"], job["name"], job["url"])
            db.finish_build(job["id"], True)
        except Exception as e:
            db.finish_build(job["id"], False, str(e))


def _start_queue_worker() -> None:
    threading.Thread(target=_queue_worker, daemon=True, name="build-queue").start()


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


@app.exception_handler(500)
async def _server_error(request: Request, exc: Exception):
    return templates.TemplateResponse(request, "404.html", {"request": request, "path": request.url.path, "is_reviewer": False,
                                                             "detail": "잠깐 문제가 생겼습니다. 잠시 뒤 다시 열어 주세요."}, status_code=500)


@app.exception_handler(403)
async def _forbidden(request: Request, exc: Exception):
    if request.url.path.startswith(("/api/", "/export/")) or request.method != "GET":
        return JSONResponse({"detail": getattr(exc, "detail", "forbidden")}, status_code=403)
    return RedirectResponse("/review/login", status_code=303)


@app.exception_handler(404)
async def _not_found(request: Request, exc: Exception):
    if request.url.path.startswith(("/api/", "/static/")):
        return JSONResponse({"detail": "not found"}, status_code=404)
    detail = getattr(exc, "detail", "") or ""
    return templates.TemplateResponse(request, "404.html", {"request": request, "path": request.url.path,
                                                             "is_reviewer": is_reviewer(request), "detail": detail},
                                      status_code=404)


# ---------- 소비자 화면 ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    approved = db.list_products("approved")
    cards = []
    for p in approved:
        c = db.get_card(p["id"])
        if c:
            cards.append({"product": p, "card": c, "sum": summarize(c)})
    cards.sort(key=lambda r: (r["product"].get("is_sample", 0), r["product"]["name"]))
    # 첫 화면 보기: 표시와 설명이 어긋난 문구가 있는 카드를 우선 고른다(없으면 문구가 가장 많은 카드).
    example = None
    real = [r for r in cards if not r["product"].get("is_sample") and r["sum"]["n_claims"] >= 3]
    if real:
        real.sort(key=lambda r: (-r["sum"]["counts"]["추가 확인 필요"], -r["sum"]["n_claims"]))
        ex = real[0]
        example = {"product": ex["product"], "card": ex["card"], "sum": ex["sum"], "heads": heads_up(ex["card"], 1),
                   "claims": sorted(ex["card"]["claims"],
                                    key=lambda c: (effective_status(c) != "추가 확인 필요", len(c["text"])))[:4]}
    makers = sorted({r["product"]["maker"] for r in cards if r["product"]["maker"]})
    # 품목별 수("로봇청소기 23종, 공기청정기 6종"). 품목이 하나면 그 이름만 쓴다
    from collections import Counter as _C
    cat_counts = _C((r["product"].get("category") or "제품") for r in cards if not r["product"].get("is_sample"))
    _cats = sorted(cat_counts.items(), key=lambda kv: -kv[1])
    cat_line = ", ".join(f"{k} {v}종" for k, v in _cats[:4]) or f"제품 {len(cards)}종"
    if len(_cats) > 4:
        cat_line += f" 외 {len(_cats) - 4}개 품목"
    base = _public_base(request)
    real_cards = [r for r in cards if not r["product"].get("is_sample")]
    hero = {"products": len(real_cards), "cats": len(cat_counts),
            "claims": sum(r["sum"]["n_claims"] for r in real_cards),
            "linked": sum(r["sum"]["linked"] for r in real_cards),
            "optout_missing": sum(1 for r in real_cards if (r["card"].get("items", {}).get("optout", {}) or {}).get("status") == "자료 부족"),
            "pending": sum(1 for p in db.list_products("pending") if not p.get("is_sample") and not (p.get("name") or "").startswith("probe")),
            "week": sum(1 for p in approved if (p.get("approved_at") or "") >= (date.today() - timedelta(days=7)).isoformat())}
    return render(request, "index.html", cards=cards, example=example, makers=makers, cat_line=cat_line, hero=hero,
                  bookmarklet=_bookmarklet(base, "/check/receive"))


@app.get("/card/{pid}", response_class=HTMLResponse)
def card_page(request: Request, pid: int, preview: int = 0):
    product, card, docs = card_view(pid)
    from_check = request.query_params.get("from") == "check"
    if product["status"] != "approved" and not preview:
        raise HTTPException(404, "아직 검토가 끝나지 않은 카드입니다.")
    if preview:
        require_reviewer(request)
    return render(request, "card.html", product=product, card=card, docs=docs, sum=summarize(card),
                  preview=bool(preview), from_check=from_check)


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
    from .release_dates import release_for
    for product in approved:
        product["release"] = release_for(product)
    for row in pair:
        row["release"] = release_for(row["product"])
    return render(request, "compare.html", approved=approved, pair=pair, a=a, b=b)


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    # 공개 카드 전체 집계 — 심사자가 보는 '현재 상태' 수치
    tot = {"products": 0, "claims": 0, "linked": 0, "mismatch": 0, "missing": 0,
           "basis": {k: 0 for k, _, _ in BASIS_KINDS}, "basis_global": {k: 0 for k, _, _ in BASIS_KINDS},
           "global_extra": 0, "optout_missing": 0, "data_missing": 0}
    for pr in db.list_products("approved"):
        c = db.get_card(pr["id"])
        if not c:
            continue
        sm = summarize(c)
        tot["products"] += 1
        tot["claims"] += sm["n_claims"]
        tot["linked"] += sm["linked"]
        tot["mismatch"] += sm["counts"]["추가 확인 필요"]
        tot["missing"] += sm["counts"]["자료 부족"]
        for k, v in basis_counts(c).items():
            tot["basis"][k] += v
        for k, v in basis_counts(c, "global").items():
            tot["basis_global"][k] += v
        tot["global_extra"] += len(global_extra(c))
        if c["items"].get("optout", {}).get("status") == "자료 부족":
            tot["optout_missing"] += 1
        if c["items"].get("data", {}).get("status") == "자료 부족":
            tot["data_missing"] += 1
    return render(request, "about.html", tot=tot)


@app.get("/findings", response_class=HTMLResponse)
def findings(request: Request):
    """공개한 카드 전체에서 확인된 것. 제품 순위를 매기지 않고, 문구 단위 집계와 실제 사례만 보여 준다."""
    tot = {"products": 0, "claims": 0, "linked": 0, "mismatch": 0, "missing": 0, "control": 0,
           "basis": {k: 0 for k, _, _ in BASIS_KINDS},
           "optout_missing": 0, "data_missing": 0, "cond_missing": 0}
    mismatches: list[dict[str, Any]] = []
    cats: dict[str, int] = {}
    for pr in db.list_products("approved"):
        c = db.get_card(pr["id"])
        if not c:
            continue
        sm = summarize(c)
        tot["products"] += 1
        tot["claims"] += sm["n_claims"]
        tot["linked"] += sm["linked"]
        tot["mismatch"] += sm["counts"]["추가 확인 필요"]
        tot["missing"] += sm["counts"]["자료 부족"]
        if sm["n_claims"] == 0:
            tot["control"] += 1
        cats[pr.get("category") or "제품"] = cats.get(pr.get("category") or "제품", 0) + 1
        for k, v in basis_counts(c).items():
            tot["basis"][k] += v
        for key, slot in (("optout", "optout_missing"), ("data", "data_missing"), ("conditions", "cond_missing")):
            if c["items"].get(key, {}).get("status") == "자료 부족":
                tot[slot] += 1
        for cl in c["claims"]:
            if effective_status(cl) != "추가 확인 필요":
                continue
            mismatches.append({"pid": pr["id"], "product": pr["name"], "maker": pr["maker"],
                               "claim": cl["text"],
                               "note": cl.get("conditions") or cl.get("reviewer_note") or "",
                               "quote": (cl.get("evidence") or [{}])[0].get("quote", ""),
                               "anchor": f"claim-{cl['id']}"})
    cat_line = ", ".join(f"{k} {v}장" for k, v in sorted(cats.items(), key=lambda kv: -kv[1]))
    return render(request, "findings.html", tot=tot, mismatches=mismatches, cat_line=cat_line)


@app.get("/api/cards.json")
def cards_json():
    """공개 카드 전체(승인된 것만)."""
    out = []
    for pr in db.list_products("approved"):
        c = db.get_card(pr["id"])
        if c:
            out.append({"id": pr["id"], "product": c["product"], "checked_at": c.get("checked_at"),
                        "summary": summarize(c), "card": c})
    return JSONResponse(out, media_type="application/json; charset=utf-8")


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /\nDisallow: /review\nDisallow: /api/\n"
                    "\nSitemap: https://ai-infocard.co.kr/sitemap.xml\n", media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    """검색에 제공할 공개 페이지. 초안·검토 URL과 요청별 분석 결과는 제외한다."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    base = "https://ai-infocard.co.kr"
    paths = ["/", "/about", "/check", "/compare", "/findings"]
    paths.extend(f"/card/{p['id']}" for p in db.list_products("approved") if db.get_card(p["id"]))
    root = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for path in paths:
        SubElement(SubElement(root, "url"), "loc").text = base + path
    return Response(tostring(root, encoding="utf-8", xml_declaration=True), media_type="application/xml")


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
    return _ingest_data(request, background, data)


_capture_keys: dict[str, float] = {}


@app.get("/capture/key")
def capture_key(request: Request, _=Depends(require_reviewer)):
    """다른 사이트에서 폼 전송으로 수집할 때 쓰는 임시 열쇠(2시간). 쿠키는 다른 사이트에서 오는 전송에 실리지 않는다."""
    import secrets, time as _t
    k = secrets.token_urlsafe(16)
    _capture_keys[k] = _t.time() + 7200
    return {"key": k}


@app.post("/capture/form")
async def capture_form(request: Request, background: BackgroundTasks, name: str = Form(""), maker: str = Form(""),
                       model: str = Form(""), category: str = Form(""), url: str = Form(""),
                       sales_text: str = Form(""), fine_text: str = Form(""), image_urls: str = Form(""),
                       force: str = Form(""), key: str = Form("")):
    """외부 판매페이지에서 폼 전송으로 넘어오는 수집(팝업·fetch가 막힌 브라우저용). 결과를 글자로 보여 준다."""
    import time as _t
    if not (key and _capture_keys.get(key, 0) > _t.time()):
        require_reviewer(request)
    data = {"name": name, "maker": maker, "model": model, "category": category, "url": url,
            "sales_text": sales_text, "fine_text": fine_text,
            "image_urls": [u.strip() for u in image_urls.splitlines() if u.strip()], "force": force == "true"}
    try:
        out = _ingest_data(request, background, data)
        return PlainTextResponse(f"OK {out['id']} {name} {model}")
    except HTTPException as e:
        return PlainTextResponse(f"ERR {e.status_code} {e.detail}", status_code=e.status_code)


@app.post("/capture/requeue")
async def capture_requeue(request: Request, model: str = Form(""), image_urls: str = Form(""), key: str = Form("")):
    """이미 등록됐지만 생성이 끊긴 제품을 상세 이미지와 함께 대기열에 다시 넣는다(서버 재시작 복구용)."""
    import time as _t
    if not (key and _capture_keys.get(key, 0) > _t.time()):
        require_reviewer(request)
    m = model.strip().lower()
    hit = [p for p in db.list_products() if (p.get("model") or "").strip().lower() == m and p.get("status") == "draft"]
    if not hit:
        return PlainTextResponse(f"ERR no draft for {model}", status_code=404)
    p = hit[-1]
    docs = db.list_documents(p["id"])
    url = docs[0]["url"] if docs else ""
    _enqueue_build(p["id"], [u.strip() for u in image_urls.splitlines() if u.strip().startswith("http")], p["name"], url)
    db.set_product_status(p["id"], "draft", "카드 생성 중(대기열)")
    return PlainTextResponse(f"OK requeued {p['id']} {p['name']}")


def _ingest_data(request: Request, background: BackgroundTasks, data: dict) -> dict:
    name = (data.get("name") or data.get("title") or "이름 없는 제품").strip()[:120]
    # 같은 모델명이 이미 있으면 중복 등록을 막는다(10/12 중복 2건 재발 방지). force=true면 그대로 만든다.
    model_in = (data.get("model") or "").strip().lower()
    if model_in and not data.get("force"):
        for p in db.list_products():
            if (p.get("model") or "").strip().lower() == model_in and p.get("status") in ("approved", "pending", "draft"):
                raise HTTPException(409, f"같은 모델명({p.get('model')})의 카드가 이미 있습니다: /review/{p['id']} ({p.get('status')}). 그래도 만들려면 force=true로 보내 주세요.")
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
    _enqueue_build(pid, image_urls, name, url)
    base = _public_base(request)
    return {"id": pid, "review_url": f"{base}/review/{pid}", "status": "building"}


# ---------- 즉석 확인(소비자용, 저장·LLM 없음) ----------
_check_hits: dict[str, list[float]] = {}


def _rate_ok(ip: str, limit: int = 30, window: int = 3600) -> bool:
    """IP별 시간당 횟수 제한. 키에 ':ocr' 같은 꼬리를 붙여 용도별로 따로 센다."""
    import time as _t
    now_t = _t.time()
    hits = [t for t in _check_hits.get(ip, []) if now_t - t < window]
    hits.append(now_t)
    _check_hits[ip] = hits
    return len(hits) <= limit


def _check_summary(card: dict[str, Any]) -> dict[str, Any]:
    claims = card["claims"]
    with_ev = [c for c in claims if c.get("evidence")]
    items = {k: bool(card["items"][k].get("evidence")) for k in COVERAGE_ITEMS}
    return {"n": len(claims), "with_ev": len(with_ev), "items": items, "items_found": sum(items.values())}



def _match_published(url: str) -> dict[str, Any] | None:
    """붙여 넣은 주소가 이미 검토·공개된 제품이면 그 제품을 돌려준다(모델명이 주소에 들어 있거나 저장된 주소와 같은 경우)."""
    import re as _re
    u = (url or "").strip().lower()
    if not u.startswith("http"):
        return None
    u_norm = u.split("?")[0].split("#")[0].rstrip("/")
    for p in db.list_products("approved"):
        model = (p.get("model") or "").strip().lower()
        if model and len(model) >= 5 and _re.sub(r"[^a-z0-9]", "", model) in _re.sub(r"[^a-z0-9]", "", u_norm):
            return p
        for d in db.list_documents(p["id"]):
            du = (d.get("url") or "").strip().lower().split("?")[0].split("#")[0].rstrip("/")
            if du and du == u_norm:
                return p
    return None

@app.get("/check", response_class=HTMLResponse)
def check_page(request: Request, q: str = ""):
    base = _public_base(request)
    return render(request, "check.html", base=base, bookmarklet=_bookmarklet(base, "/check/receive"),
                  error=None, url="", q=q.strip()[:80])


_check_ocr_gate = threading.BoundedSemaphore(1)


def _read_check_uploads(payloads: list[tuple[str, str, bytes]]) -> tuple[list[str], list[str]]:
    """OCR runs outside the async request loop; bound peak memory on the 512MB instance."""
    if not _check_ocr_gate.acquire(blocking=False):
        raise ValueError("다른 파일의 글자를 읽고 있습니다. 잠시 뒤 다시 시도해 주세요.")
    try:
        from PIL import Image
        imgs: list[bytes] = []
        texts: list[str] = []
        notes: list[str] = []
        engines = {"llm": "AI 문자 인식", "tesseract": "서버 내 문자 인식", "pdf": "PDF 텍스트 추출", "none": "읽을 수 있는 글자 없음"}
        def keep(r):
            texts.append(r.text)
            notes.extend(r.notes)
            notes.append("글자 읽기: " + engines.get(r.engine, "문자 인식"))
        for filename, ctype, data in payloads:
            if ctype == "application/pdf" or filename.lower().endswith(".pdf"):
                try:
                    keep(ocr.read_pdf(data, allow_llm=config.CHECK_OCR_LLM))
                except Exception as e:
                    raise ValueError(f"PDF를 읽지 못했습니다. 손상되거나 암호가 설정된 파일인지 확인해 주세요. ({filename})") from e
            else:
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        image.verify()
                except Exception as e:
                    raise ValueError(f"이미지를 읽지 못했습니다. JPG·PNG 캡처로 다시 올려 주세요. ({filename})") from e
                imgs.append(data)
        if imgs:
            keep(ocr.read_images(imgs, allow_llm=config.CHECK_OCR_LLM))
        return texts, list(dict.fromkeys(notes))
    finally:
        _check_ocr_gate.release()


def _read_check_url(url: str):
    # URL image OCR shares the upload gate to bound server memory.
    if not _check_ocr_gate.acquire(blocking=False):
        raise ingest.product_page.ProductBodyUnavailable("다른 자료의 글자를 읽고 있습니다. 잠시 뒤 다시 시도해 주세요.")
    try:
        return ingest.from_url(url, ocr_images=True, product_only=True)
    finally:
        _check_ocr_gate.release()


@app.post("/check", response_class=HTMLResponse)
async def check_url(request: Request, url: str = Form(""), text: str = Form(""),
                    files: list[UploadFile] = File(default=[])):
    ip = request.client.host if request.client else "?"
    if not _rate_ok(ip):
        raise HTTPException(429, "잠시 뒤 다시 시도해 주세요.")
    base = _public_base(request)
    bm = _bookmarklet(base, "/check/receive")
    url = url.strip()
    text = text.strip()
    name = ""
    notes: list[str] = []

    # 0) 이미 검토·공개된 제품이면 즉석 분석 대신 그 카드로 보낸다(사람이 확인한 카드가 더 정확하다)
    if url.startswith("http") and not text and not [f for f in (files or []) if f and f.filename]:
        hit = _match_published(url)
        if hit:
            return RedirectResponse(f"/card/{hit['id']}?from=check", status_code=303)

    # 1) 캡처 이미지·PDF 업로드 → 글자 읽기 (소비자용: 건수·크기 제한, 비전 모델은 설정으로 켜고 끔)
    uploads = [f for f in (files or []) if f and f.filename]
    if uploads:
        if not _rate_ok(ip + ":ocr", limit=config.CHECK_OCR_PER_HOUR):
            return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="",
                          error="이미지 확인은 한 시간에 몇 번만 할 수 있습니다. 잠시 뒤 다시 시도해 주세요.")
        if len(uploads) > config.CHECK_MAX_FILES:
            return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="",
                          error=f"파일은 한 번에 {config.CHECK_MAX_FILES}개까지 올릴 수 있습니다.")
        payloads: list[tuple[str, str, bytes]] = []
        for f in uploads:
            data = await f.read(config.CHECK_MAX_FILE_MB * 1024 * 1024 + 1)
            if len(data) > config.CHECK_MAX_FILE_MB * 1024 * 1024:
                return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="",
                              error=f"파일 하나는 {config.CHECK_MAX_FILE_MB}MB까지만 올릴 수 있습니다. ({f.filename})")
            ctype = (f.content_type or "").lower()
            if not (ctype == "application/pdf" or ctype.startswith("image/") or f.filename.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))):
                return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="",
                              error=f"이미지(JPG·PNG)나 PDF만 올릴 수 있습니다. ({f.filename})")
            payloads.append((f.filename, ctype, data))
        try:
            read_texts, read_notes = await run_in_threadpool(_read_check_uploads, payloads)
        except ValueError as e:
            return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="", error=str(e))
        notes.extend(read_notes)
        text = "\n".join([text] + read_texts).strip()
        if len(text) < 20:
            return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="",
                          error="올린 파일에서 읽을 수 있는 글자를 찾지 못했습니다. 글자가 또렷하게 보이는 캡처인지 확인해 주세요.")
        name = (uploads[0].filename.rsplit(".", 1)[0] or "올린 자료")[:60]
        notes = [f"올린 파일 {len(uploads)}개에서 글자를 읽어 확인했습니다."] + list(dict.fromkeys(notes))

    source_capture = None
    if text:
        sales, fine = split_fine_print(text)
        if not name:
            name = (text.splitlines()[0] if text.splitlines() else "붙여 넣은 설명")[:60]
    elif url.startswith("http"):
        if not _rate_ok(ip + ":ocr", limit=config.CHECK_OCR_PER_HOUR):
            return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="",
                          error="이미지 확인 요청이 많습니다. 잠시 뒤 다시 시도해 주세요.")
        try:
            ing = await run_in_threadpool(_read_check_url, url)
        except ingest.product_page.ProductBodyUnavailable as e:
            return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="", error=str(e), upload_fallback=True)
        except httpx.HTTPStatusError as e:
            blocked = e.response.status_code in (401, 403)
            message = ("판매사이트가 자동 접속을 거부해 이 링크로는 검색할 수 없습니다. 상세페이지 캡처 이미지나 PDF를 올려 주세요."
                       if blocked else "판매사이트에서 페이지를 보내 주지 않아 이 링크로 확인하지 못했습니다. 잠시 뒤 다시 시도하거나 상세페이지 캡처를 올려 주세요.")
            return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="", error=message, upload_fallback=True)
        except Exception:
            return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="",
                          error="페이지를 가져오지 못했습니다. 쇼핑몰의 접근 제한이나 일시적인 연결 문제일 수 있습니다. 상세페이지를 캡처한 이미지나 PDF를 올리거나, 제품 설명을 복사해 붙여 넣어 주세요. PC라면 ‘주소 입력이 안 될 때’의 버튼으로 보낼 수도 있습니다.")
        source_capture = ing.source_capture
        sales, fine = split_fine_print(ing.text)
        notes.extend(ing.notes)
        name = (ing.title or url).split("|")[0].strip()[:60]
    else:
        return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="",
                      error="판매페이지 주소를 넣거나, 캡처 이미지·PDF를 올리거나, 설명 글을 붙여 넣어 주세요.")
    use_llm = config.CHECK_LLM and _rate_ok(ip + ":llm", limit=config.CHECK_LLM_PER_HOUR)
    try:
        card, docs = await run_in_threadpool(quick_check, name, url, sales, fine, use_llm=use_llm, source_capture=source_capture)
    except ValueError as e:
        return render(request, "check.html", base=base, bookmarklet=bm, url=url, q="", error=str(e))
    return render(request, "check_result.html", card=card, docs=docs, pname=name, url=url, s=_check_summary(card), notes=notes)


@app.get("/check/receive", response_class=HTMLResponse)
def check_receive(request: Request):
    return render(request, "check_receive.html")


@app.post("/api/check")
async def api_check(request: Request):
    """북마클릿 수신 창이 보내는 본문으로 즉석 확인 결과를 HTML 조각으로 돌려준다."""
    ip = request.client.host if request.client else "?"
    if not _rate_ok(ip):
        raise HTTPException(429)
    data = await request.json()
    name = (data.get("title") or "").split("|")[0].strip()[:60] or "제품"
    url = (data.get("url") or "")[:500]
    hit = _match_published(url)
    if hit:
        # 검토자가 확인한 카드가 있으면 즉석 분석 대신 그 카드로 보낸다
        return HTMLResponse(f'<script>location.replace("/card/{hit["id"]}?from=check")</script>'
                            f'<p class="lede">이 제품은 이미 검토자가 확인한 카드가 있습니다. <a href="/card/{hit["id"]}?from=check">카드 보기</a></p>')
    use_llm = config.CHECK_LLM and _rate_ok(ip + ":llm", limit=config.CHECK_LLM_PER_HOUR)
    try:
        card, docs = await run_in_threadpool(quick_check, name, url, data.get("sales_text", ""), data.get("fine_text", ""), use_llm=use_llm)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return templates.TemplateResponse(request, "_check_body.html",
                                      {"card": card, "docs": docs, "pname": name, "url": url, "s": _check_summary(card),
                                       "notes": [], "request": request, "is_reviewer": is_reviewer(request)})


@app.post("/check/request")
def check_request(request: Request, url: str = Form(""), name: str = Form(""), note: str = Form("")):
    ip = request.client.host if request.client else "?"
    if not _rate_ok(ip, limit=10):
        raise HTTPException(429)
    if not (url or name):
        raise HTTPException(400)
    rid = db.add_request(url, name, note)
    return render(request, "check_thanks.html", rid=rid, pname=name)


@app.get("/capture/receive", response_class=HTMLResponse)
def capture_receive(request: Request, _=Depends(require_reviewer)):
    return render(request, "capture_receive.html")


@app.get("/capture", response_class=HTMLResponse)
def capture_help(request: Request, _=Depends(require_reviewer)):
    base = _public_base(request)
    return render(request, "capture.html", base=base, bookmarklet=_bookmarklet(base))



def _public_base(request: Request) -> str:
    """바깥에서 보이는 주소. Render 같은 프록시 뒤에서는 base_url이 http로 잡히므로 X-Forwarded-Proto를 따른다."""
    base = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or request.url.hostname or ""
    if proto == "https" or (host and not host.startswith(("localhost", "127.0.0.1")) and base.startswith("http://")):
        base = "https://" + base.split("://", 1)[1]
    return base

def _bookmarklet(base: str, receive: str = "/capture/receive") -> str:
    """쇼핑몰 페이지에서 실행되는 북마클릿.
    보안 정책(CSP)이 엄격한 쇼핑몰은 다른 주소로 직접 보내는 요청(fetch)을 막는다. 그래서 정보카드의
    수신 창을 새로 열고, 창끼리 메시지(postMessage)로 본문을 넘긴다. 수신 창은 정보카드 서비스와 같은
    주소라서 자기 서버로 저장할 수 있다."""
    js = (
        "(()=>{"
        # 상품평·메뉴·추천 상품 영역은 글자를 읽기 전에 뺀다(상품평 문장이 'AI 표시 문구'로 잡히는 것을 막는다)
        "const X=/review|rating|comment|reply|gnb|lnb|header|footer|nav|breadcrumb|recommend|related|cross-?sell|bundle|cart|chat|modal|popup|layer|cookie/i;"
        # 복제본은 레이아웃이 없어 줄바꿈이 사라지므로, 실제 화면에서 잠깐 감추고 innerText를 읽은 뒤 되돌린다
        "const H=[];document.querySelectorAll('nav,header,footer,aside,iframe,*[class],*[id]').forEach(n=>{const k=n.tagName.toLowerCase()+' '+(typeof n.className==='string'?n.className:'')+' '+(n.id||'');"
        "if(/^(nav|header|footer|aside|iframe)\\b/.test(k)||X.test(k)){if(n.style.display!=='none'){H.push([n,n.style.display]);n.style.display='none';}}});"
        "const T=(document.querySelector('main')||document.body).innerText;H.forEach(([n,d])=>n.style.display=d);"
        "const L=T.split('\\n').map(s=>s.trim()).filter(s=>s.length>1);"
        "const J=/쿠폰|장바구니|구매하기|로그인|혜택|무이자|배송|포인트|리뷰|평점|이벤트|공유하기|위시리스트|비교하기|결제|구독|슬라이드|별점|좋습니다|^[0-9][0-9,]*$|원 ~$|상담 예약|더보기|자세히 보기|신청/;"
        "const F=/^\\*|^※|^-|기준|측정|조건|환경에 따라|다를 수|않을 수|않습니다|한정|유의|주의|인증|시험|평가|필요합니다|설정이 필요/;"
        "const K=L.filter(s=>!J.test(s));const S=[],P=[];K.forEach(s=>(F.test(s)?P:S).push(s));"
        "const D={type:'aicard-capture',url:location.href,title:document.title,sales_text:S.join('\\n'),fine_text:P.join('\\n')};"
        f"const B='{base}';const w=window.open(B+'{receive}','aicard_receive');"
        "if(!w){alert('팝업이 차단되었습니다. 이 사이트의 팝업을 허용해 주세요.');return;}"
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
@app.get("/review/login", response_class=HTMLResponse)
def review_login(request: Request, bad: int = 0, next: str = ""):
    return render(request, "review_login.html", bad=bool(bad), next=next if next.startswith("/review") else "")


@app.post("/review/login")
def review_login_post(request: Request, token: str = Form(""), next: str = Form("")):
    if config.REVIEW_TOKEN and token.strip() != config.REVIEW_TOKEN:
        return RedirectResponse("/review/login?bad=1", status_code=303)
    resp = RedirectResponse(next if next.startswith("/review") else "/review", status_code=303)
    if config.REVIEW_TOKEN:
        resp.set_cookie("aicard_token", config.REVIEW_TOKEN, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@app.get("/review", response_class=HTMLResponse)
def review_list(request: Request):
    if not is_reviewer(request):
        return RedirectResponse("/review/login", status_code=303)
    products = db.list_products()
    rows = []
    for p in products:
        c = db.get_card(p["id"])
        rows.append({"product": p, "sum": summarize(c) if c else None, "meta": db.get_card_meta(p["id"])})
    resp = render(request, "review_list.html", rows=rows, requests=db.list_requests("new"), views=db.view_stats(30))
    if config.REVIEW_TOKEN and request.query_params.get("token"):
        resp.set_cookie("aicard_token", config.REVIEW_TOKEN, httponly=True, samesite="lax")
    return resp


# 새 품목 12개(에어컨·세탁기). 카드를 다시 만들면 제품 번호가 바뀌므로, 번호 대신 수집 이름으로 찾는다
# 개발 세트(2차 라벨, 2026-10-04). 규칙을 고칠 때 들여다본 제품이므로 최종 정확도에는 쓰지 않는다.
_DEV_SLUGS = ["lg_fh25ese", "lg_fq18gn9be1", "lg_fx25vf", "lg_sw07gk1wes", "si_sma_c7800hs", "ss_af19dx837wft1",
              "ss_af70f19d11grz", "ss_wd90f25ahs", "ss_wf90f25adt", "wn_wpvw17haecr", "wn_wpvw17hascrm", "wn_wwd12gew",
              # 3차 라벨 세트(2026-10-09). 13회차 규칙 수정에 썼으므로 개발 세트로 내린다.
              "ss_dv10bb8440gh", "lg_rd21ese", "ss_wh90f2120gbhy", "lg_fq17gw1hn2", "ss_af90h17d35wrt",
              "lg_fq18gv3ek2", "lg_fx24kntr", "ss_ap90h03193egd", "ss_rm90h64p2w"]
# 시험 세트(4차 라벨, 2026-10-11). 13회차 규칙 수정이 끝난 뒤 새로 모은 제품(새 품목 9개)만 넣는다. 최종 정확도는 여기서 한 번만 잰다.
_BLIND_SLUGS = ["ss_dw90f79f1u01s", "ss_vr90f01aag", "ss_vs90f40csg", "ss_df90h24r5c", "ss_cc99h63i1d",
                "ss_rk70f58m1zg", "ss_kq85qnh70afxkr", "ss_rm70h91rma", "ss_ls32fm703ukxkr"]


def _blind_default_pids() -> str:
    """시험 세트 제품의 최신 번호. 결과 파일이 없으면 처음 번호(96-107)를 쓴다."""
    from . import round2
    try:
        res = json.loads(round2.RESULT_PATH.read_text(encoding="utf-8"))
        pids = sorted(res["pushed"][s]["pid"] for s in _BLIND_SLUGS if s in res.get("pushed", {}))
        if pids:
            return ",".join(str(p) for p in pids)
    except Exception:
        pass
    return "96-107"


def _blind_pids(pids: str) -> list[int]:
    """"96-107" 또는 "96,97,98" 을 번호 목록으로 바꾼다."""
    out: list[int] = []
    for part in re.split(r"[,\s]+", pids.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
    return [p for p in dict.fromkeys(out)][:200]


@app.get("/review/blind", response_class=HTMLResponse)
def review_blind_list(request: Request, pids: str = "", _=Depends(require_reviewer)):
    """라벨러용 화면. 문구와 판매페이지 원문만 보여 주고 파이프라인 판정은 넘기지 않는다.

    최종 정확도를 재는 새 품목은 라벨러가 판정을 보지 않은 상태에서 매겨야 한다. 그래서 검토 화면과 따로 둔다."""
    rows = []
    for pid in _blind_pids(pids or _blind_default_pids()):
        product = db.get_product(pid)
        card = db.get_card(pid)
        if not product or not card:
            continue
        rows.append({"product": product, "n": len(card.get("claims") or []),
                     "control": "대조군" in (product.get("reviewer_note") or "")})
    return render(request, "review_blind.html", rows=rows, product=None, pid_arg=pids or _blind_default_pids())


@app.get("/review/blind/labels.csv")
def review_blind_labels(pids: str = "", _=Depends(require_reviewer)):
    """빈 라벨 표. 라벨러가 decision·basis·conditions_noted·gold_evidence_quote 를 채운다."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["pid", "slug", "product", "control", "claim_id", "claim_text",
                "decision", "basis", "conditions_noted", "gold_evidence_quote", "labeler", "note"])
    n = 0
    for pid in _blind_pids(pids or _blind_default_pids()):
        product = db.get_product(pid)
        card = db.get_card(pid)
        if not product or not card:
            continue
        control = "Y" if "대조군" in (product.get("reviewer_note") or "") else ""
        for c in card.get("claims") or []:
            w.writerow([pid, product.get("slug") or "", product["name"], control,
                        c.get("id", ""), c.get("text", ""), "", "", "", "", "", ""])
            n += 1
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="blind_labels_{n}.csv"',
                             "Cache-Control": "no-store"})


@app.get("/review/blind/{pid}.json")
def review_blind_json(pid: int, _=Depends(require_reviewer)):
    """검증자료로 묶을 블라인드 사본(판정 없음)."""
    product = db.get_product(pid)
    card = db.get_card(pid)
    if not product or not card:
        raise HTTPException(404, "카드가 없습니다.")
    return JSONResponse({"pid": pid,
                         "product": {k: product.get(k) for k in ("name", "maker", "model", "category")},
                         "documents": [{"title": d.get("title"), "url": d.get("url"), "text": d.get("text") or ""}
                                       for d in db.list_documents(pid)],
                         "claims": [{"id": c.get("id"), "text": c.get("text")} for c in card.get("claims") or []]},
                        headers={"Cache-Control": "no-store"})

@app.get("/review/queue", response_class=HTMLResponse)
def review_queue(request: Request):
    """검토 대기 카드를 문구·근거 문장까지 펼쳐 한 화면에서 훑고 한꺼번에 승인·보류한다."""
    if not is_reviewer(request):
        return RedirectResponse("/review/login?next=/review/queue", status_code=303)
    from .schema import effective_status
    approved_models = {(p.get("model") or "").strip().lower(): p["id"] for p in db.list_products("approved")}
    rows = []
    for p in db.list_products("pending"):
        if p.get("is_sample") or (p.get("name") or "").startswith("probe"):
            continue
        c = db.get_card(p["id"])
        if not c:
            continue
        sm = summarize(c)
        claims = []
        for cl in c.get("claims") or []:
            st = effective_status(cl)
            ev = (cl.get("evidence") or [{}])[0] if cl.get("evidence") else {}
            kind = BASIS_LABEL.get(cl.get("basis_kind") or "", "") if cl.get("basis_kind") else ""
            claims.append({"text": cl.get("text", ""), "status": st, "kind": kind,
                           "quote": (ev.get("quote") or "")[:220], "kind_src": ev.get("kind") or ""})
        doc_chars = sum(len(d.get("text") or "") for d in db.list_documents(p["id"]))
        rows.append({"product": p, "sum": sm, "claims": claims, "doc_chars": doc_chars,
                     "control": sm["n_claims"] == 0 and doc_chars >= 3000,
                     "dup": approved_models.get((p.get("model") or "").strip().lower())})
    rows.sort(key=lambda r: (bool(r["dup"]), -r["sum"]["n_claims"]))
    return render(request, "review_queue.html", rows=rows)


PACKET5_PIDS = [307, 314, 318, 324, 329, 268, 274, 281, 284, 295]


@app.get("/review/packet5.zip")
def review_packet5(request: Request, _=Depends(require_reviewer)):
    """5차 라벨링 자료(라벨러용, 판정 없음)를 zip으로 내려준다. 판정은 /review/packet5_pred.json 에 따로 둔다."""
    import csv, io, zipfile
    from pathlib import Path as _P
    rows = []
    for pid in PACKET5_PIDS:
        p, c = db.get_product(pid), db.get_card(pid)
        if not p or not c:
            continue
        for cl in c.get("claims") or []:
            rows.append({"pid": pid, "slug": "", "product": p["name"], "control": "", "claim_id": cl.get("id", ""),
                         "claim_text": cl.get("text", ""), "decision": "", "basis": "", "conditions_noted": "",
                         "gold_evidence_quote": "", "labeler": "", "note": ""})
    if not rows:
        raise HTTPException(404, "5차 제품 카드가 없습니다.")
    buf = io.BytesIO()
    docs = _P(__file__).resolve().parent / "eval_docs"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for lab in "ABC":
            out = io.StringIO()
            w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow({**r, "labeler": lab})
            z.writestr(f"labels_{lab}.csv", "\ufeff" + out.getvalue())
        z.writestr("라벨링_안내.md", (docs / "라벨링_안내_5차.md").read_text(encoding="utf-8"))
        z.writestr("라벨_기준_두축.md", (docs / "라벨_기준_두축.md").read_text(encoding="utf-8"))
        z.writestr("읽어보기.txt", (docs / "읽어보기_5차.txt").read_text(encoding="utf-8"))
        z.writestr("메일_발송_안내(라벨러에게 보내지 않음).md", (docs / "메일_발송_안내_5차.md").read_text(encoding="utf-8"))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=labeling_round5.zip", "Cache-Control": "no-store"})


@app.get("/review/packet5_pred.json")
def review_packet5_pred(_=Depends(require_reviewer)):
    """5차 10개 제품의 파이프라인 판정(채점용). 라벨러에게 주지 않는다."""
    pred = {}
    for pid in PACKET5_PIDS:
        c = db.get_card(pid)
        for cl in (c or {}).get("claims") or []:
            pred[f"{pid}:{cl.get('id')}"] = {"status": cl.get("status", ""), "basis_state": cl.get("basis_state", ""),
                                             "reviewer_status": cl.get("reviewer_status"),
                                             "conditions_noted": bool(cl.get("conditions_noted")),
                                             "evidence": [e.get("quote", "")[:200] for e in (cl.get("evidence") or [])[:3]],
                                             "text": cl.get("text", "")}
    return JSONResponse(pred, headers={"Cache-Control": "no-store"})


@app.post("/review/release-plan")
async def release_plan_create(request: Request, _=Depends(require_reviewer)):
    from . import release_review
    try:
        return release_review.create(await request.json())
    except (ValueError, TypeError) as e:
        raise HTTPException(409, str(e))


@app.get("/review/release/{plan_id}", response_class=HTMLResponse)
def release_plan_view(request: Request, plan_id: str, error: str = "", _=Depends(require_reviewer)):
    from . import release_review
    try:
        plan=release_review.get(plan_id)
    except ValueError as e:
        raise HTTPException(404,str(e))
    response=render(request,"release_review.html",plan=plan,error=error)
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='same-origin'
    return response


@app.post("/review/release/{plan_id}/approve")
def release_plan_approve(request: Request, plan_id: str, csrf: str = Form(""), ack: str = Form(""), _=Depends(require_reviewer)):
    from . import release_review
    from urllib.parse import urlencode
    origin=request.headers.get('origin')
    allowed_origins={str(request.base_url).rstrip('/')}
    # Render terminates TLS before forwarding to this HTTP worker. Keep the
    # exact host/port and CSRF check while accepting its HTTPS public origin.
    if request.url.scheme=='http':
        allowed_origins.add('https://'+request.url.netloc)
    if origin and origin.rstrip('/') not in allowed_origins:
        raise HTTPException(403,'같은 사이트의 최종 확인 화면에서 승인해 주세요.')
    try:
        release_review.approve(plan_id,csrf,ack)
    except ValueError as e:
        return RedirectResponse(f'/review/release/{plan_id}?'+urlencode({'error':str(e)}),status_code=303)
    return RedirectResponse(f'/review/release/{plan_id}',status_code=303)


@app.post("/review/bulk_status")
def review_bulk_status(pid: list[int] = Form([]), status: str = Form(...), _=Depends(require_reviewer)):
    if status not in ("approved", "held"):
        raise HTTPException(400)
    for i in pid:
        db.set_product_status(i, status, "빠른 검토 화면에서 일괄 처리")
    return RedirectResponse("/review/queue", status_code=303)


@app.post("/review/revalidation-batch")
async def revalidation_batch_create(request: Request, _=Depends(require_reviewer)):
    from . import revalidation_batch
    try:
        return revalidation_batch.create(await request.json())
    except (ValueError,KeyError,TypeError) as exc:
        raise HTTPException(409,str(exc))


@app.get("/review/revalidation-batch/{ident}", response_class=HTMLResponse)
def revalidation_batch_page(request: Request, ident: str, _=Depends(require_reviewer)):
    from . import revalidation_batch
    try:
        return render(request,"revalidation_batch.html",batch=revalidation_batch.get(ident))
    except ValueError as exc:
        raise HTTPException(404,str(exc))


@app.post("/review/revalidation-batch/{ident}/approve")
def revalidation_batch_approve(ident: str, csrf: str=Form(""), ack: str=Form(""), _=Depends(require_reviewer)):
    from . import revalidation_batch
    try:
        revalidation_batch.approve(ident,csrf,ack)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return RedirectResponse(f"/review/revalidation-batch/{ident}",status_code=303)


@app.post("/review/revalidation")
async def revalidation_create(request: Request, _=Depends(require_reviewer)):
    from . import revalidation
    try:
        return revalidation.create(await request.json())
    except (ValueError,KeyError,TypeError) as exc:
        raise HTTPException(409,str(exc))


@app.get("/review/revalidation/{ident}", response_class=HTMLResponse)
def revalidation_page(request: Request, ident: str, _=Depends(require_reviewer)):
    from . import revalidation
    try:
        return render(request,"revalidation.html",plan=revalidation.get(ident))
    except ValueError as exc:
        raise HTTPException(404,str(exc))


@app.post("/review/revalidation/{ident}/approve")
def revalidation_approve(ident: str, csrf: str=Form(""), ack: str=Form(""), _=Depends(require_reviewer)):
    from . import revalidation
    try:
        revalidation.approve(ident,csrf,ack)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return RedirectResponse(f"/review/revalidation/{ident}",status_code=303)


@app.get("/review/history-audit.json")
def review_history_audit(_=Depends(require_reviewer)):
    """검토자 전용: 공개 카드의 저장 시각과 승인 로그를 읽기 전용으로 대조한다."""
    with db.connect() as con:
        products = [dict(r) for r in con.execute(
            "SELECT p.id,p.name,p.model,p.status,p.approved_at,p.updated_at,c.updated_at AS card_saved_at,c.card_json,c.pipeline_meta FROM products p JOIN cards c ON c.product_id=p.id WHERE p.status='approved' ORDER BY p.id")]
        for row in products:
            card = json.loads(row.pop("card_json"))
            meta = json.loads(row.pop("pipeline_meta") or "{}")
            row["checked_at"] = card.get("checked_at")
            row["claim_count"] = len(card.get("claims", []))
            row["release_approvals"] = meta.get("release_approvals", [])
            row["review_history"] = [dict(r) for r in con.execute(
                "SELECT action,detail,at FROM review_log WHERE product_id=? ORDER BY id", (row["id"],))]
    return JSONResponse({"cards": products}, headers={"Cache-Control":"no-store"})


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


@app.get("/review/blind/{pid}", response_class=HTMLResponse)
def review_blind_card(request: Request, pid: int, _=Depends(require_reviewer)):
    product = db.get_product(pid)
    card = db.get_card(pid)
    if not product or not card:
        raise HTTPException(404, "카드가 없습니다.")
    claims = [{"id": c.get("id", ""), "text": c.get("text", "")} for c in card.get("claims") or []]
    docs = [{"title": d.get("title"), "url": d.get("url"), "text": d.get("text") or ""}
            for d in db.list_documents(pid)]
    return render(request, "review_blind.html", rows=[], product=product, claims=claims, documents=docs,
                  pid_arg=str(pid))


@app.post("/review/{pid}/claim")
def review_claim(pid: int, claim_id: str = Form(...), reviewer_basis: str = Form(""),
                 reviewer_conditions: str = Form(""),
                 reviewer_note: str = Form(""), basis_kind: str = Form(""), basis_kind_global: str = Form(""),
                 reviewer_quote: str = Form(""), _=Depends(require_reviewer)):
    product, card, _ = card_view(pid)
    for c in card["claims"]:
        if c["id"] == claim_id:
            # 검토자가 원문에서 직접 찾은 근거 문장. AI가 못 찾은 설명을 사람이 붙일 때 쓴다.
            rq = reviewer_quote.strip()
            if rq:
                evs = [e for e in (c.get("evidence") or []) if e.get("kind") != "판매페이지(검토자 확인)"]
                c["evidence"] = [{"doc_id": None, "chunk": None, "quote": rq, "kind": "판매페이지(검토자 확인)"}] + evs
            # 검토자는 두 축을 따로 고른다. 확인상태 이름은 두 축에서 파생한다.
            if reviewer_basis in BASIS_STATES:
                c["reviewer_status"] = status_from(reviewer_basis, reviewer_conditions == "예")
            else:
                c["reviewer_status"] = None
            c["reviewer_note"] = reviewer_note.strip()
            c["basis_kind"] = basis_kind if basis_kind in BASIS_LABEL else None
            c["basis_kind_global"] = basis_kind_global if basis_kind_global in BASIS_LABEL else None
    db.save_card(pid, card, db.get_card_meta(pid))
    return RedirectResponse(f"/review/{pid}#claim-{claim_id}", status_code=303)


@app.post("/review/{pid}/claim/delete")
def review_claim_delete(pid: int, claim_id: str = Form(...), _=Depends(require_reviewer)):
    product, card, _ = card_view(pid)
    gone = [c["text"] for c in card["claims"] if c["id"] == claim_id]
    card["claims"] = [c for c in card["claims"] if c["id"] != claim_id]
    card["deleted_claims"] = (card.get("deleted_claims") or []) + gone  # 재생성 때 되살아나지 않도록
    db.save_card(pid, card, db.get_card_meta(pid))
    return RedirectResponse(f"/review/{pid}", status_code=303)


@app.post("/review/{pid}/ai-precheck")
async def review_ai_precheck(pid: int, request: Request, _=Depends(require_reviewer)):
    """Keep AI draft copy edits distinct from human review and publication."""
    from . import draft_precheck
    try:
        payload=await request.json()
        return draft_precheck.apply(pid,payload)
    except (ValueError,TypeError) as e:
        raise HTTPException(409,str(e))


@app.post("/review/{pid}/source-precheck")
async def review_source_precheck(pid: int, request: Request, _=Depends(require_reviewer)):
    from . import draft_precheck
    try:
        return draft_precheck.refresh_sources(pid,await request.json())
    except (ValueError,TypeError) as e:
        raise HTTPException(409,str(e))


@app.post("/review/{pid}/item")
def review_item(pid: int, key: str = Form(...), status: str = Form(...), summary: str = Form(""),
                server: str = Form(""), support_period: str = Form(""),
                _=Depends(require_reviewer)):
    product, card, _ = card_view(pid)
    if key in card["items"] and status in config.STATUSES:
        card["items"][key]["status"] = status
        card["items"][key]["summary"] = summary.strip()
        card["items"][key]["reviewed"] = True  # 재생성해도 이 항목은 보존
        # 항목 4는 처리 위치, 항목 6은 지원 기간 기재 여부를 따로 적는다.
        if key == "data":
            card["items"][key]["server"] = server if server in DATA_SERVER_LABEL else ""
        if key == "safety":
            card["items"][key]["support_period"] = support_period if support_period in SUPPORT_PERIOD_LABEL else ""
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


@app.post("/review/request/{rid}")
def review_request(rid: int, status: str = Form(...), _=Depends(require_reviewer)):
    if status not in ("done", "skipped", "new"):
        raise HTTPException(400)
    db.set_request_status(rid, status)
    return RedirectResponse("/review", status_code=303)


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
    docs = db.list_documents(pid)
    _enqueue_build(pid, [], product["name"], docs[0]["url"] if docs else "")  # 대기열로(요청 스레드를 잡지 않게)
    return RedirectResponse(f"/review/{pid}", status_code=303)


@app.get("/review/{pid}/dryrun.json")
def review_dryrun(pid: int, _=Depends(require_reviewer)):
    """저장하지 않고 카드를 다시 만들어 돌려준다. 2차 정확도 측정용(검토자 전용, LLM 호출 2회)."""
    from .pipeline.run import build_card_dry
    product = db.get_product(pid)
    if not product:
        raise HTTPException(404)
    card, meta = build_card_dry(pid)
    return JSONResponse({"product_id": pid, "card": card, "meta": meta}, headers={"Cache-Control": "no-store"})


@app.get("/review/{pid}/export.json")
def review_export(pid: int, _=Depends(require_reviewer)):
    """제품·문서·카드를 통째로 내려준다(검토자 전용). 다른 서버의 DB로 옮길 때 쓴다."""
    product = db.get_product(pid)
    if not product:
        raise HTTPException(404)
    with db.connect() as con:
        history = [dict(row) for row in con.execute(
            "SELECT action, detail, at FROM review_log WHERE product_id=? ORDER BY id", (pid,))]
        saved = con.execute("SELECT updated_at FROM cards WHERE product_id=?", (pid,)).fetchone()
    return JSONResponse({"product": product, "documents": db.list_documents(pid),
                         "card": db.get_card(pid), "meta": db.get_card_meta(pid),
                         "review_history": history,
                         "card_saved_at": saved["updated_at"] if saved else None},
                        headers={"Cache-Control": "no-store"})


@app.get("/review/{pid}/export_slim.json")
def review_export_slim(pid: int, _=Depends(require_reviewer)):
    """문서의 chunks 를 뺀 가벼운 내보내기(원문 text 는 포함). 로컬에서 다시 나눈다."""
    product = db.get_product(pid)
    if not product:
        raise HTTPException(404)
    docs = [{k: v for k, v in d.items() if k != "chunks"} for d in db.list_documents(pid)]
    return JSONResponse({"product": product, "documents": docs, "card": db.get_card(pid), "meta": db.get_card_meta(pid)},
                        headers={"Cache-Control": "no-store"})


@app.post("/review/collect")
async def collect_upload(request: Request):
    """브라우저(검토자 PC)에서 판매페이지 본문을 받아 저장한다. 다른 사이트 페이지에서 no-cors로 보내므로
    본문(text/plain JSON)의 token으로 검토자를 확인한다. 저장만 하며 카드는 round2/start에서 만든다."""
    from . import round2
    try:
        data = json.loads((await request.body()).decode("utf-8"))
    except Exception:
        raise HTTPException(400, "JSON 본문이 필요합니다.")
    if config.REVIEW_TOKEN and data.get("token") != config.REVIEW_TOKEN:
        raise HTTPException(403, "검토자 토큰이 필요합니다.")
    slug = re.sub(r"[^a-z0-9_]", "", str(data.get("slug", "")).lower())[:60]
    if not slug or not data.get("sales_text"):
        raise HTTPException(400, "slug와 sales_text가 필요합니다.")
    data.pop("token", None)
    round2.NEW_COLLECT_DIR.mkdir(parents=True, exist_ok=True)
    (round2.NEW_COLLECT_DIR / f"{slug}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return JSONResponse({"saved": slug})


@app.get("/review/collect/list.json")
def collect_list(_=Depends(require_reviewer)):
    from . import round2
    out = {}
    for f in sorted(round2.NEW_COLLECT_DIR.glob("*.json")):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            out[f.stem] = {"name": j.get("name"), "url": j.get("url"), "sales": len(j.get("sales_text", "")),
                           "fine": len(j.get("fine_text", "")), "images": len(j.get("image_urls") or [])}
        except Exception as e:
            out[f.stem] = {"error": str(e)[:100]}
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/review/collect/{slug}.json")
def collect_get(slug: str, _=Depends(require_reviewer)):
    from . import round2
    f = round2.NEW_COLLECT_DIR / f"{re.sub(r'[^a-z0-9_]', '', slug)}.json"
    if not f.exists():
        raise HTTPException(404, "없음")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})


@app.post("/review/round2/start")
async def round2_start(request: Request, _=Depends(require_reviewer)):
    """2차 수집 일괄 처리 시작(검토자 전용). JSON {only:[slug...], skip_dry:bool} 선택."""
    from . import round2
    try:
        data = await request.json()
    except Exception:
        data = {}
    ok = round2.start(only=data.get("only") or None, skip_dry=bool(data.get("skip_dry")),
                      redo=data.get("redo") or None, redo_dry=bool(data.get("redo_dry")),
                      rebuild=data.get("rebuild") or None, fix_spacing=data.get("fix_spacing", True) is not False)
    return JSONResponse({"started": ok, "status": round2.status()}, headers={"Cache-Control": "no-store"})


@app.get("/review/round2/status.json")
def round2_status(_=Depends(require_reviewer)):
    from . import round2
    return JSONResponse(round2.status(), headers={"Cache-Control": "no-store"})


@app.get("/review/round2/results.json")
def round2_results(_=Depends(require_reviewer)):
    from . import round2
    b = round2.results_bytes()
    if b is None:
        raise HTTPException(404, "아직 결과가 없습니다.")
    return Response(b, media_type="application/json", headers={"Cache-Control": "no-store"})


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
    w.writerow(["product_id", "product", "claim_id", "claim_text", "ai_status", "ai_basis",
                "ai_conditions_noted", "reviewer_status", "basis", "conditions_noted",
                "gold_status", "gold_evidence_quote", "labeler", "note"])
    for p in db.list_products():
        c = db.get_card(p["id"])
        if not c:
            continue
        for cl in c["claims"]:
            ab, ac = axes_of({k: v for k, v in cl.items() if k != "reviewer_status"})
            w.writerow([p["id"], p["name"], cl["id"], cl["text"], cl.get("status", ""),
                        ab, "예" if ac else "아니오",
                        cl.get("reviewer_status") or "", "", "", "", "", "", ""])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=claims_labels.csv"})
