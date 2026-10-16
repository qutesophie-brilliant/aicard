"""Audited AI copy edits to unpublished drafts; never a human approval."""
import copy
import hashlib
import json
import re
from . import db


def digest(card):
    return hashlib.sha256(json.dumps(card,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def apply(pid, payload):
    allowed={"expected_sha256","claim_text_updates","remove_claim_ids","easy_updates","note"}
    if not isinstance(payload,dict) or set(payload)-allowed:
        raise ValueError("허용되지 않은 초안 수정 항목입니다.")
    if not isinstance(payload.get("note"),str) or not payload["note"].strip():
        raise ValueError("AI 사전점검 수정 이유가 필요합니다.")
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        p=con.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
        row=con.execute("SELECT * FROM cards WHERE product_id=?",(pid,)).fetchone()
        if not p or not row or p["status"]!="pending" or p["approved_at"]:
            raise ValueError("공개 이력이 없는 검토 대기 초안만 수정할 수 있습니다.")
        before=json.loads(row["card_json"])
        if digest(before)!=payload.get("expected_sha256"):
            raise ValueError("초안이 변경되었습니다. 다시 읽은 뒤 수정해 주세요.")
        if any(c.get("reviewer_status") or c.get("reviewer_note") for c in before.get("claims",[])) or any(i.get("reviewed") for i in before.get("items",{}).values()):
            raise ValueError("사람의 수정 기록이 있어 자동으로 덮어쓰지 않습니다.")
        card=copy.deepcopy(before)
        by_id={c["id"]:c for c in card.get("claims",[])}
        removals=payload.get("remove_claim_ids",[])
        updates=payload.get("claim_text_updates",{})
        if not isinstance(removals,list) or not isinstance(updates,dict) or any(x not in by_id for x in list(removals)+list(updates)):
            raise ValueError("문구 ID가 올바르지 않습니다.")
        if set(removals)&set(updates):raise ValueError("같은 문구를 삭제하고 수정할 수 없습니다.")
        docs={d["id"]:d for d in con.execute("SELECT * FROM documents WHERE product_id=?",(pid,))}
        for cid,text in updates.items():
            if not isinstance(text,str) or not 5<=len(text)<=500:raise ValueError("문구 길이가 올바르지 않습니다.")
            cl=by_id[cid];src=cl.get("source") or {};doc=docs.get(src.get("doc_id"))
            if doc is None or _norm(text) not in _norm(doc["text"]):
                raise ValueError("수정 문구가 같은 출처 문서의 원문과 일치하지 않습니다.")
            cl["text"]=text;cl["source"]={**src,"quote":text}
        card["claims"]=[c for c in card.get("claims",[]) if c["id"] not in removals]
        if not card["claims"]:raise ValueError("모든 문구를 지우는 수정은 이 경로에서 지원하지 않습니다.")
        if removals:
            card["deleted_claims"]=(card.get("deleted_claims") or [])+[by_id[x]["text"] for x in removals]
        easy=payload.get("easy_updates",{})
        if not isinstance(easy,dict) or set(easy)-{"headline","items"}:raise ValueError("쉬운 말 수정 항목이 올바르지 않습니다.")
        if "headline" in easy:
            if not isinstance(easy["headline"],str) or len(easy["headline"])>300:raise ValueError("제목 형식이 올바르지 않습니다.")
            card.setdefault("easy",{})["headline"]=easy["headline"]
        if "items" in easy:
            old=card.setdefault("easy",{}).setdefault("items",{})
            if not isinstance(easy["items"],dict) or set(easy["items"])-set(old):raise ValueError("쉬운 말 항목이 올바르지 않습니다.")
            if any(not isinstance(v,str) or len(v)>500 for v in easy["items"].values()):raise ValueError("쉬운 말 형식이 올바르지 않습니다.")
            old.update(easy["items"])
        meta=json.loads(row["pipeline_meta"] or "{}")
        entry={"actor":"AI","at":db.now(),"note":payload["note"][:2000],"human_reviewed":False,"before_sha256":digest(before),"after_sha256":digest(card)}
        meta.setdefault("ai_prechecks",[]).append(entry)
        con.execute("UPDATE cards SET card_json=?,pipeline_meta=?,updated_at=? WHERE product_id=?",
                    (json.dumps(card,ensure_ascii=False),json.dumps(meta,ensure_ascii=False),db.now(),pid))
        # Retain the complete pre-edit card so the change is reversible and auditable.
        con.execute("INSERT INTO review_log(product_id,action,detail,at) VALUES(?,?,?,?)",
                    (pid,"ai_precheck",json.dumps({"before":before,"change":payload,"actor":"AI"},ensure_ascii=False),db.now()))
        con.execute("UPDATE products SET reviewer_note=?,updated_at=? WHERE id=?",
                    ("AI 사전점검으로 초안 문구를 정리했습니다. 사람의 원문 검토와 공개 승인은 아직 필요합니다.",db.now(),pid))
    return {"id":pid,"status":"pending","human_reviewed":False,"sha256":digest(card)}
