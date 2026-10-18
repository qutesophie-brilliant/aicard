"""Immutable source rechecks. Changes are applied only by authenticated final approval."""
import copy
import json
import re
import secrets
from datetime import datetime, timezone
from . import db
from .draft_precheck import digest, sources_digest
from .release_review import FROZEN


def _table(con):
    con.execute('CREATE TABLE IF NOT EXISTS revalidation_plans (id TEXT PRIMARY KEY,payload TEXT NOT NULL,csrf TEXT NOT NULL,created_at TEXT NOT NULL,approved_at TEXT)')


def snapshot(con, pid):
    p = con.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
    c = con.execute('SELECT * FROM cards WHERE product_id=?', (pid,)).fetchone()
    docs = [dict(r) for r in con.execute('SELECT * FROM documents WHERE product_id=? ORDER BY id', (pid,))]
    if not p or not c or p['status'] != 'approved' or pid in FROZEN:
        raise ValueError('공개 카드만 재검토할 수 있으며 연구용 고정 카드는 제외됩니다.')
    return dict(p), dict(c), docs


def fingerprint(p, c, docs):
    return digest({'card':json.loads(c['card_json']), 'meta':json.loads(c['pipeline_meta'] or '{}'), 'card_saved_at':c['updated_at'], 'sources':sources_digest(docs), 'status':p['status'], 'approved_at':p['approved_at']})


def create(payload):
    payload = copy.deepcopy(payload)
    if set(payload) != {'product_id','expected_fingerprint','card','source','summary','changes','checks'}:
        raise ValueError('재검토안 형식이 올바르지 않습니다.')
    pid = payload['product_id']
    src = payload['source']
    if type(pid) is not int or set(src) != {'kind','title','url','fetched_at','text'} or src['kind'] != '판매페이지' or not src['url'].startswith('https://') or not 100 <= len(src['text']) <= 200000:
        raise ValueError('대상과 현재 원문을 확인해 주세요.')
    with db.connect() as con:
        _table(con)
        p, c, docs = snapshot(con, pid)
        if fingerprint(p,c,docs) != payload['expected_fingerprint']:
            raise ValueError('카드나 원문이 변경됐습니다. 다시 대조해 주세요.')
        before = json.loads(c['card_json'])
        after = payload['card']
        if after.get('product') != before.get('product') or after.get('public_db') != before.get('public_db'):
            raise ValueError('제품 정보와 공공 DB 기록은 변경할 수 없습니다.')
        if [(x['id'],x['status']) for x in after['claims']] != [(x['id'],x['status']) for x in before['claims']]:
            raise ValueError('기존 문구 수·판정 상태는 변경할 수 없습니다.')
        def validate_quotes(obj):
            if isinstance(obj,dict):
                if obj.get('doc_id') == -1:
                    quote = re.sub(r'\s+', '', obj.get('quote',''))
                    if not quote or quote not in re.sub(r'\s+', '',src['text']):
                        raise ValueError('현재 원문에서 찾지 못한 인용이 있습니다.')
                for val in obj.values(): validate_quotes(val)
            elif isinstance(obj,list):
                for val in obj: validate_quotes(val)
        validate_quotes(after)
        payload.update(before=before,product={k:p[k] for k in ('id','name','model')},prior_approved_at=p['approved_at'],prior_saved_at=c['updated_at'])
        ident = secrets.token_hex(12)
        con.execute('INSERT INTO revalidation_plans VALUES(?,?,?,?,NULL)',(ident,json.dumps(payload,ensure_ascii=False),secrets.token_urlsafe(32),datetime.now(timezone.utc).isoformat()))
    return {'id':ident,'url':f'/review/revalidation/{ident}','applied':False}


def get(ident):
    if not re.fullmatch('[a-f0-9]{24}',ident): raise ValueError('재검토안 번호가 올바르지 않습니다.')
    with db.connect() as con:
        _table(con)
        row = con.execute('SELECT * FROM revalidation_plans WHERE id=?',(ident,)).fetchone()
        if not row: raise ValueError('재검토안을 찾지 못했습니다.')
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        return result


def approve(ident, csrf, ack):
    if ack != 'yes': raise ValueError('원문 대조안 확인에 동의해 주세요.')
    with db.connect() as con:
        _table(con)
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM revalidation_plans WHERE id=?',(ident,)).fetchone()
        if not row or not secrets.compare_digest(row['csrf'],csrf): raise ValueError('승인 화면을 다시 열어 주세요.')
        if row['approved_at']: return {'already_approved':True}
        payload = json.loads(row['payload'])
        pid = payload['product_id']
        p,c,docs = snapshot(con,pid)
        if fingerprint(p,c,docs) != payload['expected_fingerprint']:
            raise ValueError('카드 또는 원문이 변경돼 승인을 중단했습니다. 아무 내용도 반영하지 않았습니다.')
        src = payload['source']
        from .pipeline.ingest import chunk_text
        chunks = chunk_text(src['text'])
        cur = con.execute('INSERT INTO documents(product_id,kind,title,url,fetched_at,text,chunks_json) VALUES(?,?,?,?,?,?,?)',(pid,src['kind'],src['title'],src['url'],src['fetched_at'],src['text'],json.dumps(chunks,ensure_ascii=False)))
        did = cur.lastrowid
        card = copy.deepcopy(payload['card'])
        def replace_ids(obj):
            if isinstance(obj,dict):
                if obj.get('doc_id') == -1: obj['doc_id'] = did
                for val in obj.values(): replace_ids(val)
            elif isinstance(obj,list):
                for val in obj: replace_ids(val)
        replace_ids(card)
        card['sources'] = payload['before']['sources'] + [{'doc_id':did,'kind':src['kind'],'title':src['title'],'url':src['url'],'n_chunks':len(chunks)}]
        for claim in card['claims']:
            claim['reviewer_status'] = claim['status']
            claim['reviewer_note'] = '현재 제조사 원문 대조안을 관리자가 최종 승인했습니다.'
        for item in card.get('items',{}).values(): item['reviewed'] = True
        at = db.now()
        meta = json.loads(c['pipeline_meta'] or '{}')
        detail = {'actor':'human','ai_assisted':True,'plan_id':ident,'before':payload['before'],'approved_sha256':digest(card),'changes':payload['changes'],'prior_approved_at':p['approved_at'],'source_url':src['url'],'source_fetched_at':src['fetched_at']}
        meta.setdefault('revalidations',[]).append({k:v for k,v in detail.items() if k not in ('before','changes')})
        con.execute('UPDATE cards SET card_json=?,pipeline_meta=?,updated_at=? WHERE product_id=?',(json.dumps(card,ensure_ascii=False),json.dumps(meta,ensure_ascii=False),at,pid))
        con.execute('UPDATE products SET approved_at=?,updated_at=?,reviewer_note=? WHERE id=?',(at,at,'현재 제조사 원문 대조안을 최종 승인함',pid))
        con.execute('INSERT INTO review_log(product_id,action,detail,at) VALUES(?,?,?,?)',(pid,'human_reapproval',json.dumps(detail,ensure_ascii=False),at))
        con.execute('UPDATE revalidation_plans SET approved_at=? WHERE id=?',(at,ident))
    return {'approved':pid}
