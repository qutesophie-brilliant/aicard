"""Immutable AI review proposals, accepted only by an authenticated human action."""
import copy
import json
import re
import secrets
from . import db
from .draft_precheck import digest, sources_digest
from .schema import BASIS_STATES, status_from

# Research snapshots and already published cards never enter this workflow.
FROZEN = {268,274,281,284,295,307,314,318,324,329}


def _table(con):
    con.execute("CREATE TABLE IF NOT EXISTS release_review_plans (id TEXT PRIMARY KEY, payload TEXT NOT NULL, csrf TEXT NOT NULL, created_at TEXT NOT NULL, approved_at TEXT)")


def _snapshot(con, pid):
    p=con.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
    c=con.execute("SELECT * FROM cards WHERE product_id=?",(pid,)).fetchone()
    docs=[dict(x) for x in con.execute("SELECT * FROM documents WHERE product_id=? ORDER BY id",(pid,))]
    if not p or not c or pid in FROZEN or p['status']!='pending' or p['approved_at']:
        raise ValueError(f"카드 {pid}: 공개 이력이 없는 검토 대기 카드만 포함할 수 있습니다.")
    card=json.loads(c['card_json'])
    if any(x.get('reviewer_status') or x.get('reviewer_note') for x in card.get('claims',[])) or any(x.get('reviewed') for x in card.get('items',{}).values()):
        raise ValueError(f"카드 {pid}: 기존 사람 검토 기록을 덮어쓸 수 없습니다.")
    return dict(p),card,json.loads(c['pipeline_meta'] or '{}'),docs


def create(payload):
    if not isinstance(payload,dict) or set(payload)!={'title','summary','cards'} or not isinstance(payload['cards'],list) or not 1<=len(payload['cards'])<=100:
        raise ValueError('최종 확인안의 형식이 올바르지 않습니다.')
    if not isinstance(payload['title'],str) or not 1<=len(payload['title'])<=150 or not isinstance(payload['summary'],str) or len(payload['summary'])>4000:
        raise ValueError('제목과 요약을 확인해 주세요.')
    payload=copy.deepcopy(payload);seen=set()
    with db.connect() as con:
        _table(con);con.execute('BEGIN IMMEDIATE')
        for row in payload['cards']:
            if not isinstance(row,dict) or set(row)!={'id','expected_sha256','expected_sources_sha256','recommendation','reason','decisions'}:
                raise ValueError('카드 확인안의 형식이 올바르지 않습니다.')
            pid=row['id']
            if type(pid) is not int or pid in seen:raise ValueError('카드 번호가 중복되거나 올바르지 않습니다.')
            seen.add(pid)
            p,card,meta,docs=_snapshot(con,pid)
            if digest(card)!=row['expected_sha256'] or sources_digest(docs)!=row['expected_sources_sha256']:raise ValueError(f'카드 {pid}가 변경되었습니다.')
            if row['recommendation'] not in ('ready','hold') or not isinstance(row['reason'],str) or not 1<=len(row['reason'])<=2000:raise ValueError('공개 권고 사유를 확인해 주세요.')
            claims={x['id']:x for x in card['claims']};cids=set()
            if not isinstance(row['decisions'],list):raise ValueError('문구 판정안은 목록이어야 합니다.')
            for decision in row['decisions']:
                if not isinstance(decision,dict) or set(decision)!={'claim_id','basis','conditions_noted','reason'}:raise ValueError('문구 판정안 형식이 올바르지 않습니다.')
                cid=decision['claim_id']
                if cid not in claims or cid in cids:raise ValueError('문구 번호가 중복되거나 없습니다.')
                cids.add(cid)
                if decision['basis'] not in BASIS_STATES or type(decision['conditions_noted']) is not bool or not isinstance(decision['reason'],str) or not 1<=len(decision['reason'])<=1500:raise ValueError('문구 판정 근거를 확인해 주세요.')
                if decision['basis']!='있음' and decision['conditions_noted']:raise ValueError('설명이 없는 경우 조건 축을 부여하지 않습니다.')
                decision['text']=claims[cid]['text'];decision['before_status']=claims[cid]['status'];decision['proposed_status']=status_from(decision['basis'],decision['conditions_noted'])
                decision['evidence']=claims[cid].get('evidence',[])
            row['product']={k:p[k] for k in ('name','maker','model','category')};row['claim_count']=len(card['claims']);row['public_db']=card.get('public_db',{})
        plan_id=secrets.token_hex(12)
        con.execute('INSERT INTO release_review_plans(id,payload,csrf,created_at) VALUES(?,?,?,?)',(plan_id,json.dumps(payload,ensure_ascii=False),secrets.token_urlsafe(32),db.now()))
    return {'id':plan_id,'ready':sum(r['recommendation']=='ready' for r in payload['cards']),'held':sum(r['recommendation']=='hold' for r in payload['cards']),'published':False,'url':f'/review/release/{plan_id}'}


def get(plan_id):
    if not re.fullmatch(r'[a-f0-9]{24}',plan_id):raise ValueError('확인안 번호가 올바르지 않습니다.')
    with db.connect() as con:
        _table(con);r=con.execute('SELECT * FROM release_review_plans WHERE id=?',(plan_id,)).fetchone()
        if not r:raise ValueError('최종 확인안을 찾지 못했습니다.')
        out=dict(r);out['payload']=json.loads(out['payload']);out['ready']=[x for x in out['payload']['cards'] if x['recommendation']=='ready'];out['held']=[x for x in out['payload']['cards'] if x['recommendation']=='hold']
        return out


def approve(plan_id,csrf,ack):
    if ack!='yes':raise ValueError('확인 내용에 동의한 뒤 최종 승인해 주세요.')
    with db.connect() as con:
        _table(con);con.execute('BEGIN IMMEDIATE')
        r=con.execute('SELECT * FROM release_review_plans WHERE id=?',(plan_id,)).fetchone()
        if not r or not secrets.compare_digest(r['csrf'],csrf):raise ValueError('확인 화면을 다시 열어 주세요.')
        if r['approved_at']:return {'already_approved':True}
        payload=json.loads(r['payload']);ready=[x for x in payload['cards'] if x['recommendation']=='ready']
        if not ready:raise ValueError('공개 가능한 카드가 없습니다.')
        prepared=[]
        for row in ready:
            p,card,meta,docs=_snapshot(con,row['id'])
            if digest(card)!=row['expected_sha256'] or sources_digest(docs)!=row['expected_sources_sha256']:
                raise ValueError(f"카드 {row['id']}의 내용이나 원문이 변경되었습니다. 재검토 후 새 확인안을 만들어 주세요. 아무 카드도 공개하지 않았습니다.")
            prepared.append((row,card,meta))
        at=db.now()
        for row,card,meta in prepared:
            before=copy.deepcopy(card);by={x['id']:x for x in card['claims']}
            for decision in row['decisions']:
                cl=by[decision['claim_id']]
                cl['reviewer_status']=decision['proposed_status']
                cl['reviewer_note']='AI 사전점검안을 최종 승인했습니다. '+decision['reason']
            for item in card.get('items',{}).values():item['reviewed']=True
            meta.setdefault('release_approvals',[]).append({'plan_id':plan_id,'actor':'human','method':'authenticated_final_approval','ai_assisted':True,'at':at})
            con.execute('UPDATE cards SET card_json=?,pipeline_meta=?,updated_at=? WHERE product_id=?',(json.dumps(card,ensure_ascii=False),json.dumps(meta,ensure_ascii=False),at,row['id']))
            con.execute("UPDATE products SET status='approved',approved_at=?,updated_at=?,reviewer_note=? WHERE id=?",(at,at,'AI 사전점검안과 원문·공공 DB 조회 범위를 확인한 뒤 최종 승인',row['id']))
            con.execute('INSERT INTO review_log(product_id,action,detail,at) VALUES(?,?,?,?)',(row['id'],'human_release_approval',json.dumps({'actor':'human','ai_assisted':True,'plan_id':plan_id,'before':before,'accepted_proposals':row['decisions']},ensure_ascii=False),at))
        con.execute('UPDATE release_review_plans SET approved_at=? WHERE id=?',(at,plan_id))
    return {'approved':len(ready),'held_unchanged':len(payload['cards'])-len(ready)}
