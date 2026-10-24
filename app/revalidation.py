"""Immutable source rechecks. Changes are applied only by authenticated final approval."""
import copy
import json
import re
import secrets
from contextlib import nullcontext
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
    required = {'product_id','expected_fingerprint','card','source','summary','changes','checks'}
    if set(payload)-{'review_scope'} != required or payload.get('review_scope') not in (None,'remedy'):
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
        if payload.get('review_scope') == 'remedy':
            def outside_remedy(card):
                rest = copy.deepcopy(card)
                rest.get('items',{}).pop('remedy',None)
                rest.get('easy',{}).get('items',{}).pop('remedy',None)
                return rest
            if outside_remedy(before) != outside_remedy(after) or not after.get('items',{}).get('remedy'):
                raise ValueError('문의·보증 부분 검토에서는 다른 항목을 변경할 수 없습니다.')
        if after.get('product') != before.get('product') or after.get('public_db') != before.get('public_db'):
            raise ValueError('제품 정보와 공공 DB 기록은 변경할 수 없습니다.')
        before_pairs = [(x['id'],x['status']) for x in before['claims']]
        after_pairs = [(x['id'],x['status']) for x in after['claims']]
        if after_pairs != before_pairs:
            exclusions = after.get('review_exclusions', [])
            old_claims = {x['id']:x for x in before['claims']}
            if not isinstance(exclusions,list) or not exclusions:
                raise ValueError('문구 제외에는 원문 근거와 명시적인 제외 기록이 필요합니다.')
            excluded_ids = []
            for exclusion in exclusions:
                if not isinstance(exclusion,dict) or set(exclusion) != {'id','text','reason','source_quote'}:
                    raise ValueError('문구 제외 기록의 형식이 올바르지 않습니다.')
                cid = exclusion['id']
                if cid not in old_claims or exclusion['text'] != old_claims[cid]['text'] or not isinstance(exclusion['reason'],str) or not exclusion['reason'].strip():
                    raise ValueError('제외할 기존 문구와 사유를 확인해 주세요.')
                q = re.sub(r'\s+', '', exclusion['source_quote'])
                if not q or q not in re.sub(r'\s+', '',src['text']):
                    raise ValueError('제외 사유의 현재 원문 근거를 확인해 주세요.')
                excluded_ids.append(cid)
            if len(set(excluded_ids)) != len(excluded_ids) or after_pairs != [pair for pair in before_pairs if pair[0] not in excluded_ids]:
                raise ValueError('명시한 문구만 제외할 수 있으며 문구 추가·판정 변경은 허용하지 않습니다.')
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


def approve(ident, csrf, ack, transaction=None):
    if ack != 'yes': raise ValueError('원문 대조안 확인에 동의해 주세요.')
    with (nullcontext(transaction) if transaction is not None else db.connect()) as con:
        _table(con)
        if not con.in_transaction:
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
        scope = payload.get('review_scope')
        if scope == 'remedy':
            card['items']['remedy']['reviewed'] = True
        else:
            for claim in card['claims']:
                claim['reviewer_status'] = claim['status']
                claim['reviewer_note'] = '현재 제조사 원문 대조안을 관리자가 최종 승인했습니다.'
            for item in card.get('items',{}).values(): item['reviewed'] = True
        at = db.now()
        meta = json.loads(c['pipeline_meta'] or '{}')
        detail = {'actor':'human','ai_assisted':True,'review_scope':scope or 'whole_card','plan_id':ident,'before':payload['before'],'approved_sha256':digest(card),'changes':payload['changes'],'prior_approved_at':p['approved_at'],'source_url':src['url'],'source_fetched_at':src['fetched_at']}
        meta['n_claims'] = len(card['claims'])
        meta.setdefault('revalidations',[]).append({k:v for k,v in detail.items() if k not in ('before','changes')})
        con.execute('UPDATE cards SET card_json=?,pipeline_meta=?,updated_at=? WHERE product_id=?',(json.dumps(card,ensure_ascii=False),json.dumps(meta,ensure_ascii=False),at,pid))
        if scope == 'remedy':
            con.execute('UPDATE products SET updated_at=?,reviewer_note=? WHERE id=?',(at,'문의·보증 항목만 현재 원문과 재대조·승인함. 다른 항목의 승인일은 갱신하지 않음',pid))
        else:
            con.execute('UPDATE products SET approved_at=?,updated_at=?,reviewer_note=? WHERE id=?',(at,at,'현재 제조사 원문 대조안을 최종 승인함',pid))
        con.execute('INSERT INTO review_log(product_id,action,detail,at) VALUES(?,?,?,?)',(pid,'human_item_reapproval' if scope else 'human_reapproval',json.dumps(detail,ensure_ascii=False),at))
        con.execute('UPDATE revalidation_plans SET approved_at=? WHERE id=?',(at,ident))
    return {'approved':pid}
