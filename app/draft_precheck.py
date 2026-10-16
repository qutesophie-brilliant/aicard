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


def sources_digest(docs):
    return digest([{k:d[k] for k in ("id","kind","text")} for d in sorted(docs,key=lambda d:d["id"])])


def refresh_sources(pid,payload):
    """Correct source scope only before the queue worker has claimed this draft."""
    from .pipeline import ingest
    if not isinstance(payload,dict) or set(payload)!={"expected_model","expected_sources_sha256","sales_text","fine_text","note"}:
        raise ValueError("출처 수정 항목이 올바르지 않습니다.")
    if any(not isinstance(payload[k],str) for k in payload) or not payload["sales_text"].strip() or len(payload["sales_text"])+len(payload["fine_text"])>250000:
        raise ValueError("본문 형식이 올바르지 않습니다.")
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        p=con.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
        jobs=con.execute("SELECT status FROM build_queue WHERE product_id=?",(pid,)).fetchall()
        docs=[dict(d) for d in con.execute("SELECT * FROM documents WHERE product_id=?",(pid,))]
        card=con.execute("SELECT 1 FROM cards WHERE product_id=?",(pid,)).fetchone()
        if not p or p["status"]!="draft" or p["approved_at"] or p["model"]!=payload["expected_model"] or card or not jobs or any(j["status"]!="new" for j in jobs):
            raise ValueError("아직 생성하지 않은 대기열 초안만 출처를 정리할 수 있습니다.")
        if sources_digest(docs)!=payload["expected_sources_sha256"]:raise ValueError("출처가 변경되었습니다.")
        if {d["kind"] for d in docs}-{"판매페이지","사양표"} or len({d['kind'] for d in docs})!=len(docs):
            raise ValueError("별도 문서가 있어 자동으로 바꾸지 않습니다.")
        for d in docs:
            text=payload['sales_text' if d['kind']=='판매페이지' else 'fine_text']
            chunks=ingest.chunk_text(text)
            con.execute("UPDATE documents SET text=?,chunks_json=? WHERE id=?",(text,json.dumps(chunks,ensure_ascii=False),d['id']))
        con.execute("INSERT INTO review_log(product_id,action,detail,at) VALUES(?,?,?,?)",
                    (pid,"ai_source_precheck",json.dumps({"before":docs,"note":payload["note"],"actor":"AI"},ensure_ascii=False),db.now()))
        con.execute("UPDATE products SET reviewer_note=?,updated_at=? WHERE id=?",("AI 사전점검으로 출처 범위를 정리했습니다. 초안 생성 후 사람의 원문 검토가 필요합니다.",db.now(),pid))
    return {"id":pid,"status":"draft","human_reviewed":False}


def apply(pid, payload):
    allowed={"expected_sha256","claim_text_updates","claim_context_updates","remove_claim_ids","restore_claims","item_updates","easy_updates","public_db_updates","note"}
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
        restores=payload.get("restore_claims",[])
        if not isinstance(restores,list):raise ValueError("복원 문구 형식이 올바르지 않습니다.")
        if restores:
            prior=[]
            for log in con.execute("SELECT detail FROM review_log WHERE product_id=? AND action='ai_precheck'",(pid,)):
                prior.extend(json.loads(log[0]).get('before',{}).get('claims',[]))
            valid={digest(c) for c in prior}
            for cl in restores:
                if not isinstance(cl,dict) or cl.get('id') in by_id or digest(cl) not in valid:
                    raise ValueError("이 초안의 AI 사전점검 기록에 있는 문구만 복원할 수 있습니다.")
                cl=copy.deepcopy(cl);card['claims'].append(cl);by_id[cl['id']]=cl
                card['deleted_claims']=[t for t in card.get('deleted_claims',[]) if t!=cl['text']]
        removals=payload.get("remove_claim_ids",[])
        updates=payload.get("claim_text_updates",{})
        if not isinstance(removals,list) or not isinstance(updates,dict) or any(x not in by_id for x in list(removals)+list(updates)):
            raise ValueError("문구 ID가 올바르지 않습니다.")
        if set(removals)&set(updates):raise ValueError("같은 문구를 삭제하고 수정할 수 없습니다.")
        docs={d["id"]:d for d in con.execute("SELECT * FROM documents WHERE product_id=?",(pid,))}
        contexts=payload.get("claim_context_updates",{})
        if not isinstance(contexts,dict) or any(cid not in by_id or cid in removals for cid in contexts):
            raise ValueError("문구 설명 수정 ID가 올바르지 않습니다.")
        for cid,context in contexts.items():
            # Explanations and source links may be corrected by AI, but the
            # original judgment axes and every human-review field stay intact.
            if not isinstance(context,dict) or set(context)!={"note","evidence"}:
                raise ValueError("문구 설명과 인용만 수정할 수 있습니다.")
            if not isinstance(context['note'],str) or not 1<=len(context['note'])<=1000 or not isinstance(context['evidence'],list) or not 1<=len(context['evidence'])<=8:
                raise ValueError("문구 설명·인용 형식이 올바르지 않습니다.")
            evidence=[]
            for ev in context['evidence']:
                if not isinstance(ev,dict) or set(ev)!={'doc_id','quote'} or not isinstance(ev['quote'],str) or not 5<=len(ev['quote'])<=1500:
                    raise ValueError("인용 형식이 올바르지 않습니다.")
                doc=docs.get(ev['doc_id'])
                if doc is None or _norm(ev['quote']) not in _norm(doc['text']):
                    raise ValueError("인용이 같은 제품의 원문에 없습니다.")
                prefix=_norm(ev['quote'])[:20]
                idx=next((i for i,q in enumerate(json.loads(doc['chunks_json'])) if prefix in _norm(q)),None)
                if idx is None:raise ValueError("인용 시작 위치를 찾을 수 없습니다.")
                evidence.append({'doc_id':ev['doc_id'],'quote':ev['quote'],'chunk':idx,'kind':doc['kind']})
            by_id[cid]['note']=context['note'];by_id[cid]['evidence']=evidence
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
        item_updates=payload.get('item_updates',{})
        if not isinstance(item_updates,dict) or set(item_updates)-set(card.get('items',{})):
            raise ValueError("권익 항목이 올바르지 않습니다.")
        for key,item in item_updates.items():
            if not isinstance(item,dict) or set(item)!={'status','summary','evidence'} or item['status'] not in {'근거 확인','조건 확인','자료 부족'}:
                raise ValueError("자동 초안 항목 형식이 올바르지 않습니다.")
            if not isinstance(item['summary'],str) or not 1<=len(item['summary'])<=1000 or not isinstance(item['evidence'],list) or len(item['evidence'])>8:
                raise ValueError("항목 설명·인용 형식이 올바르지 않습니다.")
            if item['status']!='자료 부족' and not item['evidence']:raise ValueError("근거 인용 없이 확인 상태를 표시할 수 없습니다.")
            evidence=[]
            for ev in item['evidence']:
                if not isinstance(ev,dict) or set(ev)!={'doc_id','quote'} or not isinstance(ev['quote'],str) or not 5<=len(ev['quote'])<=1500:
                    raise ValueError("인용 형식이 올바르지 않습니다.")
                doc=docs.get(ev['doc_id'])
                if doc is None or _norm(ev['quote']) not in _norm(doc['text']):raise ValueError("인용이 같은 제품의 원문에 없습니다.")
                chunks=json.loads(doc['chunks_json']);prefix=_norm(ev['quote'])[:20]
                idx=next((i for i,q in enumerate(chunks) if prefix in _norm(q)),None)
                if idx is None:raise ValueError("인용 시작 위치를 찾을 수 없습니다.")
                evidence.append({'doc_id':ev['doc_id'],'quote':ev['quote'],'chunk':idx,'kind':doc['kind']})
            card['items'][key]={'status':item['status'],'summary':item['summary'],'evidence':evidence}
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
        public=payload.get('public_db_updates',{})
        if not isinstance(public,dict) or set(public)-{'kc','rra','energy','recall'}:
            raise ValueError('공공 DB 항목이 올바르지 않습니다.')
        from urllib.parse import urlsplit
        official_hosts={'www.safetykorea.kr','safetykorea.kr','www.rra.go.kr','rra.go.kr','eep.energy.or.kr'}
        for key,entry in public.items():
            if not isinstance(entry,dict) or set(entry)-{'found','number','grade','detail','url'} or 'found' not in entry or (entry['found'] is not None and type(entry['found']) is not bool):
                raise ValueError('공공 DB 조회 상태가 올바르지 않습니다.')
            if any(not isinstance(v,str) or len(v)>2000 for k,v in entry.items() if k!='found'):
                raise ValueError('공공 DB 조회 설명 형식이 올바르지 않습니다.')
            url=entry.get('url','')
            if url and (urlsplit(url).scheme!='https' or urlsplit(url).hostname not in official_hosts):
                raise ValueError('공식 공공 DB의 HTTPS 주소만 연결할 수 있습니다.')
            if not entry.get('detail','').strip():raise ValueError('조회일과 모델·검색 범위를 설명해야 합니다.')
            card.setdefault('public_db',{}).setdefault(key,{}).update(entry)
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
