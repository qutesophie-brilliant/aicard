"""One explicit human approval for immutable, individually inspected source rechecks."""
import json
import re
import secrets
from . import db, revalidation


def _table(con):
    con.execute('CREATE TABLE IF NOT EXISTS revalidation_batches (id TEXT PRIMARY KEY,payload TEXT NOT NULL,csrf TEXT NOT NULL,created_at TEXT NOT NULL,approved_at TEXT)')


def create(payload):
    if not isinstance(payload,dict) or set(payload) != {'title','ready','held'} or not isinstance(payload['ready'],list) or not 1 <= len(payload['ready']) <= 100 or not isinstance(payload['held'],list):
        raise ValueError('묶음 확인안의 형식이 올바르지 않습니다.')
    ids=[]
    for ident in payload['ready']:
        plan=revalidation.get(ident)
        if plan['approved_at']: raise ValueError('이미 승인된 확인안이 포함되어 있습니다.')
        ids.append(plan['payload']['product_id'])
    held_ids=[x['id'] for x in payload['held']]
    if len(set(ids+held_ids)) != len(ids+held_ids):
        raise ValueError('카드가 중복되거나 승인·보류에 동시에 포함되어 있습니다.')
    if any(not isinstance(x.get('reason'),str) or not x['reason'].strip() for x in payload['held']):
        raise ValueError('보류 사유를 확인해 주세요.')
    with db.connect() as con:
        _table(con)
        ident=secrets.token_hex(12)
        con.execute('INSERT INTO revalidation_batches VALUES(?,?,?,?,NULL)',(ident,json.dumps(payload,ensure_ascii=False),secrets.token_urlsafe(32),db.now()))
    return {'id':ident,'url':f'/review/revalidation-batch/{ident}','ready':len(ids),'held':len(held_ids),'applied':False}


def get(ident):
    if not re.fullmatch('[a-f0-9]{24}',ident): raise ValueError('확인안 번호가 올바르지 않습니다.')
    with db.connect() as con:
        _table(con)
        row=con.execute('SELECT * FROM revalidation_batches WHERE id=?',(ident,)).fetchone()
        if not row: raise ValueError('묶음 확인안을 찾지 못했습니다.')
        result=dict(row)
        result['payload']=json.loads(result['payload'])
    result['plans']=[revalidation.get(x) for x in result['payload']['ready']]
    return result


def approve(ident,csrf,ack):
    if ack != 'yes': raise ValueError('확인 내용을 읽고 동의해 주세요.')
    with db.connect() as con:
        _table(con)
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT * FROM revalidation_batches WHERE id=?',(ident,)).fetchone()
        if not row or not secrets.compare_digest(row['csrf'],csrf): raise ValueError('승인 화면을 다시 열어 주세요.')
        if row['approved_at']: return {'already_approved':True}
        payload=json.loads(row['payload'])
        for plan_id in payload['ready']:
            plan=con.execute('SELECT csrf,approved_at FROM revalidation_plans WHERE id=?',(plan_id,)).fetchone()
            if not plan or plan['approved_at']: raise ValueError('개별 확인안 상태가 바뀌었습니다. 아무 카드도 새로 승인하지 않았습니다.')
            revalidation.approve(plan_id,plan['csrf'],'yes',transaction=con)
        con.execute('UPDATE revalidation_batches SET approved_at=? WHERE id=?',(db.now(),ident))
    return {'approved':len(payload['ready']),'held_unchanged':len(payload['held'])}
