"""2차 수집 일괄 처리(서버 안에서 돌린다).

eval/collect/*.json(브라우저로 받은 판매페이지 본문)을 읽어 제품을 만들고, 상세 이미지 OCR → 카드 초안을 차례로 만든다.
끝나면 1차 정답 라벨에 있는 제품 10개를 저장 없이 다시 돌려(dryrun) 2차 정확도 측정용 결과를 함께 남긴다.
결과는 data/round2_results.json 한 파일에 모두 담아 /review/round2/results.json 으로 내려받는다.

LLM 호출 수(예상): 카드당 OCR 묶음 n회 + 4회, dryrun 제품당 2회.
"""
import glob
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import db
from .pipeline import ingest
from .pipeline.run import build_card, build_card_dry

ROOT = Path(__file__).resolve().parent.parent
COLLECT_DIR = ROOT / "eval" / "collect"
LABELS = ROOT / "eval" / "review_round1_labels.csv"
from .config import DB_PATH
RESULT_PATH = DB_PATH.parent / "round2_results.json"

_state: dict[str, Any] = {"running": False, "log": [], "pushed": {}, "done": False, "started_at": None}
_lock = threading.Lock()


STATE_PATH = RESULT_PATH.parent / "round2_state.json"
NEW_COLLECT_DIR = RESULT_PATH.parent / "collect_new"  # 3차 시험용 새 제품(브라우저에서 /review/collect로 받음)


def _log(msg: str) -> None:
    _state["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
    _state["log"] = _state["log"][-200:]
    try:  # 서버가 재시작돼도 어디까지 갔는지 남긴다
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        json.dump(_state, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def status() -> dict[str, Any]:
    out = {k: v for k, v in _state.items()}
    if not out.get("log") and STATE_PATH.exists():  # 재시작 뒤: 마지막 기록을 보여 준다
        try:
            prev = json.load(open(STATE_PATH, encoding="utf-8"))
            out["previous_run"] = {"started_at": prev.get("started_at"), "log": prev.get("log", [])[-15:],
                                   "pushed": prev.get("pushed", {}), "note": "서버가 재시작된 것으로 보여요. 이전 실행 기록이에요."}
        except Exception:
            pass
    return out


def _label_pids() -> list[int]:
    import csv
    if not LABELS.exists():
        return []
    return sorted({int(r["product_id"]) for r in csv.DictReader(open(LABELS, encoding="utf-8-sig"))})


SPACING_CACHE = RESULT_PATH.parent / "spacing_cache.json"
try:
    _spacing_cache: dict[str, str] = json.load(open(SPACING_CACHE, encoding="utf-8"))
except Exception:
    _spacing_cache = {}


def _save_spacing_cache() -> None:
    try:
        json.dump(_spacing_cache, open(SPACING_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _run(only: list[str] | None, skip_dry: bool, redo: list[str] | None = None, redo_dry: bool = False,
         rebuild: list[str] | None = None, fix_spacing: bool = True) -> None:
    try:
        results: dict[str, Any] = {"pushed": {}, "exports": {}, "dryrun": {}, "errors": {}}
        if RESULT_PATH.exists():
            try:
                results = json.load(open(RESULT_PATH, encoding="utf-8"))
            except Exception:
                pass
        # 다시 만들 제품: 기존 제품을 지우고 결과에서도 뺀다
        for slug in (redo or []):
            info = results["pushed"].pop(slug, None)
            if info:
                try:
                    db.delete_product(info["pid"])
                except Exception:
                    pass
                results["exports"].pop(str(info["pid"]), None)
                _log(f"{slug}: 다시 만들기(기존 제품 {info['pid']} 삭제)")
        if redo_dry:
            results["dryrun"] = {}
        # 카드만 다시 만들기(OCR 문서는 그대로, 추출·연결·권익·쉬운 말만 다시). 카드당 LLM 호출 약 5회
        for slug in (rebuild or []):
            info = results["pushed"].get(slug)
            if not info:
                continue
            try:
                # 문서를 새 규칙으로 다시 나눈다(OCR 띄어쓰기 보정·각주 분리). LLM 호출 없음
                for d in db.list_documents(info["pid"]):
                    ch = ingest.chunk_text(d["text"], ocr=d["title"].startswith("상세 이미지"))
                    if fix_spacing:  # 붙은 단어 띄어쓰기 보정(의심 문장만, 결과는 캐시)
                        ch = ingest.fix_spacing(ch, _spacing_cache)
                    db.update_document_chunks(d["id"], ch)
                _save_spacing_cache()
                _log(f"{slug}: 카드 다시 생성")
                build_card(info["pid"])
                results["exports"][str(info["pid"])] = {"product": db.get_product(info["pid"]), "documents": db.list_documents(info["pid"]),
                                                         "card": db.get_card(info["pid"]), "meta": db.get_card_meta(info["pid"])}
            except Exception as e:
                results["errors"][f"rebuild-{slug}"] = str(e)[:300]
                _log(f"{slug}: 다시 생성 실패 {e}")
            RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
            json.dump(results, open(RESULT_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        files = sorted(glob.glob(str(COLLECT_DIR / "*.json"))) + sorted(glob.glob(str(NEW_COLLECT_DIR / "*.json")))
        files = [f for f in files if Path(f).name not in ("pushed.json", "pulled.json")]
        _log(f"수집 파일 {len(files)}개")
        for f in files:
            slug = Path(f).stem
            if only and slug not in only:
                continue
            if slug in results["pushed"]:
                continue
            try:
                j = json.load(open(f, encoding="utf-8"))
                name = (j.get("name") or j.get("title") or slug)[:120]
                model_in = (j.get("model") or "").strip().lower()
                dup = next((p for p in db.list_products() if model_in and (p.get("model") or "").strip().lower() == model_in and p.get("status") in ("approved", "pending")), None)
                if dup:
                    _log(f"{slug}: 같은 모델명 카드가 이미 있어 건너뜀(pid {dup['id']})")
                    results["errors"][slug] = f"duplicate model: pid {dup['id']}"
                    continue
                pid = db.create_product(name, j.get("maker", "")[:60], j.get("model", "")[:60], j.get("category", "로봇청소기")[:30])
                url = (j.get("url") or "")[:500]
                for kind, key, title in (("판매페이지", "sales_text", "판매페이지 본문"), ("사양표", "fine_text", "판매페이지 각주·사양")):
                    text = (j.get(key) or "").strip()
                    if text:
                        ing = ingest.from_text(title, text)
                        if ing.chunks:
                            db.add_document(pid, kind, f"{title} ({name})", url, ing.text, ing.chunks)
                imgs = [u for u in (j.get("image_urls") or []) if isinstance(u, str) and u.startswith("http")][:60]
                notes: list[str] = []
                if imgs:
                    _log(f"{slug}: 이미지 {len(imgs)}장 OCR")
                    ing = ingest.from_image_urls(f"상세 이미지 ({name})", imgs)
                    notes = ing.notes
                    if ing.chunks:
                        db.add_document(pid, "판매페이지", ing.title, url, ing.text, ing.chunks)
                if not db.list_documents(pid):
                    db.set_product_status(pid, "draft", "카드 생성 실패: 읽을 수 있는 글자가 없습니다.")
                    results["errors"][slug] = "no text"
                    continue
                _log(f"{slug}: 카드 생성")
                build_card(pid)
                note = ("대조군(‘AI’를 내세우지 않는 제품). " if j.get("control") else "") + "2차 수집 — 검토 대기"
                db.set_product_status(pid, "pending", note)
                results["pushed"][slug] = {"pid": pid, "control": bool(j.get("control")), "notes": notes[:5]}
                results["exports"][str(pid)] = {"product": db.get_product(pid), "documents": db.list_documents(pid),
                                                "card": db.get_card(pid), "meta": db.get_card_meta(pid)}
                _state["pushed"][slug] = pid
            except Exception as e:  # 한 제품 실패해도 계속
                results["errors"][slug] = str(e)[:300]
                _log(f"{slug}: 실패 {e}")
            RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
            json.dump(results, open(RESULT_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        if not skip_dry:
            for pid in _label_pids():
                if str(pid) in results["dryrun"]:
                    continue
                try:
                    _log(f"dryrun 제품 {pid}")
                    card, meta = build_card_dry(pid)
                    results["dryrun"][str(pid)] = {"card": card, "meta": meta}
                except Exception as e:
                    results["errors"][f"dry-{pid}"] = str(e)[:300]
                json.dump(results, open(RESULT_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        _log("완료")
        _state["done"] = True
    finally:
        _state["running"] = False


def start(only: list[str] | None = None, skip_dry: bool = False, redo: list[str] | None = None, redo_dry: bool = False,
          rebuild: list[str] | None = None, fix_spacing: bool = True) -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, done=False, started_at=time.strftime("%Y-%m-%d %H:%M:%S"), log=[])
    threading.Thread(target=_run, args=(only, skip_dry, redo, redo_dry, rebuild, fix_spacing), daemon=True).start()
    return True


def results_bytes() -> bytes | None:
    if not RESULT_PATH.exists():
        return None
    return RESULT_PATH.read_bytes()
